#!/usr/bin/env python3
"""
Sprint 1 End-to-End Validation Script: Threat Lens (Edge Abuse Defense)

Tests the complete Sprint 1 attack plan:
1. Normal traffic arrives at API Gateway -> 200 OK -> telemetry enqueued -> scored with low risk.
2. Synthetic attack traffic arrives -> Isolation Forest flags anomaly (risk >= 0.90).
3. >= 2 anomalies within rolling window -> threat:blocked:<ip> written to Redis + blocked_ips row inserted.
4. Next request from attack IP is dropped with 403 Forbidden at the gateway.
5. Internal diagnostic endpoint GET/POST /score returns calibrated risk and feature breakdown.

Usage:
    # Run full integrated in-process pipeline validation:
    python scripts/validate_threat_lens_e2e.py

    # Or run against live containers (when stack is up):
    python scripts/validate_threat_lens_e2e.py --live --gateway http://localhost:8000 --scorer http://localhost:9000

Live mode picks a random TEST-NET address for each run (198.51.100.0/24 for the
honest client, 203.0.113.0/24 for the attacker) so a block left over from a
previous demo never makes the next one look like a pass. Override with
``--normal-ip`` / ``--attack-ip`` when you want a fixed, reproducible address.
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys
from pathlib import Path

# Ensure backend and shared_core are on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
SHARED_CORE_DIR = REPO_ROOT / "shared_core"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(SHARED_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_CORE_DIR))

import httpx
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
from shared_core.telemetry import TelemetryEvent
from tests._threat_fakes import FakeRedis


def banner(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


async def run_integrated_validation():
    banner("1. Initializing Threat Lens Integrated Environment")

    # Force enable Threat Lens for validation
    settings.THREAT_LENS_ENABLED = True
    settings.RATE_LIMIT_TRUST_FORWARDED = True

    model_path = BACKEND_DIR / "models" / "threat_lens.joblib"
    if not model_path.exists():
        print(f"[*] Training model to {model_path}...")
        from shared_core.training import train_and_persist
        train_and_persist(out_path=model_path, n_normal=2000, n_attack=1000)

    settings.THREAT_LENS_MODEL_PATH = str(model_path)
    threat_tasks._detector = None
    scoring_api._detector = None

    fake_redis = FakeRedis()
    async def _get_client():
        return fake_redis
    cache_service._get_client = _get_client

    persisted_rows: dict[str, BlockedIp] = {}
    async def _mock_persist_block(identity, reason, risk, source="THREAT_LENS"):
        persisted_rows[identity] = BlockedIp(
            ip=identity,
            reason=reason,
            risk=risk,
            blocked_by="auto" if source == "THREAT_LENS" else "admin",
        )
    tl.persist_block = _mock_persist_block

    enqueued_events: list[TelemetryEvent] = []
    async def _enqueue_sync(event: TelemetryEvent):
        enqueued_events.append(event)
        # Execute Celery task synchronously
        analyze_telemetry_task(event.to_dict())
    tl.enqueue_telemetry = _enqueue_sync

    async def ping_route(req):
        return JSONResponse({"status": "ok", "path": req.url.path})

    app = Starlette(
        routes=[
            Route("/api/v1/articles", ping_route, methods=["GET"]),
            Route("/api/v1/auth/login", ping_route, methods=["POST"]),
        ]
    )
    app.add_middleware(ThreatLensMiddleware)

    gateway_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway:8000"
    )
    scorer_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=scoring_api.app), base_url="http://scorer:9000"
    )

    print(f"  [OK] Model loaded from: {model_path}")
    print(f"  [OK] Risk threshold: {settings.THREAT_LENS_RISK_THRESHOLD}")
    print(f"  [OK] Min anomalies to block: {settings.THREAT_LENS_WINDOW_MIN_ANOMALIES}")

    # ── Test Normal Traffic ───────────────────────────────────────────────────
    banner("2. Normal Traffic Verification")
    normal_ip = "198.51.100.15"
    async with gateway_client as gc:
        for i in range(3):
            res = await gc.get(
                "/api/v1/articles",
                headers={
                    "x-forwarded-for": normal_ip,
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0",
                    "authorization": "Bearer user-token",
                },
            )
            print(f"  Request {i+1} from normal IP {normal_ip} -> HTTP {res.status_code}")
            assert res.status_code == 200

        assert f"threat:blocked:{normal_ip}" not in fake_redis.store
        print(f"  [OK] Normal IP {normal_ip} is NOT blocked (status: allowed)")

        # ── Test Synthetic Attack Traffic ─────────────────────────────────────────
        banner("3. Synthetic Attack Traffic & Anomaly Blocking")
        attack_ip = "203.0.113.99"
        attack_headers = {
            "x-forwarded-for": attack_ip,
            "content-type": "application/json",
            # no user-agent, no authorization header
        }
        attack_payload = {"user": "admin", "password": "credential_stuffing_attempt_" * 50}

        # Attack request 1: first anomaly hit
        r1 = await gc.post("/api/v1/auth/login", headers=attack_headers, json=attack_payload)
        print(f"  Attack Request 1 from {attack_ip} -> HTTP {r1.status_code} (telemetry enqueued & scored)")
        assert r1.status_code == 200
        assert f"threat:blocked:{attack_ip}" not in fake_redis.store

        # Attack request 2: second anomaly hit -> trips window threshold (>=2)
        r2 = await gc.post("/api/v1/auth/login", headers=attack_headers, json=attack_payload)
        print(f"  Attack Request 2 from {attack_ip} -> HTTP {r2.status_code} (anomalies >= 2)")
        assert r2.status_code == 200

        # Verify Redis marker and DB row exist immediately
        assert fake_redis.store.get(f"threat:blocked:{attack_ip}") == "1"
        assert attack_ip in persisted_rows
        print(f"  [OK] Redis marker SET: threat:blocked:{attack_ip} = 1")
        print(f"  [OK] blocked_ips row inserted: IP={attack_ip}, risk={persisted_rows[attack_ip].risk:.4f}, blocked_by={persisted_rows[attack_ip].blocked_by}")

        # Attack request 3: dropped at gateway with 403 Forbidden!
        r3 = await gc.post("/api/v1/auth/login", headers=attack_headers, json=attack_payload)
        print(f"  Attack Request 3 from {attack_ip} -> HTTP {r3.status_code} Forbidden (Edge Dropped!)")
        assert r3.status_code == 403
        assert "temporarily blocked" in r3.json()["detail"]
        print("  [OK] Edge block verified: 403 Forbidden returned instantly")

        # Legitimate client still passes unaffected
        r_norm = await gc.get(
            "/api/v1/articles",
            headers={"x-forwarded-for": normal_ip, "user-agent": "Mozilla/5.0"},
        )
        assert r_norm.status_code == 200
        print("  [OK] Legitimate client unaffected: HTTP 200 OK")

    # ── Test /score Diagnostics ───────────────────────────────────────────────
    banner("4. Scoring Diagnostic Endpoint (/score)")
    async with scorer_client as sc:
        # GET /score
        score_get = await sc.get(f"/score?ip={attack_ip}&method=POST&path=/api/v1/auth/login&payload_bytes=500000")
        assert score_get.status_code == 200
        get_data = score_get.json()
        print(f"  GET /score -> identity: {get_data['identity']}, risk: {get_data['risk']}, violates: {get_data['violates']}")

        # POST /score
        attack_event = enqueued_events[-2]  # the attack event
        score_post = await sc.post("/score", json={"telemetry": attack_event.to_dict()})
        assert score_post.status_code == 200
        post_data = score_post.json()
        print(f"  POST /score -> identity: {post_data['identity']}, risk: {post_data['risk']}, violates: {post_data['violates']}")
        print(f"  Features evaluated ({len(post_data['features'])} total): {post_data['features'][:5]}...")
        assert post_data["risk"] >= settings.THREAT_LENS_RISK_THRESHOLD

    banner("SPRINT 1 VALIDATION SUCCESSFUL: ALL CHECKS PASSED!")
    return True


async def run_live_validation(
    gateway_url: str,
    scorer_url: str,
    normal_ip: str | None = None,
    attack_ip: str | None = None,
    attempts: int = 8,
    delay: float = 0.4,
) -> bool:
    """Drive the real containers the way a client would.

    Unlike the integrated run, nothing is mocked here: the gateway enqueues on
    Redis, the Celery worker scores with the model from the shared volume, and
    the block is observed as an actual 403 from the running API.
    """
    normal_ip = normal_ip or f"198.51.100.{random.randint(10, 250)}"
    attack_ip = attack_ip or f"203.0.113.{random.randint(10, 250)}"

    banner("LIVE validation against the running stack")
    print(f"  gateway: {gateway_url}")
    print(f"  scorer : {scorer_url}")
    print(f"  honest : {normal_ip}   attacker: {attack_ip}")

    ok = True
    async with httpx.AsyncClient(timeout=20.0) as client:
        # 1. The scorer must be up *and* have a model, otherwise nothing below
        # can be attributed to Threat Lens.
        h_res = await client.get(f"{scorer_url}/health")
        health = h_res.json()
        print(f"\n[1] GET /health -> {h_res.status_code} {health}")
        if h_res.status_code != 200 or not health.get("model_loaded"):
            print("    [FAIL] model is not loaded; scoring is skipped and nothing blocks.")
            print("           Copy the artifact onto the model_data volume or retrain:")
            print("           docker compose exec -T backend threat-lens-train --out /app/models/threat_lens.joblib")
            return False
        print("    [OK] model loaded from the shared volume")

        # 2. Honest client: realistic browser headers, must stay 200.
        normal_headers = {
            "X-Forwarded-For": normal_ip,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        }
        print(f"\n[2] Honest client {normal_ip} -> GET /api/v1/events/latest")
        for i in range(1, 4):
            res = await client.get(
                f"{gateway_url}/api/v1/events/latest", headers=normal_headers
            )
            print(f"    request {i}: HTTP {res.status_code}")
            if res.status_code != 200:
                print(f"    [FAIL] honest traffic was refused (HTTP {res.status_code}).")
                print("           If 403, this IP is already blocked: pass --normal-ip with a fresh address.")
                return False
        print("    [OK] honest client served 200, three times")

        # 3. Attack client: no User-Agent, no Authorization, oversized JSON body
        #    (classic credential stuffing shape). Keep posting until the edge
        #    answers 403 or we run out of attempts.
        attack_headers = {
            "X-Forwarded-For": attack_ip,
            "Content-Type": "application/json",
            "Accept": "*/*",
        }
        # A valid shape so the endpoint parses it (a 422 is still observed, but a
        # parsed 401 is a more honest "attack that reached the app").
        payload = {
            "email": "admin@example.com",
            "password": "credential_stuffing_" * 4000,
        }
        print(f"\n[3] Attacker {attack_ip} -> POST /api/v1/auth/login (<= {attempts} hits)")
        blocked = False
        saw_rate_limit = False
        last = None
        for i in range(1, attempts + 1):
            res = await client.post(
                f"{gateway_url}/api/v1/auth/login",
                headers=attack_headers,
                json=payload,
            )
            last = res
            print(f"    hit {i}: HTTP {res.status_code}")
            if res.status_code == 403:
                blocked = True
                break
            if res.status_code == 429:
                saw_rate_limit = True
            await asyncio.sleep(delay)

        if not blocked:
            print("\n    [FAIL] the client was never refused with 403.")
            if saw_rate_limit:
                print("           The rate limiter answered 429 first; the monthly/daily quota")
                print("           is protecting the API, but Threat Lens did not trip.")
            print("           Check THREAT_LENS_ENABLED=true and that the worker is consuming")
            print("           the 'threat' queue: docker compose logs --tail=30 worker threat")
            return False

        detail = (last.json() or {}).get("detail", "")
        print(f"    [OK] edge drop: HTTP 403 — {detail!r}")
        if "temporarily blocked" not in detail:
            print("    [WARN] unexpected 403 body; was this really Threat Lens?")
            ok = False

        # 4. A blocked attacker must not take the honest client down with them.
        res = await client.get(
            f"{gateway_url}/api/v1/events/latest", headers=normal_headers
        )
        print(f"\n[4] Honest client after the block -> HTTP {res.status_code}")
        if res.status_code != 200:
            print("    [FAIL] collateral damage: honest client is no longer served.")
            return False
        print("    [OK] honest client still served 200")

        # 5. The block must be durable and visible to an operator.
        print("\n[5] Operator evidence")
        print(f"    redis:  docker compose exec -T cache redis-cli -a <pw> KEYS 'threat:blocked:{attack_ip}'")
        print(f"    audit:  docker compose exec -T db psql -U <user> -d <db> -c \"SELECT ip, source, risk_score, blocked_at FROM blocked_ips WHERE ip = '{attack_ip}';\"")
        print(f"    admin:  GET {gateway_url}/api/v1/admin/threat/blocks  (as an admin bearer token)")
        print(f"    undo :  POST {gateway_url}/api/v1/admin/threat/blocks/{attack_ip}/unblock")
        print(f"    [OK] block is live for {attack_ip}; unblock is one API call away")

    if ok:
        banner("LIVE VALIDATION PASSED: telemetry -> model -> block -> 403")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Run against running HTTP containers")
    parser.add_argument("--gateway", default="http://localhost:8000", help="Gateway URL")
    parser.add_argument("--scorer", default="http://localhost:9000", help="Scorer debug API URL")
    parser.add_argument("--normal-ip", default=None, help="Fixed IP for the honest client (default: random TEST-NET-2)")
    parser.add_argument("--attack-ip", default=None, help="Fixed IP for the attacker (default: random TEST-NET-3)")
    parser.add_argument("--attempts", type=int, default=8, help="Max attack requests before giving up (default: 8)")
    parser.add_argument("--delay", type=float, default=0.4, help="Seconds between attack requests (default: 0.4)")
    args = parser.parse_args()

    if args.live:
        passed = asyncio.run(
            run_live_validation(
                args.gateway,
                args.scorer,
                normal_ip=args.normal_ip,
                attack_ip=args.attack_ip,
                attempts=args.attempts,
                delay=args.delay,
            )
        )
    else:
        passed = asyncio.run(run_integrated_validation())
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
