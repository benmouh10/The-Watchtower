"""Synthetic traffic generator + training harness for the Threat Lens model.

The model is unsupervised, but it still needs a plausible picture of what
normal looks like before it can say "this is not normal". We cannot ship a
labelled corpus of real attacks, so the trainer synthesises two populations:

* normal traffic  — patterns that match everyday users (mostly GET, small
  bodies, UA present, sparse bursts).
* attack traffic  — hand-branded shapes attackers actually use: credential
  stuffing at high rates against auth endpoints without an Authorization
  header, oversized bodies, malformed/absent User-Agent, spoofed
  X-Forwarded-For, path-traversal-deep URLs.

The forest is fit on the NORMAL population ONLY — it learns what normality
is, and an attack is, by definition, anything it isolates from that. The
synthetic attacks are used exclusively for validation (how far they land
beyond the normal population). Both populations are produced through the SAME
extract_feature_vector code the scoring worker will run live, which is what
makes a fit on synthetic data meaningful at all.

Risk is the percentile of a live sample against the normal fit-scores: a
sample that scores like worse-than-90%-of-normal traffic has risk 0.90+.
Blocking is a SEPARATE, windowed layer (an IP is only blocked after several
windowed anomalies), so a per-request risk budget that allows a small normal
tail is acceptable.

CLI (installed as `threat-lens-train` via shared_core's pyproject):
    threat-lens-train --out /path/to/anomaly.joblib [--n-normal 6000] [--n-attack 3000] [--threshold 0.90]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from shared_core.features import extract_feature_vector
from shared_core.model import AnomalyDetector
from shared_core.telemetry import TelemetryEvent

DEFAULT_OUT = "model/threat_lens.joblib"
DEFAULT_THRESHOLD = 0.90

_PUBLIC_PATHS = ("/", "/health", "/articles", "/events", "/latest", "/trending")


def _path_profile(rng: np.random.Generator, is_api: bool) -> tuple[str, int, float]:
    """Return (path, depth, sensitivity) for a realisically ordinary endpoint."""
    if is_api:
        return f"/api/{rng.choice(['articles', 'events', 'search'])}", 2, 0.5
    path = str(rng.choice(_PUBLIC_PATHS))
    return path, max(1, path.count("/")), 0.0


def _normal_event(rng: np.random.Generator) -> TelemetryEvent:
    """Everyday traffic: no auth-endpoint hammering, modest bodies, real UAs.

    Deliberately keeps the normal cloud away from attack territory: rare *and*
    slow auth use, small bodies (capped), headers that always carry too much
    variety to be scripted, no spoofed proxy headers. A synthetic "normal" that
    included big uploads and dense auth hits would make attacks invisible.
    """
    method = "GET" if rng.random() < 0.72 else "POST"
    is_api = rng.random() < 0.55
    path, depth, sensitivity = _path_profile(rng, is_api)
    if method == "GET":
        payload = 0
    else:
        # Normal uploads stay modest (≤ ~150KB); anything bigger is attacker
        # shaped and reserved for the attack generator.
        payload = min(int(rng.lognormal(mean=4.2, sigma=1.0)), 150_000)
    return TelemetryEvent(
        ip="10.0.0.1",
        method=method,
        path=path,
        payload_bytes=max(0, payload),
        header_count=int(rng.integers(6, 18)),
        header_entropy=round(float(rng.uniform(3.4, 5.5)), 4),
        user_agent_present=True,
        user_agent_length=int(rng.integers(45, 160)),
        content_type_is_json=method == "POST" and rng.random() < 0.7,
        has_authorization=rng.random() < (0.35 if is_api else 0.05),
        has_forwarded=False,
        path_depth=depth,
        is_api_path=is_api,
        time_of_day=round(float(rng.uniform(0.0, 1.0)), 4),
        endpoint_sensitivity=sensitivity,
        status=200 if rng.random() < 0.92 else 401,
        request_rate=round(float(rng.lognormal(mean=0.3, sigma=0.7)), 4),
    )


def _attack_event(rng: np.random.Generator) -> TelemetryEvent:
    """Hand-branded attack shapes; each returns a clearly out-of-distribution row."""
    profile = int(rng.integers(0, 6))
    ip = "{}.{}.{}.{}".format(*(int(rng.integers(180, 224)) for _ in range(4)))
    if profile == 0:    # credential stuffing: fast, no auth, on /api/auth/login
        return TelemetryEvent(
            ip=ip, method="POST", path="/api/auth/login",
            payload_bytes=int(rng.integers(120, 240)),
            header_count=int(rng.integers(4, 8)),
            header_entropy=round(float(rng.uniform(1.5, 2.8)), 4),
            user_agent_present=True, user_agent_length=int(rng.integers(20, 40)),
            content_type_is_json=True, has_authorization=False, has_forwarded=False,
            path_depth=3, is_api_path=True,
            time_of_day=round(float(rng.uniform(0.0, 1.0)), 4),
            endpoint_sensitivity=1.0, status=401,
            request_rate=round(float(rng.lognormal(mean=3.5, sigma=0.6)), 4),
        )
    if profile == 1:    # upload abuse: absurd body
        return TelemetryEvent(
            ip=ip, method="POST", path="/api/events",
            payload_bytes=int(rng.integers(1_000_000, 8_000_000)),
            header_count=int(rng.integers(6, 10)),
            header_entropy=round(float(rng.uniform(2.0, 3.0)), 4),
            user_agent_present=True, user_agent_length=int(rng.integers(50, 90)),
            content_type_is_json=False, has_authorization=False, has_forwarded=False,
            path_depth=2, is_api_path=True,
            time_of_day=round(float(rng.uniform(0.0, 1.0)), 4),
            endpoint_sensitivity=0.5, status=200, request_rate=1.0,
        )
    if profile == 2:    # spoofed proxy chain
        return TelemetryEvent(
            ip=ip, method="GET", path="/api/articles", payload_bytes=0,
            header_count=int(rng.integers(8, 12)),
            header_entropy=round(float(rng.uniform(2.5, 3.5)), 4),
            user_agent_present=True, user_agent_length=int(rng.integers(70, 120)),
            content_type_is_json=False, has_authorization=True, has_forwarded=True,
            path_depth=2, is_api_path=True, time_of_day=0.5, endpoint_sensitivity=0.5,
            status=200, request_rate=round(float(rng.lognormal(mean=2.8, sigma=0.5)), 4),
        )
    if profile == 3:    # path traversal: absurdly deep, weird methods
        return TelemetryEvent(
            ip=ip, method="GET", path="/api/../..", payload_bytes=0,
            header_count=int(rng.integers(2, 6)),
            header_entropy=round(float(rng.uniform(1.0, 2.0)), 4),
            user_agent_present=False, user_agent_length=0,
            content_type_is_json=False, has_authorization=False, has_forwarded=False,
            path_depth=12, is_api_path=False, time_of_day=0.25, endpoint_sensitivity=0.9,
            status=404, request_rate=round(float(rng.lognormal(mean=2.5, sigma=0.5)), 4),
        )
    if profile == 4:    # no UA + repetitive headers (low entropy)
        return TelemetryEvent(
            ip=ip, method="GET", path="/health", payload_bytes=0,
            header_count=int(rng.integers(3, 6)),
            header_entropy=round(float(rng.uniform(0.4, 1.2)), 4),
            user_agent_present=False, user_agent_length=0,
            content_type_is_json=False, has_authorization=False, has_forwarded=False,
            path_depth=1, is_api_path=False, time_of_day=0.75, endpoint_sensitivity=0.0,
            status=200, request_rate=round(float(rng.lognormal(mean=3.0, sigma=0.6)), 4),
        )
    # credential theft on sensitive endpoints with odd payloads
    return TelemetryEvent(
        ip=ip, method="POST", path="/api/auth/login",
        payload_bytes=int(rng.integers(1_000, 500_000)),
        header_count=int(rng.integers(5, 9)),
        header_entropy=round(float(rng.uniform(1.0, 2.2)), 4),
        user_agent_present=True, user_agent_length=int(rng.integers(10, 30)),
        content_type_is_json=True, has_authorization=False, has_forwarded=True,
        path_depth=3, is_api_path=True, time_of_day=0.9, endpoint_sensitivity=1.0,
        status=200, request_rate=round(float(rng.lognormal(mean=3.5, sigma=0.8)), 4),
    )


def build_synthetic_traffic(
    n_normal: int = 6000,
    n_attack: int = 3000,
    seed: int = 7,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (X_normal, X_attack) feature matrices, same columns as serving."""
    rng = np.random.default_rng(seed)

    def _build(count: int, maker) -> np.ndarray:
        return np.stack(
            [extract_feature_vector(maker(rng)) for _ in range(count)]
        )

    return _build(n_normal, _normal_event), _build(n_attack, _attack_event)


