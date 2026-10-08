"""
GlobeLens AI — Threat Lens scoring worker task.

Consumes telemetry from the dedicated "threat" Celery queue (routed off the
main queue so backlog never competes with enrichment/newsletter work). For
each request it:

1. rebuilds the TelemetryEvent from the wire dict (lenient schema),
2. fills the windowed request rate this identity has produced,
3. scores the canonical feature vector with the shared_core model,
4. keeps a short rolling window of anomaly flags per identity, and
5. blocks an identity that trips the risk threshold often enough within the
   window — writing the Redis block marker (enforced by the middleware) and a
   feed entry for admin/monitoring.

Everything fails open. A missing model artifact or any Redis error means the
event is dropped silently; abuse defence going quiet is the accepted
degradation mode.
"""
from __future__ import annotations

import asyncio
import logging
import time

from app.core.config import settings
from app.services import threat_lens as tl
from app.services.cache_service import cache_service
from app.worker.celery_app import celery_app
from shared_core.features import extract_feature_vector
from shared_core.model import AnomalyDetector
from shared_core.telemetry import TelemetryEvent

logger = logging.getLogger(__name__)

# The model is loaded once per worker process, not per task: joblib read + deserialise
# is a few hundred ms, while a telemetry task should finish in single-digit ms.
_detector: AnomalyDetector | None = None

# One process-wide event loop, exactly like app.worker.tasks — the asyncpg / redis
# pools are tied to the loop that opened them.
_PROCESS_LOOP: asyncio.AbstractEventLoop | None = None


def _run_in_process_loop(coroutine) -> object:
    global _PROCESS_LOOP
    if _PROCESS_LOOP is None or _PROCESS_LOOP.is_closed():
        _PROCESS_LOOP = asyncio.new_event_loop()
    asyncio.set_event_loop(_PROCESS_LOOP)
    return _PROCESS_LOOP.run_until_complete(coroutine)


def _get_detector() -> AnomalyDetector:
    """Process-cached detector; returns an unloaded (fail-open) one on any error.

    Self-healing: if the cached instance is not loaded (e.g. the container
    started before the model artifact was placed on the volume), every call
    retries the load rather than locking "unloaded" in for the process life.
    """
    global _detector
    if _detector is None:
        detector = AnomalyDetector(model_path=settings.THREAT_LENS_MODEL_PATH)
        _detector = detector
    if not _detector.is_loaded:
        try:
            _detector.load()
        except Exception:
            logger.exception("Threat Lens model load failed; scoring disabled")
    return _detector


async def _window_rate(identity: str) -> int:
    """Count this identity's requests in the current fixed rate window."""
    client = await cache_service._get_client()
    now = int(time.time())
    key = tl.window_key(identity, now, settings.THREAT_LENS_RATE_WINDOW_SECONDS)
    count = await client.incr(key)
    await client.expire(key, settings.THREAT_LENS_RATE_WINDOW_SECONDS + 1)
    return int(count)


async def _push_anomaly_flag(identity: str, anomalous: bool) -> list[str]:
    """Append to the identity's rolling window and return the current contents."""
    client = await cache_service._get_client()
    key = f"threat:window:{identity}"
    await client.rpush(key, "1" if anomalous else "0")
    await client.ltrim(key, -settings.THREAT_LENS_WINDOW_SIZE, -1)
    await client.expire(key, settings.THREAT_LENS_BLOCK_TTL_SECONDS + 3600)
    return await client.lrange(key, 0, -1)


@celery_app.task(name="app.worker.threat_tasks.analyze_telemetry_task")
def analyze_telemetry_task(payload: dict) -> dict:
    """Score one request's telemetry and block the identity if its window trips.

    Returns a dictionary for observability; blocking happens inside. Never
    raises with the API's problem — a throwaway telemetry event is not worth a
    worker crash.
    """
    event = TelemetryEvent.from_dict(payload or {})
    identity = event.ip

    detector = _get_detector()
    if not detector.is_loaded:
        return {"skipped": True, "reason": "model_unavailable", "identity": identity}
    if not identity or identity == "unknown":
        return {"skipped": True, "reason": "no_identity", "identity": identity}

    try:
        event.request_rate = float(_run_in_process_loop(_window_rate(identity)))
        risk = detector.score_risk(extract_feature_vector(event))
        anomalous = risk >= settings.THREAT_LENS_RISK_THRESHOLD
        window = _run_in_process_loop(_push_anomaly_flag(identity, anomalous))
        anomaly_hits = sum(flag == "1" for flag in window)
    except Exception:
        logger.exception("Threat Lens scoring failed; event dropped")
        return {"skipped": True, "reason": "scoring_error", "identity": identity}

    if anomaly_hits < settings.THREAT_LENS_WINDOW_MIN_ANOMALIES:
        return {
            "identity": identity,
            "risk": risk,
            "anomalous": anomalous,
            "anomaly_hits": anomaly_hits,
        }

    reason = f"anomalous_x{anomaly_hits}_risk_{risk:.2f}"
    _run_in_process_loop(tl.block_identity(identity, reason))
    _run_in_process_loop(tl.persist_block(identity, reason, risk))
    return {
        "identity": identity,
        "risk": risk,
        "anomalous": anomalous,
        "anomaly_hits": anomaly_hits,
        "blocked": True,
        "reason": reason,
    }