"""Hermetic tests for the Threat Lens middleware (block check + telemetry)."""
import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.core.config import settings
from app.middleware.threat_lens import ThreatLensMiddleware
from app.services import threat_lens as tl

from tests._threat_fakes import threat_cache  # noqa: F401 (fixture re-export)

_BLOCK_KEY = f"{settings.THREAT_LENS_BLOCKLIST_KEY_PREFIX}:127.0.0.1"


def _make_app() -> Starlette:
    async def ok(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/ping", ok), Route("/health", ok)])
    app.add_middleware(ThreatLensMiddleware)
    return app


def _client(app: Starlette) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.fixture
def telemetry_recorder(monkeypatch):
    calls = []

    async def _enqueue(event):
        calls.append(event)

    monkeypatch.setattr(tl, "enqueue_telemetry", _enqueue)
    return calls


@pytest.fixture(autouse=True)
def _enable_threat(monkeypatch):
    monkeypatch.setattr(settings, "THREAT_LENS_ENABLED", True)


class TestMiddlewareBehaviour:
    @pytest.mark.asyncio
    async def test_disabled_middleware_is_inert(self, monkeypatch, telemetry_recorder):
        monkeypatch.setattr(settings, "THREAT_LENS_ENABLED", False)
        async with _client(_make_app()) as client:
            for _ in range(3):
                assert (await client.get("/ping")).status_code == 200
        assert telemetry_recorder == []

    @pytest.mark.asyncio
    async def test_healthy_request_passes_and_enqueues_telemetry(self, threat_cache, telemetry_recorder):
        async with _client(_make_app()) as client:
            response = await client.get("/ping")
        assert response.status_code == 200
        assert len(telemetry_recorder) == 1
        event = telemetry_recorder[0]
        assert event.method == "GET"
        assert event.path == "/ping"
        assert event.ip == "127.0.0.1"  # httpx ASGITransport's default client host

    @pytest.mark.asyncio
    async def test_blocked_identity_gets_403_without_telemetry(self, threat_cache, telemetry_recorder):
        threat_cache.store[_BLOCK_KEY] = "1"
        async with _client(_make_app()) as client:
            response = await client.get("/ping")
        assert response.status_code == 403
        assert telemetry_recorder == []

    @pytest.mark.asyncio
    async def test_exempt_path_is_not_telemetried(self, threat_cache, telemetry_recorder):
        async with _client(_make_app()) as client:
            assert (await client.get("/health")).status_code == 200
        assert telemetry_recorder == []

    @pytest.mark.asyncio
    async def test_options_request_is_never_telemetried(self, threat_cache, telemetry_recorder):
        async with _client(_make_app()) as client:
            response = await client.request("OPTIONS", "/ping")
        assert response.status_code in (200, 405)
        assert telemetry_recorder == []

    @pytest.mark.asyncio
    async def test_bytes_status_fills_telemetry(self, threat_cache, telemetry_recorder):
        async def _boom(request):
            return PlainTextResponse("err", status_code=404)

        app = Starlette(routes=[Route("/missing", _boom)])
        app.add_middleware(ThreatLensMiddleware)
        async with _client(app) as client:
            assert (await client.get("/missing")).status_code == 404
        assert len(telemetry_recorder) == 1
        assert telemetry_recorder[0].status == 404


class TestFailOpen:
    @pytest.mark.asyncio
    async def test_cache_down_never_blocks(self, monkeypatch, telemetry_recorder):
        async def _boom():
            raise RuntimeError("redis down")

        monkeypatch.setattr(tl, "is_identity_blocked", _boom)
        async with _client(_make_app()) as client:
            assert (await client.get("/ping")).status_code == 200

    @pytest.mark.asyncio
    async def test_enqueue_failure_never_fails_the_request(self, monkeypatch, threat_cache):
        async def _boom(event):
            raise RuntimeError("broker down")

        monkeypatch.setattr(tl, "enqueue_telemetry", _boom)
        async with _client(_make_app()) as client:
            assert (await client.get("/ping")).status_code == 200