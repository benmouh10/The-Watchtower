"""Hermetic tests for the Threat Lens scoring worker task."""
import pytest

from app.core.config import settings
from app.services import threat_lens as tl
from app.worker import threat_tasks
from app.worker.threat_tasks import analyze_telemetry_task
from shared_core.model import AnomalyDetector
from shared_core.training import build_synthetic_traffic

from tests._threat_fakes import threat_cache  # noqa: F401 (fixture re-export)

ATTACK = {
    "ip": "203.0.113.66",
    "method": "POST",
    "path": "/api/auth/login",
    "payload_bytes": 400_000,
    "header_count": 5,
    "header_entropy": 1.4,
    "user_agent_present": False,
    "user_agent_length": 0,
    "content_type_is_json": True,
    "has_authorization": False,
    "has_forwarded": False,
    "path_depth": 3,
    "is_api_path": True,
    "time_of_day": 0.5,
    "endpoint_sensitivity": 1.0,
    "status": 401,
    "request_rate": 0.0,
}

NORMAL = {
    "ip": "198.51.100.7",
    "method": "GET",
    "path": "/api/articles/42",
    "payload_bytes": 0,
    "header_count": 12,
    "header_entropy": 4.8,
    "user_agent_present": True,
    "user_agent_length": 110,
    "content_type_is_json": False,
    "has_authorization": True,
    "has_forwarded": False,
    "path_depth": 3,
    "is_api_path": True,
    "time_of_day": 0.6,
    "endpoint_sensitivity": 0.5,
    "status": 200,
    "request_rate": 0.0,
}


@pytest.fixture
def trained_model(tmp_path, monkeypatch):
    """Train a small model and point the worker at it; reset the module cache."""
    normal, _ = build_synthetic_traffic(n_normal=150, n_attack=75, seed=7)
    path = tmp_path / "threat_lens.joblib"
    AnomalyDetector(str(path), seed=7).train(normal).persist()
    monkeypatch.setattr(settings, "THREAT_LENS_MODEL_PATH", str(path))
    monkeypatch.setattr(threat_tasks, "_detector", None)
    return path


@pytest.fixture
def noop_side_effects(monkeypatch):
    """Keep a block decision from touching Redis-blocked markers or the DB."""
    block_calls = []

    async def _block(identity, reason):
        block_calls.append((identity, reason))

    async def _record(identity, reason, risk):
        pass

    monkeypatch.setattr(tl, "block_identity", _block)
    monkeypatch.setattr(threat_tasks, "_record_block", _record)
    return block_calls


class TestScoring:
    def test_attack_flow_blocks_after_min_anomalies(self, threat_cache, trained_model, noop_side_effects):
        outcomes = [analyze_telemetry_task(dict(ATTACK)) for _ in range(5)]
        assert outcomes[0]["anomalous"] is True
        assert outcomes[0].get("blocked") is None
        assert all(o.get("blocked") is True for o in outcomes[1:])
        assert [o["identity"] for o in outcomes[1:]] == [ATTACK["ip"]] * 4

    def test_normal_flow_never_blocks(self, threat_cache, trained_model, noop_side_effects):
        outcomes = [analyze_telemetry_task(dict(NORMAL)) for _ in range(5)]
        assert len(outcomes) == 5
        assert all(o.get("blocked") is None for o in outcomes)

    def test_block_decision_records_blocked_identity(self, threat_cache, trained_model, noop_side_effects):
        block_calls = noop_side_effects
        for _ in range(2):
            analyze_telemetry_task(dict(ATTACK))
        assert block_calls and block_calls[0][0] == ATTACK["ip"]

    def test_window_is_per_identity(self, threat_cache, trained_model, noop_side_effects):
        for _ in range(3):
            analyze_telemetry_task(dict(ATTACK))
        for _ in range(5):
            assert analyze_telemetry_task(dict(NORMAL)).get("blocked") is None


class TestFailOpen:
    def test_missing_model_skips_without_raising(self, threat_cache, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "THREAT_LENS_MODEL_PATH", str(tmp_path / "missing.joblib"))
        monkeypatch.setattr(threat_tasks, "_detector", None)
        result = analyze_telemetry_task(dict(ATTACK))
        assert result["skipped"] is True
        assert result["reason"] == "model_unavailable"

    def test_model_appearing_later_self_heals(self, threat_cache, tmp_path, monkeypatch, noop_side_effects):
        """The cached detector retries the load instead of locking 'unloaded' in."""
        monkeypatch.setattr(threat_tasks, "_detector", None)
        normal, _ = build_synthetic_traffic(n_normal=150, n_attack=75, seed=7)
        path = tmp_path / "threat_lens.joblib"
        monkeypatch.setattr(settings, "THREAT_LENS_MODEL_PATH", str(path))

        assert analyze_telemetry_task(dict(ATTACK))["reason"] == "model_unavailable"

        AnomalyDetector(str(path), seed=7).train(normal).persist()
        outcome = analyze_telemetry_task(dict(ATTACK))
        assert outcome.get("skipped") is not True
        assert outcome["anomalous"] is True

    def test_unknown_identity_is_skipped(self, threat_cache, trained_model, noop_side_effects):
        payload = dict(ATTACK)
        payload["ip"] = "unknown"
        result = analyze_telemetry_task(payload)
        assert result["skipped"] is True
        assert result["reason"] == "no_identity"

    def test_empty_payload_is_handled(self, threat_cache, trained_model, noop_side_effects):
        result = analyze_telemetry_task({})
        assert result["identity"] == "unknown"
        assert result["skipped"] is True

    def test_redis_down_drops_event_quietly(self, monkeypatch, trained_model, noop_side_effects):
        async def _boom():
            raise RuntimeError("redis down")

        from app.services.cache_service import cache_service

        monkeypatch.setattr(cache_service, "_get_client", _boom)
        result = analyze_telemetry_task(dict(ATTACK))
        assert result["skipped"] is True
        assert result["reason"] == "scoring_error"