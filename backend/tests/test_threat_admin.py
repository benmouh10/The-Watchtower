"""Hermetic tests for the Threat Lens admin ops (list / manual-block / unblock)."""
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.controllers import admin_controller
from app.controllers.admin_controller import require_admin, router as admin_router
from app.core.config import settings
from app.core.database import get_db
from app.services import threat_lens as tl

from tests._threat_fakes import threat_cache  # noqa: F401 (fixture re-export)

_PREFIX = "/api/v1/admin"


def _row(ip, source="THREAT_LENS", reason="anomalous_x2_risk_0.99", risk=0.991,
         blocked_at=None, expires_at=None):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        ip=ip,
        source=source,
        reason=reason,
        risk_score=risk,
        blocked_at=blocked_at or now,
        expires_at=expires_at or now,
    )


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows
        self.closed = False

    async def execute(self, stmt):
        return _FakeResult(self._rows)

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def close(self):
        self.closed = True


@pytest.fixture
def api(threat_cache, monkeypatch):
    """FastAPI app with the real admin router; admin + db dependencies faked."""
    app = FastAPI()
    app.include_router(admin_router, prefix=_PREFIX)

    async def _fake_admin():
        return SimpleNamespace(id=None, name="admin", role="ADMIN")

    rows = []

    async def _fake_get_db():
        db = _FakeDb(rows)
        try:
            yield db
        finally:
            await db.close()

    app.dependency_overrides[require_admin] = _fake_admin
    app.dependency_overrides[get_db] = _fake_get_db
    return app, rows


async def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestListBlocks:
    @pytest.mark.asyncio
    async def test_active_flag_reflects_redis_marker(self, api, threat_cache):
        app, rows = api
        row_a = _row("203.0.113.10")
        row_b = _row("203.0.113.11")
        rows.extend([row_a, row_b])
        threat_cache.store[f"{settings.THREAT_LENS_BLOCKLIST_KEY_PREFIX}:203.0.113.11"] = "1"

        async with await _client(app) as client:
            response = await client.get(f"{_PREFIX}/threat/blocks")
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 2
        by_ip = {b["ip"]: b for b in body["blocks"]}
        assert by_ip["203.0.113.10"]["active"] is False
        assert by_ip["203.0.113.11"]["active"] is True
        assert by_ip["203.0.113.10"]["source"] == "THREAT_LENS"
        assert by_ip["203.0.113.10"]["risk_score"] == 0.991
        assert body["recent_feed"] == []

    @pytest.mark.asyncio
    async def test_recent_feed_is_included(self, api, threat_cache):
        app, rows = api
        rows.append(_row("203.0.113.12"))
        threat_cache.store[settings.THREAT_LENS_BLOCKS_FEED_KEY] = [
            "1791373803|203.0.113.12|anomalous_x2_risk_0.99",
            "1791373802|203.0.113.9|manual_block_by_admin",
        ]
        async with await _client(app) as client:
            body = (await client.get(f"{_PREFIX}/threat/blocks")).json()
        assert [e["ip"] for e in body["recent_feed"]] == ["203.0.113.12", "203.0.113.9"]

    @pytest.mark.asyncio
    async def test_redis_down_still_lists_rows(self, api, monkeypatch):
        app, rows = api
        rows.append(_row("203.0.113.13"))

        from app.services.cache_service import cache_service

        async def _boom():
            raise RuntimeError("redis down")

        monkeypatch.setattr(cache_service, "_get_client", _boom)
        async with await _client(app) as client:
            body = (await client.get(f"{_PREFIX}/threat/blocks")).json()
        assert body["count"] == 1
        assert body["blocks"][0]["active"] is False

    @pytest.mark.asyncio
    async def test_empty_list(self, api):
        app, rows = api
        async with await _client(app) as client:
            body = (await client.get(f"{_PREFIX}/threat/blocks")).json()
        assert body["count"] == 0
        assert body["blocks"] == []


