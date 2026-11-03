"""Hermetic tests for the internal Threat Lens /score and /health diagnostic API."""
import httpx
import pytest

from app.core.config import settings
from app.scoring_api import app, _get_detector
import app.scoring_api as scoring_api_mod
from shared_core.model import AnomalyDetector
from shared_core.training import build_synthetic_traffic


@pytest.fixture
def trained_model(tmp_path, monkeypatch):
    normal, _ = build_synthetic_traffic(n_normal=100, n_attack=50, seed=7)
    path = tmp_path / "threat_lens.joblib"
    AnomalyDetector(str(path), seed=7).train(normal).persist()
    monkeypatch.setattr(settings, "THREAT_LENS_MODEL_PATH", str(path))
    monkeypatch.setattr(scoring_api_mod, "_detector", None)
    return path


@pytest.fixture
def client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test:9000"
    )


class TestScoringApi:
    @pytest.mark.asyncio
    async def test_health_reports_ok_and_model_status(self, client, trained_model):
        async with client as c:
            res = await c.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["model_loaded"] is True
        assert data["model_path"] == str(trained_model)

    @pytest.mark.asyncio
    async def test_get_score_with_defaults(self, client, trained_model):
        async with client as c:
            res = await c.get("/score")
        assert res.status_code == 200
        data = res.json()
        assert "risk" in data
        assert 0.0 <= data["risk"] <= 1.0
        assert data["threshold"] == settings.THREAT_LENS_RISK_THRESHOLD
        assert len(data["features"]) == 15
        assert len(data["feature_names"]) == 15

    @pytest.mark.asyncio
    async def test_get_score_with_query_params(self, client, trained_model):
        async with client as c:
            res = await c.get(
                "/score?ip=203.0.113.50&method=POST&path=/api/v1/auth/login&payload_bytes=500000"
            )
        assert res.status_code == 200
        data = res.json()
        assert data["identity"] == "203.0.113.50"
        assert "risk" in data
        assert isinstance(data["violates"], bool)

    @pytest.mark.asyncio
    async def test_post_score_returns_calibrated_risk(self, client, trained_model):
        payload = {
            "telemetry": {
                "ip": "203.0.113.99",
                "method": "POST",
                "path": "/api/v1/auth/login",
                "payload_bytes": 400000,
                "header_count": 4,
                "header_entropy": 1.2,
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
                "request_rate": 50.0,
            }
        }
        async with client as c:
            res = await c.post("/score", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["identity"] == "203.0.113.99"
        assert data["risk"] >= 0.85
        assert data["violates"] is True
        assert len(data["features"]) == 15

    @pytest.mark.asyncio
    async def test_score_503_when_model_missing(self, client, tmp_path, monkeypatch):
        missing_path = tmp_path / "nonexistent.joblib"
        monkeypatch.setattr(settings, "THREAT_LENS_MODEL_PATH", str(missing_path))
        monkeypatch.setattr(scoring_api_mod, "_detector", None)

        async with client as c:
            res = await c.get("/score")
        assert res.status_code == 503
        assert "Threat Lens model not loaded" in res.json()["detail"]
