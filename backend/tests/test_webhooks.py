"""Outbound webhook registration, queueing, signing, and delivery.

A PROCESSED event is fanned out to every active endpoint that asked for its
event type (empty event_types = subscribe to everything). Queueing is
idempotent on (webhook_id, event_id). Delivery signs the raw payload with
HMAC-SHA256; with WEBHOOK_ENABLED off it is recorded 'logged' exactly like
email/push log mode.
"""

import uuid

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _register(client, headers, event_types=None, url=None):
    resp = await client.post(
        "/api/v1/webhooks",
        json={
            "url": url or f"https://hooks.example.org/{uuid.uuid4().hex}",
            "event_types": event_types,
        },
        headers=headers,
    )
    return resp


async def _make_event(db):
    from src.data.entities.models import Event

    event = Event(title="Test event for webhooks", summary="summary", status="PROCESSED")
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


async def test_register_list_delete_webhook(client, auth):
    identity = await auth()
    headers = identity["headers"]
    resp = await _register(client, headers, event_types=["event.created"])
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["secret"]
    assert body["event_types"] == ["event.created"]

    listing = await client.get("/api/v1/webhooks", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["count"] == 1

    r = await client.delete(f"/api/v1/webhooks/{body['id']}", headers=headers)
    assert r.status_code == 200
    asserts = r.json()
    assert asserts["deactivated"] is True
    assert asserts["is_active"] is False


async def test_empty_event_types_subscribes_to_everything(client, auth):
    identity = await auth()
    resp = await _register(client, identity["headers"], event_types=[])
    assert resp.status_code == 201, resp.text
    assert resp.json()["event_types"] == []


async def test_unknown_event_type_is_refused(client, auth):
    identity = await auth()
    resp = await _register(client, identity["headers"], event_types=["market.gap"])
    assert resp.status_code == 422
    assert "Unsupported event type" in resp.json()["detail"]


async def test_endpoint_cap_is_enforced(client, auth):
    from src.core.config import settings

    identity = await auth()
    headers = identity["headers"]
    for _ in range(settings.WEBHOOK_MAX_ENDPOINTS):
        resp = await _register(client, headers)
        assert resp.status_code == 201, resp.text
    over = await _register(client, headers)
    assert over.status_code == 422
    assert "Endpoint cap reached" in over.json()["detail"]


async def test_enqueue_fires_matching_hooks_and_is_idempotent(client, auth):
    from src.core.database import AsyncSessionFactory
    from src.services.webhook_service import WebhookService

    identity = await auth()
    headers = identity["headers"]
    matching = (await _register(client, headers, event_types=["event.created"])).json()
    _skip = (await _register(client, headers, event_types=["event.changed"])).json()
    _all = (await _register(client, headers, event_types=[])).json()

    async with AsyncSessionFactory() as db:
        event = await _make_event(db)
        service = WebhookService(db)
        created = await service.queue_for_event(event)
        assert created == 2
        again = await service.queue_for_event(event)
        assert again == 0


async def test_log_mode_flush_records_deliveries(client, auth):
    from src.core.database import AsyncSessionFactory
    from src.services.webhook_service import LOGGED, WebhookService

    identity = await auth()
    _reg = (await _register(client, identity["headers"])).json()
    async with AsyncSessionFactory() as db:
        event = await _make_event(db)
        service = WebhookService(db)
        await service.queue_for_event(event)
        counts = await service.flush_pending_deliveries()
        assert counts[LOGGED] == 1


async def test_live_mode_posts_signed_payload(monkeypatch, client, auth):
    import httpx

    from src.core.config import settings
    from src.core.database import AsyncSessionFactory
    from src.services.webhook_service import SENT, WebhookService, build_signature

    class FakeResponse:
        status_code = 200
        text = "ok"

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, content=None, headers=None):
            self.calls.append({"url": url, "content": content, "headers": headers or {}})
            return FakeResponse()

    fake = FakeAsyncClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: fake)
    monkeypatch.setattr(settings, "WEBHOOK_ENABLED", True)

    identity = await auth()
    reg = (await _register(client, identity["headers"])).json()
    async with AsyncSessionFactory() as db:
        event = await _make_event(db)
        service = WebhookService(db)
        await service.queue_for_event(event)
        counts = await service.flush_pending_deliveries()
        assert counts[SENT] == 1
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["url"] == reg["url"]
        assert call["headers"]["X-GlobeLens-Event"] == "event.created"
        computed = build_signature(reg["secret"], call["content"])
        assert call["headers"]["X-GlobeLens-Signature"] == computed


async def test_failed_delivery_retries_then_fails(monkeypatch, client, auth):
    import httpx
    from datetime import datetime, timedelta, timezone

    from src.core.config import settings
    from src.core.database import AsyncSessionFactory
    from src.services.webhook_service import FAILED, PENDING, WebhookService

    class ExplodingResponse:
        status_code = 500
        text = "boom"

    class ExplodingAsyncClient:
        def __init__(self, timeout=None):
            self.sent = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, content=None, headers=None):
            self.sent += 1
            return ExplodingResponse()

    fake = ExplodingAsyncClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: fake)
    monkeypatch.setattr(settings, "WEBHOOK_ENABLED", True)

    identity = await auth()
    await _register(client, identity["headers"])
    base = datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc)
    async with AsyncSessionFactory() as db:
        event = await _make_event(db)
        service = WebhookService(db)
        await service.queue_for_event(event, now=base)
        now = base
        for _ in range(settings.WEBHOOK_DELIVERY_MAX_ATTEMPTS - 1):
            counts = await service.flush_pending_deliveries(now=now + timedelta(seconds=1))
            assert counts[FAILED] == 0
            from sqlalchemy import select

            from src.data.entities.models import WebhookDelivery

            row = (await db.execute(select(WebhookDelivery))).scalars().first()
            assert row.status == PENDING
            assert row.attempts == fake.sent
            now = now + timedelta(seconds=60 * (2 ** (row.attempts - 1)))
        final = await service.flush_pending_deliveries(now=now + timedelta(seconds=1))
        assert final[FAILED] == 1


async def test_admin_drain_endpoint(client, auth):
    from src.core.database import AsyncSessionFactory
    from src.services.webhook_service import LOGGED, WebhookService

    identity = await auth()
    await _register(client, identity["headers"])
    async with AsyncSessionFactory() as db:
        event = await _make_event(db)
        await WebhookService(db).queue_for_event(event)

    resp = await client.post(
        "/api/v1/admin/webhooks/deliver",
        headers=identity["headers"],
    )
    assert resp.status_code == 403

    from sqlalchemy import update

    from src.data.entities.models import User

    async with AsyncSessionFactory() as db:
        await db.execute(update(User).where(User.id == identity["user_id"]).values(role="ADMIN"))
        await db.commit()
    r = await client.post(
        "/api/v1/admin/webhooks/deliver",
        headers=identity["headers"],
    )
    assert r.status_code == 200
    assert r.json()["logged"] >= 1