class TestManualBlock:
    @pytest.mark.asyncio
    async def test_manual_block_writes_marker_feed_and_row(self, api, threat_cache, monkeypatch):
        app, rows = api
        persisted = {}

        async def _fake_persist(identity, reason, risk, source="THREAT_LENS"):
            persisted.update(identity=identity, reason=reason, risk=risk, source=source)

        monkeypatch.setattr(tl, "persist_block", _fake_persist)
        async with await _client(app) as client:
            response = await client.post(
                f"{_PREFIX}/threat/blocks",
                json={"ip": "203.0.113.99", "reason": "credential stuffing"},
            )
        assert response.status_code == 201
        assert response.json()["source"] == "MANUAL"
        assert threat_cache.store[f"{settings.THREAT_LENS_BLOCKLIST_KEY_PREFIX}:203.0.113.99"] == "1"
        assert threat_cache.store[settings.THREAT_LENS_BLOCKS_FEED_KEY]
        assert persisted["identity"] == "203.0.113.99"
        assert persisted["reason"] == "credential stuffing"
        assert persisted["source"] == "MANUAL"
        assert persisted["risk"] == 1.0

    @pytest.mark.asyncio
    async def test_manual_block_defaults_reason(self, api, threat_cache, monkeypatch):
        app, rows = api
        persisted = {}

        async def _fake_persist(identity, reason, risk, source="THREAT_LENS"):
            persisted["reason"] = reason

        monkeypatch.setattr(tl, "persist_block", _fake_persist)
        async with await _client(app) as client:
            response = await client.post(f"{_PREFIX}/threat/blocks", json={"ip": "203.0.113.98"})
        assert response.status_code == 201
        assert persisted["reason"] == "manual_block_by_admin"

    @pytest.mark.asyncio
    async def test_manual_block_rejects_bogus_ip(self, api):
        app, rows = api
        async with await _client(app) as client:
            response = await client.post(
                f"{_PREFIX}/threat/blocks", json={"ip": "not-an-ip"}
            )
        assert response.status_code == 400


class TestUnblock:
    @pytest.mark.asyncio
    async def test_unblock_clears_marker_and_record(self, api, threat_cache, monkeypatch):
        app, rows = api
        rows.append(_row("203.0.113.5"))
        threat_cache.store[f"{settings.THREAT_LENS_BLOCKLIST_KEY_PREFIX}:203.0.113.5"] = "1"
        threat_cache.store["threat:window:203.0.113.5"] = ["1", "1"]
        deleted = []

        async def _fake_delete(identity):
            deleted.append(identity)
            return True

        monkeypatch.setattr(tl, "delete_blocked_ip", _fake_delete)
        async with await _client(app) as client:
            response = await client.post(f"{_PREFIX}/threat/blocks/203.0.113.5/unblock")
        assert response.status_code == 200
        body = response.json()
        assert body["cleared_enforcement"] is True
        assert body["cleared_record"] is True
        assert f"{settings.THREAT_LENS_BLOCKLIST_KEY_PREFIX}:203.0.113.5" not in threat_cache.store
        assert "threat:window:203.0.113.5" not in threat_cache.store
        assert deleted == ["203.0.113.5"]

    @pytest.mark.asyncio
    async def test_unblock_404_when_nothing_exists(self, api):
        app, rows = api
        async with await _client(app) as client:
            response = await client.post(f"{_PREFIX}/threat/blocks/203.0.113.6/unblock")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_unblock_rejects_bogus_ip(self, api):
        app, rows = api
        async with await _client(app) as client:
            response = await client.post(f"{_PREFIX}/threat/blocks/999.1.1.1/unblock")
        assert response.status_code == 400


class TestGuard:
    @pytest.mark.asyncio
    async def test_routes_carry_admin_rbac_tag(self, threat_cache):
        """Policy test relies on __rbac_min__ to classify routes; check tag here."""
        assert getattr(require_admin, "__rbac_min__", None) is not None
        paths = {
            getattr(route, "path", "") for route in admin_router.routes
            if "threat" in getattr(route, "path", "")
        }
        assert "/threat/blocks" in paths
        assert "/threat/blocks/{ip}/unblock" in paths