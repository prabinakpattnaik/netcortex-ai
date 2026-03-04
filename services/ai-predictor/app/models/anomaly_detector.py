"""Anomaly detection using scikit-learn Isolation Forest.

Supports two modes:
  1. **History mode** (primary) — receives a full time-series window from
     InfluxDB and trains/predicts on the complete historical dataset.
  2. **Point mode** (fallback) — accumulates samples in an in-memory sliding
     window when InfluxDB history is not available (e.g., manual REST calls).
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest

from app.config import settings

logger = logging.getLogger(__name__)

# Feature column order expected by the model
FEATURE_KEYS = [
    "cpu_usage",
    "mem_usage",
    "disk_usage",
    "net_rx",
    "net_tx",
    "net_errors",
    "temperature_max",
    "bgp_down_count",
]


class AnomalyDetector:
    """Per-agent Isolation Forest anomaly detector.

    In **history mode**, the detector receives a full window of historical
    data points from InfluxDB.  It trains on the older portion and predicts
    on the most recent sample.

    In **point mode** (for manual REST API calls), it maintains an in-memory
    sliding window buffer just like before.
    """

    def __init__(self, agent_id: str, window_size: int | None = None) -> None:
        self.agent_id = agent_id
        self.window_size = window_size or settings.SLIDING_WINDOW_SIZE
        self.buffer: deque[np.ndarray] = deque(maxlen=self.window_size)
        self.model: IsolationForest | None = None
        self.is_trained: bool = False
        self._samples_since_retrain: int = 0
        self._total_processed: int = 0

    # ------------------------------------------------------------------
    # History mode — primary pipeline (InfluxDB data)
    # ------------------------------------------------------------------

    def detect_with_history(
        self,
        current_flat: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Run anomaly detection using a full historical window from InfluxDB.

        Parameters
        ----------
        current_flat:
            The latest flattened metrics (used as the sample to classify).
        history:
            List of flattened feature dicts from InfluxDB, sorted by time.
            Each dict must contain the keys listed in FEATURE_KEYS.

        Returns
        -------
        dict with ``is_anomaly``, ``anomaly_score``, ``affected_metrics``,
        and ``history_points`` (number of training samples).
        """
        self._total_processed += 1

        # Build feature matrix from history
        history_features = []
        for h in history:
            features = self._extract_features(h)
            history_features.append(features)

        if len(history_features) < settings.INFLUX_MIN_HISTORY_POINTS:
            return {
                "is_anomaly": False,
                "anomaly_score": 0.0,
                "affected_metrics": [],
                "history_points": len(history_features),
            }

        # Train on full historical window
        train_data = np.array(history_features)
        self.model = IsolationForest(
            contamination=settings.ANOMALY_CONTAMINATION,
            n_estimators=100,
            random_state=42,
            n_jobs=-1,
        )
        self.model.fit(train_data)
        self.is_trained = True

        logger.debug(
            "AnomalyDetector trained on %d historical points for agent=%s",
            len(train_data),
            self.agent_id,
        )

        # Predict on the latest sample
        current_features = self._extract_features(current_flat)
        result = self._predict(current_features, current_flat, train_data)
        result["history_points"] = len(train_data)
        return result

    # ------------------------------------------------------------------
    # Point mode — fallback for REST API manual inference
    # ------------------------------------------------------------------

    def detect(self, metrics: dict[str, Any]) -> dict[str, Any]:
        """Run anomaly detection on a single metrics sample (point mode).

        Uses the in-memory sliding window buffer.  This is the fallback
        for manual REST API calls when InfluxDB history is not available.
        """
        features = self._extract_features(metrics)
        self.buffer.append(features)
        self._samples_since_retrain += 1
        self._total_processed += 1

        # Train / retrain when enough data has accumulated
        if not self.is_trained and len(self.buffer) >= self.window_size:
            self._train()
        elif self.is_trained and self._samples_since_retrain >= self.window_size:
            self._train()

        if not self.is_trained:
            return {
                "is_anomaly": False,
                "anomaly_score": 0.0,
                "affected_metrics": [],
            }

        data = np.array(list(self.buffer))
        return self._predict(features, metrics, data)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return runtime status information for this detector."""
        return {
            "agent_id": self.agent_id,
            "is_trained": self.is_trained,
            "buffer_size": len(self.buffer),
            "window_size": self.window_size,
            "total_processed": self._total_processed,
            "samples_since_retrain": self._samples_since_retrain,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_features(metrics: dict[str, Any]) -> np.ndarray:
        """Pull numeric features from a metrics dict into a numpy array."""
        values = []
        for key in FEATURE_KEYS:
            val = metrics.get(key, 0.0)
            values.append(float(val) if val is not None else 0.0)
        return np.array(values, dtype=np.float64)

    def _train(self) -> None:
        """Fit (or re-fit) the Isolation Forest on the in-memory buffer."""
        data = np.array(list(self.buffer))
        self.model = IsolationForest(
            contamination=settings.ANOMALY_CONTAMINATION,
            n_estimators=100,
            random_state=42,
            n_jobs=-1,
        )
        self.model.fit(data)
        self.is_trained = True
        self._samples_since_retrain = 0
        logger.info(
            "AnomalyDetector trained for agent=%s with %d samples (point mode)",
            self.agent_id,
            len(data),
        )

    def _predict(
        self,
        features: np.ndarray,
        metrics: dict[str, Any],
        training_data: np.ndarray,
    ) -> dict[str, Any]:
        """Score one sample and determine which metrics are most anomalous."""
        assert self.model is not None

        sample = features.reshape(1, -1)
        raw_score: float = float(self.model.decision_function(sample)[0])
        prediction: int = int(self.model.predict(sample)[0])
        is_anomaly = prediction == -1

        # Identify affected metrics by comparing each feature to the
        # mean / std of the training data.
        affected: list[str] = []
        if is_anomaly:
            means = training_data.mean(axis=0)
            stds = training_data.std(axis=0)
            stds[stds == 0] = 1.0  # avoid division by zero
            z_scores = np.abs((features - means) / stds)
            for idx, key in enumerate(FEATURE_KEYS):
                if z_scores[idx] > 2.0:
                    affected.append(key)

        return {
            "is_anomaly": is_anomaly,
            "anomaly_score": round(raw_score, 4),
            "affected_metrics": affected,
        }
