"""End-to-End integration test for Threat Lens (Sprint 1 validation).

Validates the complete loop:
1. THREAT_LENS_ENABLED=true
2. Normal traffic receives 200, enqueues telemetry, worker scores with low risk (<0.90), no block.
3. Synthetic attack traffic trips threshold (risk >= 0.90).
4. Windowed hit count reaches threshold (>=2) -> Redis block marker set + blocked_ips row persisted.
5. Next request from attacker IP receives 403 Forbidden edge drop immediately.
6. Diagnostic /score endpoint returns calibrated risk reflecting the attack payload.
"""
import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.core.config import settings
from app.entities.models import BlockedIp
from app.middleware.threat_lens import ThreatLensMiddleware
from app.services import threat_lens as tl
from app.services.cache_service import cache_service
from app.worker.threat_tasks import analyze_telemetry_task
import app.worker.threat_tasks as threat_tasks
import app.scoring_api as scoring_api
from shared_core.model import AnomalyDetector
from shared_core.training import build_synthetic_traffic

from tests._threat_fakes import FakeRedis


@pytest.fixture
def integrated_environment(tmp_path, monkeypatch):
    """Setup fake Redis, trained model artifact, and in-memory DB session."""
    # 1. Enable Threat Lens & skip private peers False for TEST-NET-3 testing
    monkeypatch.setattr(settings, "THREAT_LENS_ENABLED", True)
    monkeypatch.setattr(settings, "THREAT_LENS_SKIP_PRIVATE_PEERS", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_TRUST_FORWARDED", True)

    # 2. Redis fake
    fake_redis = FakeRedis()

    async def _get_client():
        return fake_redis

    monkeypatch.setattr(cache_service, "_get_client", _get_client)

    # 3. Train and persist real model
    normal, _ = build_synthetic_traffic(n_normal=200, n_attack=100, seed=7)
    model_path = tmp_path / "threat_lens.joblib"
    AnomalyDetector(str(model_path), seed=7).train(normal).persist()
    monkeypatch.setattr(settings, "THREAT_LENS_MODEL_PATH", str(model_path))
    monkeypatch.setattr(threat_tasks, "_detector", None)
    monkeypatch.setattr(scoring_api, "_detector", None)

    # 4. In-memory DB recording for persist_block
    persisted_rows = {}

    async def _fake_persist_block(identity, reason, risk, source="THREAT_LENS"):
        persisted_rows[identity] = BlockedIp(
            ip=identity,
            reason=reason,
            risk=risk,
            blocked_by="auto" if source == "THREAT_LENS" else "admin",
        )

    monkeypatch.setattr(tl, "persist_block", _fake_persist_block)
    return fake_redis, persisted_rows, str(model_path)


def _build_app(enqueued_sink: list) -> Starlette:
    async def login_route(request):
        return JSONResponse({"status": "ok"}, status_code=200)

    async def article_route(request):
        return JSONResponse({"title": "Sample Article"}, status_code=200)

    app = Starlette(
        routes=[
            Route("/api/v1/auth/login", login_route, methods=["POST"]),
            Route("/api/v1/articles/1", article_route, methods=["GET"]),
        ]
    )
    app.add_middleware(ThreatLensMiddleware)
    return app


@pytest.mark.asyncio
async def test_threat_lens_e2e_normal_then_attack_pipeline(integrated_environment, monkeypatch):
    fake_redis, persisted_rows, model_path = integrated_environment
    enqueued_events = []

    # Wire telemetry enqueue directly to the worker task so the pipeline runs inline
    async def _enqueue_sync(event):
        enqueued_events.append(event)
        # Worker scores telemetry event
        analyze_telemetry_task(event.to_dict())

    monkeypatch.setattr(tl, "enqueue_telemetry", _enqueue_sync)

    app = _build_app(enqueued_events)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )

    normal_ip = "198.51.100.22"
    attack_ip = "203.0.113.88"

    async with client as c:
        # ── 1. Fire normal traffic ──────────────────────────────────────────
        for i in range(4):
            res = await c.get(
                "/api/v1/articles/1",
                headers={
                    "x-forwarded-for": normal_ip,
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0",
                    "authorization": "Bearer token123",
                },
            )
            assert res.status_code == 200

        # Normal client is NOT blocked in Redis or DB
        assert f"{settings.THREAT_LENS_BLOCKLIST_KEY_PREFIX}:{normal_ip}" not in fake_redis.store
        assert normal_ip not in persisted_rows

        # ── 2. Fire synthetic attack traffic ────────────────────────────────
        # Credential stuffing pattern: POST /api/v1/auth/login, missing UA, no Bearer auth
        attack_headers = {
            "x-forwarded-for": attack_ip,
            "content-type": "application/json",
        }
        attack_payload = {"user": "admin", "password": "x" * 2000}

        # Attack request 1: trips anomaly, hits=1 (threshold is 2, not blocked yet)
        res1 = await c.post("/api/v1/auth/login", headers=attack_headers, json=attack_payload)
        assert res1.status_code == 200
        assert f"{settings.THREAT_LENS_BLOCKLIST_KEY_PREFIX}:{attack_ip}" not in fake_redis.store

        # Attack request 2: trips anomaly, hits=2 (window threshold reached -> BLOCKED!)
        res2 = await c.post("/api/v1/auth/login", headers=attack_headers, json=attack_payload)
        assert res2.status_code == 200

        # Verify Redis marker and DB row exist immediately
        assert fake_redis.store.get(f"{settings.THREAT_LENS_BLOCKLIST_KEY_PREFIX}:{attack_ip}") == "1"
        assert attack_ip in persisted_rows
        blocked_row = persisted_rows[attack_ip]
        assert blocked_row.ip == attack_ip
        assert blocked_row.risk >= settings.THREAT_LENS_RISK_THRESHOLD
        assert blocked_row.blocked_by == "auto"

        # ── 3. Subsequent request from attack IP is dropped with 403 ─────────
        res3 = await c.post("/api/v1/auth/login", headers=attack_headers, json=attack_payload)
        assert res3.status_code == 403
        assert "temporarily blocked" in res3.json()["detail"]

        # Normal client can still access routes without interruption
        res_normal = await c.get(
            "/api/v1/articles/1",
            headers={"x-forwarded-for": normal_ip, "user-agent": "Mozilla/5.0"},
        )
        assert res_normal.status_code == 200

    # ── 4. Verify /score returns calibrated risk for attack event ───────────
    scoring_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=scoring_api.app), base_url="http://test:9000"
    )
    async with scoring_client as sc:
        # Verify /score via GET
        score_get_res = await sc.get(
            f"/score?ip={attack_ip}&method=POST&path=/api/v1/auth/login&payload_bytes=500000"
        )
        assert score_get_res.status_code == 200
        score_data = score_get_res.json()
        assert score_data["identity"] == attack_ip
        assert score_data["risk"] >= 0.85

        # Verify /score via POST with the attack telemetry
        attack_event = enqueued_events[5]  # attack request 2
        score_post_res = await sc.post(
            "/score",
            json={"telemetry": attack_event.to_dict()},
        )
        assert score_post_res.status_code == 200
        assert score_post_res.json()["risk"] >= settings.THREAT_LENS_RISK_THRESHOLD
        assert score_post_res.json()["violates"] is True

        # Verify /score via POST with normal telemetry
        normal_event = enqueued_events[0]
        score_normal_res = await sc.post(
            "/score",
            json={"telemetry": normal_event.to_dict()},
        )
        assert score_normal_res.status_code == 200
        assert score_normal_res.json()["risk"] < settings.THREAT_LENS_RISK_THRESHOLD
        assert score_normal_res.json()["violates"] is False
