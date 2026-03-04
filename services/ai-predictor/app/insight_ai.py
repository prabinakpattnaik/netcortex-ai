"""InsightAI - ML model orchestrator.

Manages per-agent model instances (AnomalyDetector, FaultPredictor,
RootCauseAnalyzer, BayesianDiagnostics) and exposes two pipeline
entry points:

  1. ``process_with_history`` (primary) — receives historical data from
     InfluxDB and runs the full ML pipeline on real time-series.
  2. ``process_metrics`` (fallback) — single-point pipeline for manual
     REST API calls when InfluxDB data is not available.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.models.anomaly_detector import AnomalyDetector
from app.models.bayesian_diagnosis import BayesianDiagnostics
from app.models.fault_predictor import FaultPredictor
from app.models.root_cause import RootCauseAnalyzer

logger = logging.getLogger(__name__)


class InsightAI:
    """Orchestrates all ML models on a per-agent basis.

    Each unique ``agent_id`` gets its own set of model instances so that
    training state is isolated per device.
    """

    def __init__(self) -> None:
        self._anomaly_detectors: dict[str, AnomalyDetector] = {}
        self._fault_predictors: dict[str, FaultPredictor] = {}
        self._root_cause_analyzers: dict[str, RootCauseAnalyzer] = {}
        self._bayesian_diagnostics: dict[str, BayesianDiagnostics] = {}
        self._processed_count: int = 0
        self._prediction_count: int = 0

    # ------------------------------------------------------------------
    # Model instance accessors (lazy creation)
    # ------------------------------------------------------------------

    def _get_anomaly_detector(self, agent_id: str) -> AnomalyDetector:
        if agent_id not in self._anomaly_detectors:
            self._anomaly_detectors[agent_id] = AnomalyDetector(agent_id)
        return self._anomaly_detectors[agent_id]

    def _get_fault_predictor(self, agent_id: str) -> FaultPredictor:
        if agent_id not in self._fault_predictors:
            self._fault_predictors[agent_id] = FaultPredictor(agent_id)
        return self._fault_predictors[agent_id]

    def _get_root_cause_analyzer(self, agent_id: str) -> RootCauseAnalyzer:
        if agent_id not in self._root_cause_analyzers:
            self._root_cause_analyzers[agent_id] = RootCauseAnalyzer(agent_id)
        return self._root_cause_analyzers[agent_id]

    def _get_bayesian_diagnostics(self, agent_id: str) -> BayesianDiagnostics:
        if agent_id not in self._bayesian_diagnostics:
            self._bayesian_diagnostics[agent_id] = BayesianDiagnostics(agent_id)
        return self._bayesian_diagnostics[agent_id]

    # ------------------------------------------------------------------
    # PRIMARY: History-based pipeline (InfluxDB data)
    # ------------------------------------------------------------------

    def process_with_history(
        self,
        agent_id: str,
        timestamp_str: str,
        current_flat: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Run the full ML pipeline using historical data from InfluxDB.

        Parameters
        ----------
        agent_id:
            Unique agent identifier (e.g. "sonic-leaf-01").
        timestamp_str:
            ISO-8601 timestamp of the triggering Kafka message.
        current_flat:
            The latest flattened metrics (for anomaly classification).
        history:
            Time-sorted list of flattened feature dicts from InfluxDB.
            Each dict has: timestamp, cpu_usage, mem_usage, etc.

        Returns
        -------
        List of prediction dicts ready for Kafka publication.
        """
        self._processed_count += 1
        predictions: list[dict[str, Any]] = []

        # --- 1. Anomaly Detection (on full InfluxDB history) ---
        detector = self._get_anomaly_detector(agent_id)
        anomaly_result = detector.detect_with_history(current_flat, history)

        if anomaly_result["is_anomaly"]:
            predictions.append(self._build_prediction(
                agent_id=agent_id,
                timestamp=timestamp_str,
                model="anomaly_detection",
                result=anomaly_result,
            ))

            # --- 2. Root Cause Analysis (on full history, when anomaly) ---
            rca = self._get_root_cause_analyzer(agent_id)
            rca_result = rca.analyze_with_history(
                history,
                affected_metrics=anomaly_result.get("affected_metrics"),
            )
            if rca_result["root_causes"]:
                predictions.append(self._build_prediction(
                    agent_id=agent_id,
                    timestamp=timestamp_str,
                    model="root_cause_analysis",
                    result=rca_result,
                ))

        # --- 3. Fault Prediction (always, on full history) ---
        # Runs regardless of anomaly, because trend extrapolation
        # can predict a future fault even from normal-looking data.
        predictor = self._get_fault_predictor(agent_id)
        fault_results = predictor.predict_with_history(history)
        for fault in fault_results:
            predictions.append(self._build_prediction(
                agent_id=agent_id,
                timestamp=timestamp_str,
                model="fault_prediction",
                result=fault,
            ))

        # --- 4. Bayesian Diagnosis (always, on full history) ---
        # Probabilistic causal analysis: computes posterior fault
        # probabilities using a Bayesian Network with CPTs learned
        # from historical data.
        bayesian = self._get_bayesian_diagnostics(agent_id)
        bayesian_result = bayesian.diagnose_with_history(current_flat, history)
        if bayesian_result.get("most_likely_fault") is not None:
            predictions.append(self._build_prediction(
                agent_id=agent_id,
                timestamp=timestamp_str,
                model="bayesian_diagnosis",
                result=bayesian_result,
            ))

        self._prediction_count += len(predictions)

        if predictions:
            logger.info(
                "Agent %s: %d predictions from %d history points",
                agent_id,
                len(predictions),
                len(history),
            )

        return predictions

    # ------------------------------------------------------------------
    # FALLBACK: Single-point pipeline (for REST API manual calls)
    # ------------------------------------------------------------------

    def process_metrics(self, metrics: dict[str, Any]) -> list[dict[str, Any]]:
        """Run the full ML pipeline on a single metrics message (point mode).

        This is the fallback used by manual REST API calls when InfluxDB
        history is not available.
        """
        agent_id: str = metrics.get("agent_id", "unknown")
        timestamp_str: str = metrics.get(
            "timestamp", datetime.now(timezone.utc).isoformat()
        )
        timestamp_epoch = self._parse_timestamp(timestamp_str)

        self._processed_count += 1
        predictions: list[dict[str, Any]] = []

        flat = self._flatten_metrics(metrics)

        # --- 1. Anomaly Detection ---
        detector = self._get_anomaly_detector(agent_id)
        anomaly_result = detector.detect(flat)

        # Always feed the RCA buffer
        rca = self._get_root_cause_analyzer(agent_id)
        rca.add_sample(flat)

        if anomaly_result["is_anomaly"]:
            predictions.append(self._build_prediction(
                agent_id=agent_id,
                timestamp=timestamp_str,
                model="anomaly_detection",
                result=anomaly_result,
            ))

            # --- 2. Fault Prediction (when anomaly) ---
            predictor = self._get_fault_predictor(agent_id)
            fault_results = predictor.predict(flat, timestamp_epoch)
            for fault in fault_results:
                predictions.append(self._build_prediction(
                    agent_id=agent_id,
                    timestamp=timestamp_str,
                    model="fault_prediction",
                    result=fault,
                ))

            # --- 3. Root Cause Analysis ---
            rca_result = rca.analyze(
                affected_metrics=anomaly_result.get("affected_metrics")
            )
            if rca_result["root_causes"]:
                predictions.append(self._build_prediction(
                    agent_id=agent_id,
                    timestamp=timestamp_str,
                    model="root_cause_analysis",
                    result=rca_result,
                ))
        else:
            # Still track trends in fault predictor
            predictor = self._get_fault_predictor(agent_id)
            fault_results = predictor.predict(flat, timestamp_epoch)
            for fault in fault_results:
                predictions.append(self._build_prediction(
                    agent_id=agent_id,
                    timestamp=timestamp_str,
                    model="fault_prediction",
                    result=fault,
                ))

        self._prediction_count += len(predictions)
        return predictions

    # ------------------------------------------------------------------
    # Manual inference (for the REST API)
    # ------------------------------------------------------------------

    def run_anomaly_detection(
        self, agent_id: str, metrics: dict[str, Any]
    ) -> dict[str, Any]:
        """Run anomaly detection on demand for a single sample."""
        flat = self._flatten_metrics(metrics)
        detector = self._get_anomaly_detector(agent_id)
        return detector.detect(flat)

    def run_fault_prediction(
        self,
        agent_id: str,
        metrics: dict[str, Any],
        timestamp_epoch: float | None = None,
    ) -> list[dict[str, Any]]:
        """Run fault prediction on demand."""
        flat = self._flatten_metrics(metrics)
        ts = timestamp_epoch or datetime.now(timezone.utc).timestamp()
        predictor = self._get_fault_predictor(agent_id)
        return predictor.predict(flat, ts)

    def run_root_cause_analysis(
        self,
        agent_id: str,
        affected_metrics: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run RCA on demand using the existing buffer for an agent."""
        rca = self._get_root_cause_analyzer(agent_id)
        return rca.analyze(affected_metrics)

    def run_bayesian_diagnosis(
        self,
        agent_id: str,
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """Run Bayesian fault diagnosis on demand (point mode)."""
        flat = self._flatten_metrics(metrics)
        bayesian = self._get_bayesian_diagnostics(agent_id)
        return bayesian.diagnose(flat)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return an overview of all managed models and counters."""
        agents = (
            set(self._anomaly_detectors.keys())
            | set(self._fault_predictors.keys())
            | set(self._root_cause_analyzers.keys())
            | set(self._bayesian_diagnostics.keys())
        )

        per_agent = {}
        for agent_id in sorted(agents):
            per_agent[agent_id] = {
                "anomaly_detector": (
                    self._anomaly_detectors[agent_id].status()
                    if agent_id in self._anomaly_detectors
                    else None
                ),
                "fault_predictor": (
                    self._fault_predictors[agent_id].status()
                    if agent_id in self._fault_predictors
                    else None
                ),
                "root_cause_analyzer": (
                    self._root_cause_analyzers[agent_id].status()
                    if agent_id in self._root_cause_analyzers
                    else None
                ),
                "bayesian_diagnostics": (
                    self._bayesian_diagnostics[agent_id].status()
                    if agent_id in self._bayesian_diagnostics
                    else None
                ),
            }

        return {
            "total_agents": len(agents),
            "processed_count": self._processed_count,
            "prediction_count": self._prediction_count,
            "agents": per_agent,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _flatten_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
        """Flatten nested metric groups into a flat key-value dict.

        The consumed Kafka message may have nested structures like:
        ``{"cpu": {"usage": 78.5}, "memory": {"usage": 65.2}, ...}``

        This method produces:
        ``{"cpu_usage": 78.5, "mem_usage": 65.2, ...}``
        """
        flat: dict[str, Any] = {}

        # Copy top-level scalars
        for key, val in metrics.items():
            if not isinstance(val, (dict, list)):
                flat[key] = val

        # CPU
        cpu = metrics.get("cpu", {})
        if isinstance(cpu, dict):
            flat["cpu_usage"] = cpu.get("usage", cpu.get("usage_percent", 0.0))

        # Memory
        mem = metrics.get("memory", {})
        if isinstance(mem, dict):
            flat["mem_usage"] = mem.get("usage", mem.get("usage_percent", 0.0))

        # Disk (may be a list of devices or a single dict)
        disk = metrics.get("disk", [])
        if isinstance(disk, list) and disk:
            usages = [d.get("usage_percent", 0.0) for d in disk if isinstance(d, dict)]
            flat["disk_usage"] = sum(usages) / len(usages) if usages else 0.0
        elif isinstance(disk, dict):
            flat["disk_usage"] = disk.get("usage", disk.get("usage_percent", 0.0))

        # Network (may be a list of interfaces or a single dict)
        net = metrics.get("network", [])
        if isinstance(net, list) and net:
            flat["net_rx"] = sum(n.get("rx_bytes", 0) for n in net if isinstance(n, dict))
            flat["net_tx"] = sum(n.get("tx_bytes", 0) for n in net if isinstance(n, dict))
            flat["net_errors"] = sum(
                n.get("rx_errors", 0) + n.get("tx_errors", 0)
                for n in net if isinstance(n, dict)
            )
        elif isinstance(net, dict):
            flat["net_rx"] = net.get("rx_bytes", net.get("rx", 0.0))
            flat["net_tx"] = net.get("tx_bytes", net.get("tx", 0.0))
            flat["net_errors"] = net.get("errors", 0.0)

        # SONiC: Hardware temperatures
        hw = metrics.get("hardware")
        if isinstance(hw, dict):
            temps = hw.get("temperatures", [])
            if isinstance(temps, list) and temps:
                flat["temperature_max"] = max(
                    (t.get("current", 0.0) for t in temps if isinstance(t, dict)),
                    default=0.0,
                )

        # SONiC: BGP session state
        bgp = metrics.get("bgp", [])
        if isinstance(bgp, list) and bgp:
            down = sum(
                1 for b in bgp
                if isinstance(b, dict) and b.get("state") != "Established"
            )
            flat["bgp_down_count"] = float(down)

        # Ensure all required keys exist
        for key in [
            "cpu_usage",
            "mem_usage",
            "disk_usage",
            "net_rx",
            "net_tx",
            "net_errors",
            "temperature_max",
            "bgp_down_count",
        ]:
            flat.setdefault(key, 0.0)

        return flat

    @staticmethod
    def _build_prediction(
        agent_id: str,
        timestamp: str,
        model: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Create a prediction envelope matching the published schema."""
        return {
            "prediction_id": str(uuid.uuid4()),
            "timestamp": timestamp,
            "agent_id": agent_id,
            "model": model,
            "result": result,
        }

    @staticmethod
    def _parse_timestamp(ts: str) -> float:
        """Parse an ISO-8601 timestamp string to a Unix epoch float."""
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.timestamp()
        except (ValueError, AttributeError):
            return datetime.now(timezone.utc).timestamp()
