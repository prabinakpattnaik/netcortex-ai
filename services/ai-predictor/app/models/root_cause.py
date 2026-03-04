"""Root cause analysis via Pearson correlation on metric windows.

Supports two modes:
  1. **History mode** (primary) — receives a full InfluxDB time-series window
     and computes correlations on the historical data.
  2. **Point mode** (fallback) — uses an in-memory buffer for manual REST
     API calls.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

METRIC_KEYS = [
    "cpu_usage",
    "mem_usage",
    "disk_usage",
    "net_rx",
    "net_tx",
    "net_errors",
    "temperature_max",
    "bgp_down_count",
]

# Human-readable recommendations keyed by the dominant root-cause metric.
RECOMMENDATIONS: dict[str, str] = {
    "cpu_usage": "Investigate CPU cooling system and running processes",
    "mem_usage": "Check for memory leaks and consider scaling memory",
    "disk_usage": "Free disk space or expand storage volume",
    "net_rx": "Investigate inbound network traffic for anomalies",
    "net_tx": "Investigate outbound network traffic for anomalies",
    "net_errors": "Check network interface health and cable connections",
    "temperature_max": "Check fan status and airflow; potential switch overheat",
    "bgp_down_count": "Check BGP sessions; possible peer or link failure",
}


class RootCauseAnalyzer:
    """Correlation-based root cause analysis for detected anomalies.

    In history mode, works directly on the InfluxDB time-series window.
    In point mode, maintains an internal buffer for REST API calls.
    """

    def __init__(self, agent_id: str, window_size: int | None = None) -> None:
        self.agent_id = agent_id
        self.window_size = window_size or settings.SLIDING_WINDOW_SIZE
        self.buffer: deque[dict[str, float]] = deque(maxlen=self.window_size)

    # ------------------------------------------------------------------
    # History mode — primary pipeline (InfluxDB data)
    # ------------------------------------------------------------------

    def analyze_with_history(
        self,
        history: list[dict[str, Any]],
        affected_metrics: list[str] | None = None,
    ) -> dict[str, Any]:
        """Perform RCA using the full InfluxDB time-series window.

        Parameters
        ----------
        history:
            List of flattened feature dicts from InfluxDB, sorted by time.
        affected_metrics:
            Optional list of metric names flagged as anomalous.

        Returns
        -------
        dict with ``root_causes``, ``recommendation``, and ``history_points``.
        """
        if len(history) < settings.INFLUX_MIN_HISTORY_POINTS:
            return {
                "root_causes": [],
                "recommendation": "Insufficient historical data for root cause analysis",
                "history_points": len(history),
            }

        data = self._history_to_array(history)
        correlations = self._compute_correlations(data, affected_metrics)

        # Sort by absolute correlation descending
        correlations.sort(key=lambda x: abs(x["correlation"]), reverse=True)

        top_metric = correlations[0]["metric"] if correlations else None
        recommendation = RECOMMENDATIONS.get(
            top_metric, "Review system metrics and logs for anomalies"
        )

        return {
            "root_causes": correlations,
            "recommendation": recommendation,
            "history_points": len(history),
        }

    # ------------------------------------------------------------------
    # Point mode — fallback for REST API
    # ------------------------------------------------------------------

    def add_sample(self, metrics: dict[str, Any]) -> None:
        """Append a metrics sample to the internal buffer."""
        sample = {}
        for key in METRIC_KEYS:
            val = metrics.get(key, 0.0)
            sample[key] = float(val) if val is not None else 0.0
        self.buffer.append(sample)

    def analyze(
        self, affected_metrics: list[str] | None = None
    ) -> dict[str, Any]:
        """Perform RCA using the in-memory buffer (point mode)."""
        if len(self.buffer) < 10:
            return {
                "root_causes": [],
                "recommendation": "Insufficient data for root cause analysis",
            }

        data = self._buffer_to_array()
        correlations = self._compute_correlations(data, affected_metrics)
        correlations.sort(key=lambda x: abs(x["correlation"]), reverse=True)

        top_metric = correlations[0]["metric"] if correlations else None
        recommendation = RECOMMENDATIONS.get(
            top_metric, "Review system metrics and logs for anomalies"
        )

        return {
            "root_causes": correlations,
            "recommendation": recommendation,
        }

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "buffer_size": len(self.buffer),
            "window_size": self.window_size,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _history_to_array(history: list[dict[str, Any]]) -> np.ndarray:
        """Convert InfluxDB history list into a 2-D numpy array."""
        rows = []
        for h in history:
            rows.append([float(h.get(k, 0.0) or 0.0) for k in METRIC_KEYS])
        return np.array(rows, dtype=np.float64)

    def _buffer_to_array(self) -> np.ndarray:
        """Convert the deque of dicts into a 2-D numpy array."""
        rows = []
        for sample in self.buffer:
            rows.append([sample[k] for k in METRIC_KEYS])
        return np.array(rows, dtype=np.float64)

    @staticmethod
    def _compute_correlations(
        data: np.ndarray,
        affected_metrics: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Compute Pearson correlation of each metric with an anomaly signal.

        The anomaly signal is the row-wise sum of absolute z-scores,
        optionally restricted to the ``affected_metrics`` columns.
        """
        n_samples, n_features = data.shape

        means = data.mean(axis=0)
        stds = data.std(axis=0)
        stds[stds == 0] = 1.0
        z_scores = (data - means) / stds

        # Build anomaly signal: sum of absolute z-scores over affected cols
        if affected_metrics:
            col_indices = [
                i
                for i, k in enumerate(METRIC_KEYS)
                if k in affected_metrics
            ]
        else:
            col_indices = list(range(n_features))

        anomaly_signal = np.abs(z_scores[:, col_indices]).sum(axis=1)

        results: list[dict[str, Any]] = []
        for idx, key in enumerate(METRIC_KEYS):
            metric_col = data[:, idx]
            corr = _pearson(metric_col, anomaly_signal)

            # Determine trend direction from simple slope
            t = np.arange(n_samples, dtype=np.float64)
            slope = _simple_slope(t, metric_col)
            if slope > 0.01:
                trend = "increasing"
            elif slope < -0.01:
                trend = "decreasing"
            else:
                trend = "stable"

            results.append(
                {
                    "metric": key,
                    "correlation": round(corr, 4),
                    "trend": trend,
                }
            )

        return results


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    """Compute the Pearson correlation coefficient between two 1-D arrays."""
    if len(x) < 2:
        return 0.0
    x_mean = x.mean()
    y_mean = y.mean()
    num = ((x - x_mean) * (y - y_mean)).sum()
    denom = np.sqrt(((x - x_mean) ** 2).sum() * ((y - y_mean) ** 2).sum())
    if denom == 0:
        return 0.0
    return float(num / denom)


def _simple_slope(t: np.ndarray, y: np.ndarray) -> float:
    """Return the OLS slope of y on t."""
    n = len(t)
    if n < 2:
        return 0.0
    sum_t = t.sum()
    sum_y = y.sum()
    sum_tt = (t * t).sum()
    sum_ty = (t * y).sum()
    denom = n * sum_tt - sum_t * sum_t
    if denom == 0:
        return 0.0
    return float((n * sum_ty - sum_t * sum_y) / denom)
