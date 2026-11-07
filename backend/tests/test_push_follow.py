"""Roadmap item 8: followed-event Web Push notifications (EventFollow consumed)."""
import uuid

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

from sqlalchemy import select, update

from src.core.database import AsyncSessionFactory
from src.data.entities.models import Event, PushDelivery, User
from src.services.push_service import LOGGED, PushService
from src.services.webpush_sender import build_payload_for_event


async def _make_event(title: str = "Follow push test event") -> uuid.UUID:
    async with AsyncSessionFactory() as db:
        evt = Event(title=title, summary="A summarized development.")
        db.add(evt)
        await db.commit()
        return evt.id


async def _register_sub(client, headers, endpoint=None):
    resp = await client.post(
        "/api/v1/push/subscriptions",
        json={
            "endpoint": endpoint or f"https://push.example.org/sub/{uuid.uuid4().hex}",
            "p256dh": "x".join(["B", "="] * 10),
            "auth": "auth-secret-here",
        },
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _count_event_deliveries(event_id):
    async with AsyncSessionFactory() as db:
        rows = (
            await db.execute(
                select(PushDelivery).where(PushDelivery.event_id == event_id)
            )
        ).scalars().all()
        return rows


async def test_follower_device_receives_logged_event_push(client, auth):
    identity = await auth()
    event_id = await _make_event()
    headers = identity["headers"]

    assert (await client.post(f"/api/v1/events/{event_id}/follow", headers=headers)).status_code == 200
    await _register_sub(client, headers)

    async with AsyncSessionFactory() as db:
        evt = await db.get(Event, event_id)
        created = await PushService(db).enqueue_follow_event(evt)
        assert created == 1

    deliveries = await _count_event_deliveries(event_id)
    assert len(deliveries) == 1
    assert deliveries[0].digest_id is None
    assert deliveries[0].delivery_status == "pending"

    async with AsyncSessionFactory() as db:
        counts = await PushService(db).flush_pending_deliveries()
    assert counts[LOGGED] == 1

    deliveries = await _count_event_deliveries(event_id)
    assert deliveries[0].delivery_status == LOGGED
    assert deliveries[0].sent_at is not None


async def test_enqueue_is_idempotent_per_device(client, auth):
    identity = await auth()
    event_id = await _make_event()
    headers = identity["headers"]

    await client.post(f"/api/v1/events/{event_id}/follow", headers=headers)
    await _register_sub(client, headers)

    async with AsyncSessionFactory() as db:
        evt = await db.get(Event, event_id)
        service = PushService(db)
        assert await service.enqueue_follow_event(evt) == 1
        assert await service.enqueue_follow_event(evt) == 0

    assert len(await _count_event_deliveries(event_id)) == 1


async def test_non_follower_gets_nothing(client, auth):
    identity = await auth()
    event_id = await _make_event()
    await _register_sub(client, identity["headers"])

    async with AsyncSessionFactory() as db:
        evt = await db.get(Event, event_id)
        created = await PushService(db).enqueue_follow_event(evt)
    assert created == 0
    assert await _count_event_deliveries(event_id) == []


async def test_unfollowed_device_still_queued_if_user_follows_elsewhere(client, auth):
    identity = await auth()
    event_a = await _make_event(title="A")
    event_b = await _make_event(title="B")
    headers = identity["headers"]

    await client.post(f"/api/v1/events/{event_a}/follow", headers=headers)
    await _register_sub(client, headers)

    async with AsyncSessionFactory() as db:
        evt_b = await db.get(Event, event_b)
        assert await PushService(db).enqueue_follow_event(evt_b) == 0


async def test_event_payload_builder():
    payload = build_payload_for_event(
        title="Economy shocks markets",
        summary="A summarized development but not long enough to prove truncation.",
        event_id="abc-123",
        base_url="https://globelens.example",
    )
    assert payload["title"] == "GlobeLens AI: Economy shocks markets"
    assert "truncation" in payload["body"]
    assert payload["tag"] == "event-abc-123"
    assert payload["data"] == {"url": "https://globelens.example/events/abc-123"}


async def test_admin_flush_drains_follow_ups(client, auth):
    identity = await auth()
    event_id = await _make_event()
    headers = identity["headers"]
    await client.post(f"/api/v1/events/{event_id}/follow", headers=headers)
    await _register_sub(client, headers)

    async with AsyncSessionFactory() as db:
        evt = await db.get(Event, event_id)
        await PushService(db).enqueue_follow_event(evt)

    adm = await auth()
    async with AsyncSessionFactory() as db:
        await db.execute(update(User).where(User.id == adm["user_id"]).values(role="ADMIN"))
        await db.commit()

    resp = await client.post(
        "/api/v1/admin/push/deliver",
        headers=adm["headers"],
    )
    assert resp.status_code == 200
    assert resp.json()["logged"] >= 1