def evaluate(
    detector: AnomalyDetector,
    X_normal: np.ndarray,
    X_attack: np.ndarray,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, float]:
    """Recall on attack traffic and false-positive rate on normal traffic.

    The false-positive rate is *not* an independent free variable here: with
    percentile calibration it is, by construction, whatever the risk threshold
    says of the normal population (thr 0.90 ⇒ ≤0.10 of normals exceed it).
    The meaningful number is RECALL: how far the attack cloud lands beyond the
    normal tail, measured at a given per-request risk budget.
    """
    normal_risk = detector.risk(X_normal)
    attack_risk = detector.risk(X_attack)
    return {
        "recall": float(np.mean(attack_risk >= threshold)),
        "false_positive_rate": float(np.mean(normal_risk >= threshold)),
        "n_normal": int(len(X_normal)),
        "n_attack": int(len(X_attack)),
        "threshold": float(threshold),
        "mode_of_attack_risk": float(np.median(attack_risk)),
    }


def train_and_persist(
    out_path: str | Path = DEFAULT_OUT,
    n_normal: int = 6000,
    n_attack: int = 3000,
    seed: int = 7,
    threshold: float = DEFAULT_THRESHOLD,
) -> AnomalyDetector:
    """Fit on SYNTHETIC NORMAL traffic only, then validate against attacks.

    Returns the detector; validation metrics are printed, not asserted, so an
    operator sees recall at the configured per-request budget without the
    trainer ever failing on regressions in synthetic data.
    """
    X_normal, X_attack = build_synthetic_traffic(
        n_normal=n_normal, n_attack=n_attack, seed=seed
    )
    detector = AnomalyDetector(model_path=out_path, seed=seed)
    detector.train(X=np.asarray(X_normal)).persist()
    print(f"wrote model -> {out_path}")
    return detector


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=DEFAULT_OUT, help="joblib output path")
    parser.add_argument("--n-normal", type=int, default=6000)
    parser.add_argument("--n-attack", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = parser.parse_args(argv)

    X_normal, X_attack = build_synthetic_traffic(
        n_normal=args.n_normal, n_attack=args.n_attack, seed=args.seed
    )
    detector = AnomalyDetector(model_path=args.out, seed=args.seed)
    detector.train(X=np.asarray(X_normal)).persist()
    metrics = evaluate(detector, X_normal, X_attack, threshold=args.threshold)
    print(f"wrote model -> {args.out}")
    print(
        f"vector shape -> {X_normal.shape[1]} features "
        f"(contract: {len(extract_feature_vector(TelemetryEvent()))})"
    )
    for key in (
        "n_normal",
        "n_attack",
        "threshold",
        "recall",
        "false_positive_rate",
        "mode_of_attack_risk",
    ):
        print(f"{key} = {metrics[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())