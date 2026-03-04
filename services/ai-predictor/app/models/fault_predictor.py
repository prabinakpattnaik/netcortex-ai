"""Fault prediction via linear-regression trend analysis.

Supports two modes:
  1. **History mode** (primary) — receives timestamped data from InfluxDB and
     fits regression on real time-series.  Much more accurate than point mode.
  2. **Point mode** (fallback) — accumulates individual samples with timestamps
     for manual REST API calls.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

# Mapping: metric_name -> (threshold, fault_type)
FAULT_RULES: dict[str, tuple[float, str]] = {
    "cpu_usage": (settings.FAULT_THRESHOLD_CPU, "thermal_throttling"),
    "mem_usage": (settings.FAULT_THRESHOLD_MEMORY, "memory_exhaustion"),
    "disk_usage": (settings.FAULT_THRESHOLD_DISK, "disk_full"),
    "net_errors": (settings.FAULT_THRESHOLD_NET_ERRORS, "network_degradation"),
    "temperature_max": (settings.FAULT_THRESHOLD_TEMPERATURE, "switch_overheat"),
}


class FaultPredictor:
    """Predict upcoming faults by extrapolating metric trends.

    Uses ordinary-least-squares linear regression.  In history mode the
    regression runs on the full InfluxDB time-series window (real timestamps).
    """

    def __init__(self, agent_id: str, window_size: int | None = None) -> None:
        self.agent_id = agent_id
        self.window_size = window_size or settings.SLIDING_WINDOW_SIZE
        self.horizon_minutes = settings.FAULT_PREDICTION_HORIZON_MINUTES

        # Per-metric sliding windows for point mode.
        # Each entry is (timestamp_epoch, value).
        self.buffers: dict[str, deque[tuple[float, float]]] = {
            key: deque(maxlen=self.window_size) for key in FAULT_RULES
        }

    # ------------------------------------------------------------------
    # History mode — primary pipeline (InfluxDB data)
    # ------------------------------------------------------------------

    def predict_with_history(
        self,
        history: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Analyse historical time-series from InfluxDB and predict faults.

        Parameters
        ----------
        history:
            List of dicts from InfluxDB, each with ``timestamp`` (epoch)
            plus the metric feature keys.  Sorted by time ascending.

        Returns
        -------
        A list of fault prediction dicts.
        """
        if len(history) < settings.INFLUX_MIN_HISTORY_POINTS:
            return []

        predictions: list[dict[str, Any]] = []

        for metric_name, (threshold, fault_type) in FAULT_RULES.items():
            # Build time-series from history
            data_points: list[tuple[float, float]] = []
            for h in history:
                ts = h.get("timestamp")
                val = h.get(metric_name)
                if ts is not None and val is not None:
                    data_points.append((float(ts), float(val)))

            if len(data_points) < settings.INFLUX_MIN_HISTORY_POINTS:
                continue

            buf = deque(data_points, maxlen=len(data_points))
            result = self._extrapolate(buf, threshold, self.horizon_minutes)

            if result is not None:
                projected_value, r_squared, time_to_fault = result
                current_value = data_points[-1][1]
                confidence = max(0.0, min(1.0, r_squared))
                predictions.append(
                    {
                        "predicted_fault": fault_type,
                        "metric": metric_name,
                        "current_value": round(current_value, 2),
                        "projected_value": round(projected_value, 2),
                        "threshold": threshold,
                        "confidence": round(confidence, 4),
                        "time_to_fault_minutes": round(time_to_fault, 1),
                        "history_points": len(data_points),
                    }
                )

        return predictions

    # ------------------------------------------------------------------
    # Point mode — fallback for REST API
    # ------------------------------------------------------------------

    def predict(
        self, metrics: dict[str, Any], timestamp_epoch: float
    ) -> list[dict[str, Any]]:
        """Analyse current metrics using in-memory buffer (point mode)."""
        predictions: list[dict[str, Any]] = []

        for metric_name, (threshold, fault_type) in FAULT_RULES.items():
            value = metrics.get(metric_name)
            if value is None:
                continue

            value = float(value)
            self.buffers[metric_name].append((timestamp_epoch, value))

            buf = self.buffers[metric_name]
            if len(buf) < 10:
                continue

            result = self._extrapolate(buf, threshold, self.horizon_minutes)
            if result is not None:
                projected_value, r_squared, time_to_fault = result
                confidence = max(0.0, min(1.0, r_squared))
                predictions.append(
                    {
                        "predicted_fault": fault_type,
                        "metric": metric_name,
                        "current_value": round(value, 2),
                        "projected_value": round(projected_value, 2),
                        "threshold": threshold,
                        "confidence": round(confidence, 4),
                        "time_to_fault_minutes": round(time_to_fault, 1),
                    }
                )

        return predictions

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "buffer_sizes": {k: len(v) for k, v in self.buffers.items()},
            "horizon_minutes": self.horizon_minutes,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extrapolate(
        buf: deque[tuple[float, float]],
        threshold: float,
        horizon_minutes: int,
    ) -> tuple[float, float, float] | None:
        """Fit linear regression and check whether the threshold is breached.

        Returns ``(projected_value, r_squared, time_to_fault_minutes)`` if a
        breach is predicted within the horizon, otherwise ``None``.
        """
        data = np.array(list(buf))
        t = data[:, 0]  # timestamps (seconds)
        y = data[:, 1]  # metric values

        # Normalise time to start at 0 (seconds)
        t = t - t[0]

        n = len(t)
        sum_t = t.sum()
        sum_y = y.sum()
        sum_tt = (t * t).sum()
        sum_ty = (t * y).sum()

        denom = n * sum_tt - sum_t * sum_t
        if denom == 0:
            return None

        slope = (n * sum_ty - sum_t * sum_y) / denom
        intercept = (sum_y - slope * sum_t) / n

        # Only predict faults when the trend is *increasing* toward the
        # threshold.
        if slope <= 0:
            return None

        # R-squared (coefficient of determination)
        y_pred = slope * t + intercept
        ss_res = ((y - y_pred) ** 2).sum()
        ss_tot = ((y - y.mean()) ** 2).sum()
        r_squared = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0.0

        # Project to horizon_minutes into the future (from the last sample)
        horizon_seconds = horizon_minutes * 60
        future_t = t[-1] + horizon_seconds
        projected_value = slope * future_t + intercept

        if projected_value >= threshold:
            # Estimate time to threshold from the last observed value
            current_value = y[-1]
            if slope > 0 and current_value < threshold:
                time_to_fault_seconds = (threshold - intercept - slope * t[-1]) / slope
                time_to_fault_minutes = time_to_fault_seconds / 60.0
            else:
                time_to_fault_minutes = 0.0

            return projected_value, r_squared, max(0.0, time_to_fault_minutes)

        return None
