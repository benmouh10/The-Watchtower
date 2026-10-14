"""
GlobeLens AI — Threat Lens service layer.

The bridge between the HTTP middleware and the Celery worker that scores
traffic. It builds a shared_core TelemetryEvent from a Starlette Request,
enqueues it on the dedicated "threat" queue, and reads/writes the per-identity
block markers in Redis.

Every interaction is fail-open by design: if Redis or the broker is down, the
worst outcome is "no telemetry, no block check", never a failed request. A
missing block marker (or a Redis blip) must mean "allow through".
"""
from __future__ import annotations

import time
from typing import Optional

from starlette.requests import Request

from app.core.config import settings
from app.services.cache_service import cache_service
from shared_core.features import shannon_entropy
from shared_core.telemetry import TelemetryEvent

# Endpoints that are honey for credential theft / stuffing, scored 1.0 so the
# model sees them as sensitive regardless of Redis state.
_SENSITIVE_ENDPOINTS = {
    "/api/v1/auth/login": 1.0,
    "/api/v1/auth/register": 1.0,
    "/api/v1/auth/refresh": 1.0,
    "/api/v1/auth/logout": 0.9,
}

# Paths the middleware never telemetries nor block-checks: health probes, docs
# and static media. Mirror of the rate limiter's _EXEMPT_PATHS plus media.
THREAT_EXEMPT_PATHS = {"/", "/health", "/docs", "/redoc", "/openapi.json", "/favicon.ico"}


def _header_blob(headers) -> str:
    """Flatten a Starlette Headers object into one string for entropy math."""
    return "\n".join(f"{name}:{headers[name]}" for name in dict(headers))


def client_identity(request: Request) -> str:
    """Same identity rules as the rate limiter: trust forwarded only when told."""
    if settings.RATE_LIMIT_TRUST_FORWARDED:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip() or "unknown"
    client = request.client
    return client.host if client and client.host else "unknown"


def endpoint_sensitivity(path: str) -> float:
    """0..1 sensitivity of an endpoint, used as a model feature."""
    for sensitive, score in _SENSITIVE_ENDPOINTS.items():
        if path.startswith(sensitive):
            return score
    if path.startswith("/api/v1/admin"):
        return 0.9
    if path.startswith("/api/v1/"):
        return 0.5
    return 0.0


def build_telemetry(request: Request, status_code: int) -> TelemetryEvent:
    """Turn a Starlette request (plus its eventual status) into a telemetry event.

    request_rate is filled by the threat worker, which owns the per-IP window;
    building it here would cost an extra Redis read on the hot path.
    """
    headers = request.headers
    content_length = headers.get("content-length", "0")
    try:
        payload_bytes = max(0, int(content_length))
    except (TypeError, ValueError):
        payload_bytes = 0

    path = request.url.path.rstrip("/") or "/"
    authorization = headers.get("authorization", "")
    user_agent = headers.get("user-agent", "")
    now = time.gmtime()

    return TelemetryEvent(
        ip=client_identity(request),
        method=request.method,
        path=path,
        payload_bytes=payload_bytes,
        header_count=len(headers),
        header_entropy=round(shannon_entropy(_header_blob(headers)), 4),
        user_agent_present=bool(user_agent),
        user_agent_length=len(user_agent),
        content_type_is_json=(headers.get("content-type", "") or "").startswith(
            "application/json"
        ),
        has_authorization=authorization[:7].lower() == "bearer ",
        has_forwarded=bool(
            headers.get("x-forwarded-for") or headers.get("x-real-ip")
        ),
        path_depth=path.count("/"),
        is_api_path=path.startswith("/api/v1/"),
        time_of_day=round((now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec) / 86400.0, 4),
        endpoint_sensitivity=endpoint_sensitivity(path),
        status=status_code,
        request_rate=0.0,
    )


async def enqueue_telemetry(event: TelemetryEvent) -> bool:
    """Publish telemetry to the threat queue. Fail-open: never raises."""
    try:
        from app.worker.celery_app import celery_app

        celery_app.send_task(
            settings.THREAT_LENS_TASK,
            args=[event.to_dict()],
            queue=settings.THREAT_LENS_QUEUE,
            ignore_result=True,
        )
        return True
    except Exception:
        return False


async def is_identity_blocked(identity: str) -> bool:
    """Whether a block marker exists for this identity. Fail-open: False on error."""
    try:
        client = await cache_service._get_client()
        return bool(await client.get(f"{settings.THREAT_LENS_BLOCKLIST_KEY_PREFIX}:{identity}"))
    except Exception:
        return False


async def block_identity(identity: str, reason: str) -> None:
    """Mark an identity blocked for the TTL and push onto the block feed.

    Used by the threat worker after a window trips the anomaly count. Errors are
    swallowed: the in-memory/db side of a block decision must not crash the task.
    """
    try:
        client = await cache_service._get_client()
        marker_key = f"{settings.THREAT_LENS_BLOCKLIST_KEY_PREFIX}:{identity}"
        await client.set(
            marker_key,
            "1",
            ex=settings.THREAT_LENS_BLOCK_TTL_SECONDS,
        )
        await client.lpush(
            settings.THREAT_LENS_BLOCKS_FEED_KEY,
            f"{int(time.time())}|{identity}|{reason}",
        )
    except Exception:
        pass


async def unblock_identity(identity: str) -> None:
    """Clear a block marker (admin/ops escape hatch)."""
    try:
        client = await cache_service._get_client()
        await client.delete(f"{settings.THREAT_LENS_BLOCKLIST_KEY_PREFIX}:{identity}")
    except Exception:
        pass


def window_key(identity: str, now: int, window_seconds: int) -> str:
    """Fixed-window bucket key used to fingerprint the current rate bucket."""
    return f"threat:rate:{identity}:{now // max(1, window_seconds)}"