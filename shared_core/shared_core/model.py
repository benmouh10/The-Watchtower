"""The unsupervised anomaly model: IsolationForest + calibrated risk.

An IsolationForest decides *how isolated* an example is, not *how likely* it
is an attack. score_samples for inliers cluster around 0 and grow more
negative for outliers, but the raw value means nothing on its own — so risk is
calibrated as the percentile rank of the current decision score against the
decision scores the model saw during training. Risk 1 means "as anomalous as
the most anomalous training example"; risk 0 means "typical".

The detector fails open: with no model loaded it answers 0.0, so a missing
artifact can never take the gateway down.
"""
from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

_DEFAULT_SEED = 7


class AnomalyDetector:
    """Thin, serializable wrapper around an IsolationForest with calibration."""

    def __init__(self, model_path: str | Path, seed: int = _DEFAULT_SEED) -> None:
        self.model_path = str(model_path)
        self.seed = seed
        self._forest: IsolationForest | None = None
        # Sorted decision scores from the training set, used to calibrate risk.
        self._train_decision: np.ndarray | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────
    @property
    def is_loaded(self) -> bool:
        return self._forest is not None and self._train_decision is not None

    def load(self) -> "AnomalyDetector":
        if not Path(self.model_path).exists():
            return self
        payload = joblib.load(self.model_path)
        self._forest = payload["forest"]
        self._train_decision = np.asarray(payload["train_decision"], dtype=np.float64)
        return self

    def persist(self) -> None:
        Path(self.model_path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "forest": self._forest,
                "train_decision": self._train_decision,
                "seed": self.seed,
            },
            self.model_path,
        )

    # ── training ───────────────────────────────────────────────────────────
    def train(self, X: np.ndarray) -> "AnomalyDetector":
        """Fit on a (usually synthetic) matrix of feature vectors."""
        forest = IsolationForest(
            n_estimators=200,
            max_samples="auto",
            contamination="auto",
            random_state=self.seed,
        )
        self._forest = forest.fit(np.asarray(X, dtype=np.float64))
        self._train_decision = np.sort(
            forest.decision_function(np.asarray(X, dtype=np.float64))
        )
        return self

    # ── serving ────────────────────────────────────────────────────────────
    def score_samples(self, X: np.ndarray) -> np.ndarray:
        """Raw sklearn isolation scores (negative = more anomalous)."""
        if not self.is_loaded:
            return np.zeros(len(X), dtype=np.float64)
        return self._forest.decision_function(np.asarray(X, dtype=np.float64))

    def risk(self, X: np.ndarray) -> np.ndarray:
        """Calibrated 0..1 anomaly risk, one per row; zeros when not loaded."""
        X = np.asarray(X, dtype=np.float64)
        if not self.is_loaded:
            return np.zeros(len(X), dtype=np.float64)
        live = self._forest.decision_function(X)
        # Risk = the share of training examples that were *less* anomalous than
        # this one (scores strictly above it). An extreme outlier sits beyond
        # nearly every training point, so almost all of them score above it and
        # risk approaches 1; a stereotypical sample is beaten only by the most
        # normal handful, so risk approaches 0.
        above = len(self._train_decision) - np.searchsorted(
            self._train_decision, live, side="right"
        )
        return (above.astype(np.float64) / max(1, len(self._train_decision))).ravel()

    def score_risk(self, features: np.ndarray) -> float:
        """0..1 risk for a single feature vector (the common call site)."""
        return float(self.risk(features.reshape(1, -1))[0])