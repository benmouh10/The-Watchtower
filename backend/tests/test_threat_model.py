"""Hermetic tests for the shared_core anomaly model."""
import numpy as np

from shared_core.model import AnomalyDetector
from shared_core.training import build_synthetic_traffic, evaluate


class TestFailOpen:
    def test_missing_model_answers_zero_risk(self):
        detector = AnomalyDetector("does/not/exist.joblib")
        assert detector.is_loaded is False
        assert detector.score_risk(np.zeros(15)) == 0.0
        assert (detector.risk(np.zeros((3, 15))) == 0.0).all()

    def test_score_samples_returns_zeros_without_model(self):
        detector = AnomalyDetector("does/not/exist.joblib")
        assert (detector.score_samples(np.zeros((2, 15))) == 0.0).all()


class TestTrainPersistLoad:
    def test_roundtrip_survives_disk(self, tmp_path):
        normal, attack = build_synthetic_traffic(n_normal=120, n_attack=60, seed=7)
        path = tmp_path / "threat_lens.joblib"
        detector = AnomalyDetector(str(path), seed=7).train(normal)
        detector.persist()

        reloaded = AnomalyDetector(str(path)).load()
        assert reloaded.is_loaded
        np.testing.assert_allclose(
            reloaded.score_samples(normal[:5]),
            detector.score_samples(normal[:5]),
            rtol=1e-6,
        )

    def test_train_is_deterministic(self):
        normal, _ = build_synthetic_traffic(n_normal=100, n_attack=50, seed=1)
        first = AnomalyDetector("a.joblib", seed=7).train(normal)
        second = AnomalyDetector("b.joblib", seed=7).train(normal)
        np.testing.assert_array_equal(first.score_samples(normal), second.score_samples(normal))


class TestCalibration:
    def test_risk_is_bounded_in_unit_interval(self):
        normal, _ = build_synthetic_traffic(n_normal=200, n_attack=100, seed=3)
        detector = AnomalyDetector("c.joblib", seed=7).train(normal)
        risks = detector.risk(normal)
        assert (risks >= 0.0).all() and (risks <= 1.0).all()

    def test_attack_cloud_clears_the_normal_tail(self):
        normal, attack = build_synthetic_traffic(n_normal=300, n_attack=150, seed=3)
        detector = AnomalyDetector("d.joblib", seed=7).train(normal)
        assert np.median(detector.risk(attack)) > np.percentile(detector.risk(normal), 90)

    def test_evaluate_reports_recall_and_budget(self):
        normal, attack = build_synthetic_traffic(n_normal=300, n_attack=150, seed=7)
        detector = AnomalyDetector("e.joblib", seed=7).train(normal)
        metrics = evaluate(detector, normal, attack, threshold=0.90)
        # Calibration tail property: at threshold 0.90 at most ~10% of normal
        # traffic sits above it, while the attack cloud should land well past it.
        assert metrics["threshold"] == 0.90
        assert metrics["recall"] > 0.80
        assert metrics["false_positive_rate"] <= 0.12