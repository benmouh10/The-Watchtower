"""Feature engineering: request observables -> a fixed, versioned vector.

FEATURE_NAMES is the single order contract between training and serving. Both
the trainer and the scoring worker build vectors through extract_feature_vector,
so a sample used for fitting occupies exactly the same columns as a live
request — the train/serve-skew guard the whole package exists for.

All features are bounded and cheap; nothing here can raise on malformed input.
"""
from __future__ import annotations

import math

import numpy as np

from shared_core.telemetry import TelemetryEvent

# The canonical column order. Do not reorder: appended-only. A new feature must
# be added at the end, and the version bumped, so an old persisted model keeps
# reading the same indices it was trained on.
FEATURE_NAMES: tuple[str, ...] = (
    "request_rate",            # requests/60s observed for this source
    "payload_bytes_log",       # log1p(content-length / body bytes)
    "header_count",            # number of request headers
    "header_entropy",          # Shannon entropy over the header blob
    "user_agent_present",      # 0 if the UA header is absent
    "user_agent_length_log",   # log1p(len(UA))
    "content_type_is_json",
    "has_authorization",
    "has_forwarded",           # spoofing signal: X-Forwarded-* on direct client
    "path_depth",              # number of segments
    "is_api_path",             # under /api/...
    "time_of_day_sin",
    "time_of_day_cos",
    "endpoint_sensitivity",    # 0..1
    "method_num",              # GET=0 POST=1 PUT=2 PATCH=3 DELETE=4 other=5
)

_METHOD_INDEX = {
    "GET": 0, "POST": 1, "PUT": 2, "PATCH": 3, "DELETE": 4,
}

_PAYLOAD_UPPER = 10_000_000  # clipping point for absurd uploads, in bytes.
_PATH_DEPTH_CAP = 12
_HEADER_COUNT_CAP = 200


def _method_number(method: str) -> float:
    return float(_METHOD_INDEX.get(method.upper(), 5))


def extract_feature_vector(event: "TelemetryEvent") -> np.ndarray:
    """Return a float64 vector aligned with FEATURE_NAMES."""
    tod = float(event.time_of_day)
    return np.asarray(
        [
            float(max(0.0, event.request_rate)),
            math.log1p(min(max(0, event.payload_bytes), _PAYLOAD_UPPER)),
            float(min(max(0, event.header_count), _HEADER_COUNT_CAP)),
            float(min(max(0.0, event.header_entropy), 10.0)),
            1.0 if event.user_agent_present else 0.0,
            math.log1p(max(0, event.user_agent_length)),
            1.0 if event.content_type_is_json else 0.0,
            1.0 if event.has_authorization else 0.0,
            1.0 if event.has_forwarded else 0.0,
            float(min(max(1, event.path_depth), _PATH_DEPTH_CAP)),
            1.0 if event.is_api_path else 0.0,
            math.sin(2.0 * math.pi * tod),
            math.cos(2.0 * math.pi * tod),
            float(min(max(0.0, event.endpoint_sensitivity), 1.0)),
            _method_number(event.method),
        ],
        dtype=np.float64,
    )


def feature_vector_from_dict(raw: dict) -> np.ndarray:
    """Build the canonical vector from a wire dict (training convenience)."""
    return extract_feature_vector(TelemetryEvent.from_dict(raw))


def shannon_entropy(text: str) -> float:
    """Shannon entropy in bits of a string; 0.0 for empty input."""
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(text)
    total = 0.0
    for count in counts.values():
        p = count / n
        total -= p * math.log2(p)
    return total