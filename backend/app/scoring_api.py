"""
GlobeLens AI — Threat Lens scoring DEBUG endpoint.

Internal-only diagnostic API on port 9000 (never the public gateway). It lets
an operator score a single request's telemetry against the deployed model and
see exactly what the worker would have decided — including the feature vector,
so a surprising risk can be traced to a field.

It deliberately does NOT write blocks: blocking belongs to the worker's
windowed decision, and this API is a read-only microscope, not a switch.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.config import settings
from shared_core.features import FEATURE_NAMES, extract_feature_vector
from shared_core.model import AnomalyDetector
from shared_core.telemetry import TelemetryEvent

app = FastAPI(
    title="Threat Lens Scoring (internal)",
    description="Internal diagnostic scorer for the global news platform.",
    docs_url=None,
    redoc_url=None,
    version="0.1.0",
)

_detector: AnomalyDetector | None = None


class ScoreRequest(BaseModel):
    telemetry: dict[str, Any] = Field(..., description="One TelemetryEvent as a dict")


def _get_detector() -> AnomalyDetector:
    """Process-cached, fail-open detector.

    Self-healing: if the cached instance is not loaded (e.g. the sidecar started
    before the model artifact was placed on the volume), every call retries the
    load so the API comes online when the artifact appears.
    """
    global _detector
    if _detector is None:
        _detector = AnomalyDetector(model_path=settings.THREAT_LENS_MODEL_PATH)
    if not _detector.is_loaded:
        try:
            _detector.load()
        except Exception:
            pass
    return _detector


@app.get("/health", tags=["Health"], include_in_schema=False)
async def health() -> dict:
    detector = _get_detector()
    return {
        "status": "ok",
        "model_loaded": detector.is_loaded,
        "model_path": settings.THREAT_LENS_MODEL_PATH,
    }


def _evaluate_event(event: TelemetryEvent) -> dict[str, Any]:
    detector = _get_detector()
    if not detector.is_loaded:
        raise HTTPException(status_code=503, detail="Threat Lens model not loaded")

    features = extract_feature_vector(event)
    risk = float(detector.score_risk(features))
    threshold = settings.THREAT_LENS_RISK_THRESHOLD
    return {
        "identity": event.ip,
        "risk": round(risk, 4),
        "threshold": threshold,
        "violates": risk >= threshold,
        "feature_names": list(FEATURE_NAMES),
        "features": [round(float(v), 4) for v in features.tolist()],
    }


@app.get("/score", tags=["Score"])
async def score_get(request: Request) -> dict[str, Any]:
    """Score telemetry via GET request with query params (or sample defaults)."""
    params: dict[str, Any] = dict(request.query_params)
    if "payload_bytes" in params:
        try:
            params["payload_bytes"] = int(params["payload_bytes"])
        except ValueError:
            pass
    if "header_count" in params:
        try:
            params["header_count"] = int(params["header_count"])
        except ValueError:
            pass
    if "header_entropy" in params:
        try:
            params["header_entropy"] = float(params["header_entropy"])
        except ValueError:
            pass
    if "time_of_day" in params:
        try:
            params["time_of_day"] = float(params["time_of_day"])
        except ValueError:
            pass
    if "endpoint_sensitivity" in params:
        try:
            params["endpoint_sensitivity"] = float(params["endpoint_sensitivity"])
        except ValueError:
            pass
    if "request_rate" in params:
        try:
            params["request_rate"] = float(params["request_rate"])
        except ValueError:
            pass
    if "path_depth" in params:
        try:
            params["path_depth"] = int(params["path_depth"])
        except ValueError:
            pass
    if "user_agent_length" in params:
        try:
            params["user_agent_length"] = int(params["user_agent_length"])
        except ValueError:
            pass
    event = TelemetryEvent.from_dict(params)
    return _evaluate_event(event)


@app.post("/score", tags=["Score"])
async def score(body: ScoreRequest) -> dict[str, Any]:
    """Score telemetry exactly as the worker does. Never writes state."""
    event = TelemetryEvent.from_dict(body.telemetry or {})
    return _evaluate_event(event)