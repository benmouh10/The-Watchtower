"""Threat Lens enforcement on the auth path (not only the edge middleware).

The middleware is exercised in ``test_threat_middleware.py``; these tests make
sure the second, auth-level guard is actually wired into ``get_current_user``
and ``get_current_user_optional`` — a blocked identity must be refused even
when a request reaches the dependency without passing ThreatLensMiddleware.
"""
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import Depends, FastAPI
from jose import jwt

import app.controllers.auth_controller as auth
from app.controllers.auth_controller import get_current_user, get_current_user_optional
from app.core.config import settings
from app.entities.models import UserRole
from app.services import threat_lens as tl

from tests._threat_fakes import threat_cache  # noqa: F401 (fixture re-export)

_BLOCKED_IP = "203.0.113.66"


def _token() -> str:
    return jwt.encode(
        {"sub": str(uuid.uuid4())},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/me")
    async def me(user=Depends(get_current_user)):
        return {"id": str(user.id)}

    @app.get("/maybe")
    async def maybe(user=Depends(get_current_user_optional)):
        return {"id": str(user.id) if user else None}

    return app


async def _get(path: str) -> httpx.Response:
    app = _make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, headers=_headers())


@pytest.fixture(autouse=True)
def _enable_threat(monkeypatch):
    monkeypatch.setattr(settings, "THREAT_LENS_ENABLED", True)
    monkeypatch.setattr(settings, "THREAT_LENS_SKIP_PRIVATE_PEERS", True)
    monkeypatch.setattr(tl, "client_identity", lambda request: _BLOCKED_IP)

    # get_current_user reads the user from the repository; give it one so the
    # test is about the block check, not the database.
    class _FakeUserRepository:
        def __init__(self, db):
            pass

        async def find_by_id(self, user_id):
            return SimpleNamespace(
                id=user_id, role=UserRole.AUTH_USER, is_blocked=False
            )

    monkeypatch.setattr(auth, "UserRepository", _FakeUserRepository)


def _mark_blocked(fake_redis) -> None:
    fake_redis.store[
        f"{settings.THREAT_LENS_BLOCKLIST_KEY_PREFIX}:{_BLOCKED_IP}"
    ] = "1"


class TestAuthPathEnforcement:
    @pytest.mark.asyncio
    async def test_blocked_identity_is_refused_on_authenticated_route(
        self, threat_cache
    ):
        _mark_blocked(threat_cache)
        response = await _get("/me")
        assert response.status_code == 403
        assert "temporarily blocked" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_blocked_identity_is_refused_on_guest_route(self, threat_cache):
        # get_current_user_optional swallows auth errors, so this proves the
        # Threat Lens dependency runs *before* that try/except and still 403s.
        _mark_blocked(threat_cache)
        response = await _get("/maybe")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_healthy_identity_passes(self, threat_cache):
        response = await _get("/me")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_disabled_flag_is_inert(self, threat_cache, monkeypatch):
        monkeypatch.setattr(settings, "THREAT_LENS_ENABLED", False)
        _mark_blocked(threat_cache)
        assert (await _get("/me")).status_code == 200

    @pytest.mark.asyncio
    async def test_private_peer_is_never_refused(self, threat_cache, monkeypatch):
        monkeypatch.setattr(tl, "client_identity", lambda request: "127.0.0.1")
        threat_cache.store[
            f"{settings.THREAT_LENS_BLOCKLIST_KEY_PREFIX}:127.0.0.1"
        ] = "1"
        assert (await _get("/me")).status_code == 200

    @pytest.mark.asyncio
    async def test_cache_error_fails_open(self, threat_cache, monkeypatch):
        async def _boom(identity):
            raise RuntimeError("redis down")

        monkeypatch.setattr(tl, "is_identity_blocked", _boom)
        assert (await _get("/me")).status_code == 200
