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

from fastapi import FastAPI, HTTPException
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
    """Process-cached, fail-open detector."""
    global _detector
    if _detector is None:
        detector = AnomalyDetector(model_path=settings.THREAT_LENS_MODEL_PATH)
        try:
            detector.load()
        except Exception:
            pass
        _detector = detector
    return _detector


@app.get("/health", tags=["Health"], include_in_schema=False)
async def health() -> dict:
    detector = _get_detector()
    return {
        "status": "ok",
        "model_loaded": detector.is_loaded,
        "model_path": settings.THREAT_LENS_MODEL_PATH,
    }


@app.post("/score", tags=["Score"])
async def score(body: ScoreRequest) -> dict[str, Any]:
    """Score telemetry exactly as the worker does. Never writes state."""
    event = TelemetryEvent.from_dict(body.telemetry or {})
    detector = _get_detector()

    if not detector.is_loaded:
        raise HTTPException(status_code=503, detail="Threat Lens model not loaded")

    vector = extract_feature_vector(event).tolist()
    risk = float(detector.score_risk(extract_feature_vector(event)))
    threshold = settings.THREAT_LENS_RISK_THRESHOLD
    return {
        "identity": event.ip,
        "risk": round(risk, 4),
        "threshold": threshold,
        "violates": risk >= threshold,
        "feature_names": list(FEATURE_NAMES),
        "features": [round(v, 4) for v in vector],
    }