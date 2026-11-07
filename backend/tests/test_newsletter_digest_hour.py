"""Newsletter preferred_digest_hour scheduling.

The digest engine renders one brief per run, but each subscriber's email is
stamped with scheduled_for = the next occurrence of their preferred UTC hour.
The delivery queue only drains rows that are due, so a reader's brief actually
arrives at the hour they asked for instead of whenever the batch happened to
run.
"""

import uuid
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

FIXED_NOW = datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc)


def _to_utc(dt):
    return dt.astimezone(timezone.utc)


async def test_next_occurrence_same_day_later_hour():
    from src.services.newsletter_service import next_digest_occurrence

    result = next_digest_occurrence(FIXED_NOW, 20)
    assert _to_utc(result) == datetime(2026, 9, 30, 20, 0, 0, tzinfo=timezone.utc)


async def test_next_occurrence_past_hour_defers_to_tomorrow():
    from src.services.newsletter_service import next_digest_occurrence

    result = next_digest_occurrence(FIXED_NOW, 6)
    assert _to_utc(result) == datetime(2026, 10, 1, 6, 0, 0, tzinfo=timezone.utc)


async def test_next_occurrence_midnight_boundary():
    from src.services.newsletter_service import next_digest_occurrence

    at_midnight = datetime(2026, 9, 30, 23, 45, 0, tzinfo=timezone.utc)
    assert _to_utc(next_digest_occurrence(at_midnight, 0)) == datetime(
        2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc
    )


async def _subscribe_with_hour(client, auth, hour):
    identity = await auth()
    resp = await client.put(
        "/api/v1/newsletter/settings",
        json={"digest_frequency": "daily", "preferred_digest_hour": hour},
        headers=identity["headers"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["preferred_digest_hour"] == hour
    return identity


async def _make_digest(db):
    from src.data.entities.models import Digest

    digest = Digest(
        title="Comprehensive Brief - test",
        content_html="<h1>Brief</h1>",
        event_ids=[],
        event_count=0,
        frequency="daily",
    )
    db.add(digest)
    await db.commit()
    await db.refresh(digest)
    return digest


async def _enqueued_row(db, user_id):
    from sqlalchemy import select

    from src.data.entities.models import NewsletterDelivery

    result = await db.execute(
        select(NewsletterDelivery).where(NewsletterDelivery.user_id == user_id)
    )
    return result.scalars().first()


async def test_enqueue_stamps_preferred_delivery_hour(client, auth):
    from src.core.database import AsyncSessionFactory
    from src.services.newsletter_service import NewsletterService

    identity = await _subscribe_with_hour(client, auth, 20)
    async with AsyncSessionFactory() as db:
        digest = await _make_digest(db)
        created = await NewsletterService(db).enqueue_digest(digest, now=FIXED_NOW)
        assert created == 1
        row = await _enqueued_row(db, identity["user_id"])
        assert row is not None
        assert _to_utc(row.scheduled_for) == datetime(2026, 9, 30, 20, 0, 0, tzinfo=timezone.utc)


async def test_default_hour_is_six(client, auth):
    from src.core.database import AsyncSessionFactory
    from src.services.newsletter_service import NewsletterService

    identity = await auth()
    sub = await client.post("/api/v1/newsletter/subscribe", headers=identity["headers"])
    assert sub.status_code == 200, sub.text
    assert sub.json()["preferred_digest_hour"] == 6
    async with AsyncSessionFactory() as db:
        digest = await _make_digest(db)
        await NewsletterService(db).enqueue_digest(digest, now=FIXED_NOW)
        row = await _enqueued_row(db, identity["user_id"])
        assert _to_utc(row.scheduled_for) == datetime(2026, 10, 1, 6, 0, 0, tzinfo=timezone.utc)


async def test_different_hours_get_different_schedules(client, auth):
    from src.core.database import AsyncSessionFactory
    from src.services.newsletter_service import NewsletterService

    a = await _subscribe_with_hour(client, auth, 6)
    b = await _subscribe_with_hour(client, auth, 22)
    async with AsyncSessionFactory() as db:
        digest = await _make_digest(db)
        await NewsletterService(db).enqueue_digest(digest, now=FIXED_NOW)
        row_a = await _enqueued_row(db, a["user_id"])
        row_b = await _enqueued_row(db, b["user_id"])
        assert _to_utc(row_a.scheduled_for) == datetime(2026, 10, 1, 6, 0, 0, tzinfo=timezone.utc)
        assert _to_utc(row_b.scheduled_for) == datetime(2026, 9, 30, 22, 0, 0, tzinfo=timezone.utc)


async def test_pending_deliveries_wait_for_due_time(client, auth):
    from src.core.database import AsyncSessionFactory
    from src.services.newsletter_service import NewsletterService

    identity = await _subscribe_with_hour(client, auth, 20)
    async with AsyncSessionFactory() as db:
        digest = await _make_digest(db)
        service = NewsletterService(db)
        await service.enqueue_digest(digest, now=FIXED_NOW)
        before = await service.pending_deliveries(now=datetime(2026, 9, 30, 19, 0, 0, tzinfo=timezone.utc))
        assert before == []
        at_hour = await service.pending_deliveries(
            now=datetime(2026, 9, 30, 20, 0, 0, tzinfo=timezone.utc)
        )
        assert [tuple_row[0].user_id for tuple_row in at_hour] == [uuid.UUID(identity["user_id"])]


async def test_flush_does_not_send_future_deliveries(client, auth):
    from src.core.database import AsyncSessionFactory
    from src.services.newsletter_service import LOGGED, NewsletterService

    identity = await _subscribe_with_hour(client, auth, 20)
    async with AsyncSessionFactory() as db:
        digest = await _make_digest(db)
        service = NewsletterService(db)
        await service.enqueue_digest(digest, now=FIXED_NOW)
        early = await service.flush_pending_deliveries(
            now=datetime(2026, 9, 30, 19, 0, 0, tzinfo=timezone.utc)
        )
        assert early[LOGGED] == 0
        row = await _enqueued_row(db, identity["user_id"])
        assert row.delivery_status == "pending"

        late = await service.flush_pending_deliveries(
            now=datetime(2026, 9, 30, 20, 0, 0, tzinfo=timezone.utc)
        )
        assert late[LOGGED] == 1
