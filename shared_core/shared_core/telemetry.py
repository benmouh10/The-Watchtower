"""Versioned wire schema for one request's telemetry.

Versioned on purpose: the producer (gateway middleware) and the consumer
(scoring worker) can be deployed at different releases, so ``from_dict`` is
lenient — unknown keys are ignored and missing fields fall back to their
dataclass defaults. Adding a field is a minor version bump that never breaks
an older consumer.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

TELEMETRY_VERSION = 1


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TelemetryEvent:
    """Observables captured for one HTTP request.

    Everything here is *raw* and cheap to collect at the gateway. Derived
    mathematics (the feature vector, the risk score) is computed by the
    scoring worker from these fields — never by the request thread.
    """

    # ── identity & routing ─────────────────────────────────────────────────
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    ts_iso: str = field(default_factory=_utc_now_iso)
    ip: str = "unknown"
    method: str = "GET"
    path: str = "/"

    # ── request shape ──────────────────────────────────────────────────────
    payload_bytes: int = 0
    header_count: int = 0
    header_entropy: float = 0.0
    user_agent_present: bool = False
    user_agent_length: int = 0
    content_type_is_json: bool = False
    has_authorization: bool = False
    has_forwarded: bool = False
    path_depth: int = 1
    is_api_path: bool = False

    # ── context ────────────────────────────────────────────────────────────
    # Seconds since midnight UTC scaled to 0..1 (set by the producer).
    time_of_day: float = 0.0
    # 0..1 priority class of the endpoint (auth > normal API > public).
    endpoint_sensitivity: float = 0.0
    # Status the request ended with (filled by the producer after the app).
    status: int = 200

    # ── filled by the scoring worker ───────────────────────────────────────
    request_rate: float = 0.0

    # ── schema guard ───────────────────────────────────────────────────────
    version: int = TELEMETRY_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "TelemetryEvent":
        """Rebuild from a wire dict, ignoring unknown keys and missing fields.

        Only keys the dataclass declares are passed through; the constructor
        supplies defaults for everything else, so a producer from a future
        release cannot smuggle unknown fields into the consumer.
        """
        known = cls.__dataclass_fields__.keys()
        return cls(**{name: raw[name] for name in known if name in raw})