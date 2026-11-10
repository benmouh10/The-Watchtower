"""Event-view analytics: recording + per-user and global rollups.

POST /events/{id}/view appends a reading-habit row; the per-user summary
derives totals, daily series, topic mix and bias-lean mix from those rows; the
admin aggregate spans all users.
"""

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

BASE = datetime.now(timezone.utc) - timedelta(hours=1)


async def _make_event(db, *, topic="TECH", bias=None):
    from src.data.entities.models import Event

    event = Event(title="Analytics test event", topic=topic, status="PROCESSED")
    if bias is not None:
        event.bias_lean = bias
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


async def test_record_view_then_summary(client, auth):
    from src.core.database import AsyncSessionFactory
    from src.services.analytics_service import AnalyticsService
    from src.data.entities.models import BiasLean

    identity = await auth()
    async with AsyncSessionFactory() as db:
        event = await _make_event(db, topic="WORLD", bias=BiasLean.LEFT)
        service = AnalyticsService(db)
        view = await service.record_view(
            identity["user_id"], event.id, dwell_seconds=42, seen_at=BASE
        )
        assert view.id is not None
        summary = await service.summary_for_user(identity["user_id"])
        assert summary["total_views"] == 1
        assert summary["avg_dwell_seconds"] == 42.0
        assert summary["by_topic"] == {"WORLD": 1}
        assert summary["by_bias_lean"] == {"LEFT": 1}
        assert summary["daily"][0]["views"] == 1

        views = await service.views_for_user(identity["user_id"])
        assert len(views) == 1
        assert views[0]["event_id"] == str(event.id)
        assert views[0]["dwell_seconds"] == 42


async def test_view_endpoint_records(client, auth):
    from sqlalchemy import select

    from src.core.database import AsyncSessionFactory
    from src.data.entities.models import EventView

    identity = await auth()
    async with AsyncSessionFactory() as db:
        event = await _make_event(db)
    resp = await client.post(
        f"/api/v1/events/{event.id}/view",
        params={"dwell_seconds": 15},
        headers=identity["headers"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["recorded"] is True

    async with AsyncSessionFactory() as db:
        rows = (await db.execute(
            select(EventView).where(
                EventView.user_id == identity["user_id"],
                EventView.event_id == event.id,
            )
        )).scalars().all()
        assert len(rows) == 1
        assert rows[0].dwell_seconds == 15


async def test_window_filtering(client, auth):
    from src.core.database import AsyncSessionFactory
    from src.services.analytics_service import AnalyticsService

    identity = await auth()
    async with AsyncSessionFactory() as db:
        event_a = await _make_event(db, topic="TECH")
        event_b = await _make_event(db, topic="ECON")
        service = AnalyticsService(db)
        await service.record_view(identity["user_id"], event_a.id, seen_at=BASE)
        await service.record_view(
            identity["user_id"],
            event_b.id,
            seen_at=BASE - timedelta(days=60),
        )
        recent = await service.summary_for_user(identity["user_id"])
        assert recent["total_views"] == 1
        assert recent["by_topic"] == {"TECH": 1}


async def test_global_summary_admin_only(client, auth):
    from src.core.database import AsyncSessionFactory
    from src.services.analytics_service import AnalyticsService

    identity = await auth()
    async with AsyncSessionFactory() as db:
        event = await _make_event(db)
        await AnalyticsService(db).record_view(identity["user_id"], event.id, seen_at=BASE)

    forbidden = await client.get("/api/v1/analytics/admin/views", headers=identity["headers"])
    assert forbidden.status_code == 403

    from sqlalchemy import update

    from src.data.entities.models import User

    async with AsyncSessionFactory() as db:
        await db.execute(update(User).where(User.id == identity["user_id"]).values(role="ADMIN"))
        await db.commit()

    allowed = await client.get("/api/v1/analytics/admin/views", headers=identity["headers"])
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["total_views"] == 1
    assert body["active_readers"] == 1