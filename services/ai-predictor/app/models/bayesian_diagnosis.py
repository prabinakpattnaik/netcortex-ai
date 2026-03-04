"""Bayesian Network for probabilistic fault diagnosis.

Models causal dependencies between datacenter metrics and fault states
using conditional probability tables (CPTs).  Given observed metric values,
computes posterior probabilities of each fault type.

Since all metric nodes are always observed during inference, fault
probabilities reduce to simple CPT lookups — no complex marginalisation
or Variable Elimination is needed.

Supports two modes:
  1. **History mode** (primary) — learns CPTs from InfluxDB historical data
     via Maximum-Likelihood Estimation with Laplace smoothing.
  2. **Point mode** (fallback) — uses expert-defined CPTs when insufficient
     history is available.

Implementation uses only numpy (no external BN library required) because
the network structure is fixed and all evidence nodes are always observed.

Network Structure (DAG):
    ┌───────────────────────────────────────────────────────────────────┐
    │                    METRIC NODES (observed)                        │
    │                                                                   │
    │   cpu_usage   mem_usage   disk_usage   net_errors                │
    │   temperature_max         bgp_down_count                         │
    └───────┬──────────┬──────────┬──────────┬──────────┬──────────┬───┘
            │          │          │          │          │          │
    ┌───────▼──────────▼──────────▼──────────▼──────────▼──────────▼───┐
    │                     FAULT NODES (inferred)                        │
    │                                                                   │
    │   thermal_throttling   ← cpu_usage, temperature_max               │
    │   memory_exhaustion    ← mem_usage, cpu_usage                     │
    │   disk_full            ← disk_usage                               │
    │   network_degradation  ← net_errors, bgp_down_count              │
    │   switch_overheat      ← temperature_max                          │
    │   bgp_failure          ← bgp_down_count, net_errors              │
    └───────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

# ─── Discrete state labels ────────────────────────────────────────────
STATES = ("low", "normal", "elevated", "critical")
N_STATES = len(STATES)  # 4

# ─── Discretisation thresholds ────────────────────────────────────────
# metric → [(upper_bound, state_label), ...]  in ascending order
DISCRETISATION: dict[str, list[tuple[float, str]]] = {
    "cpu_usage": [
        (30.0, "low"),
        (70.0, "normal"),
        (90.0, "elevated"),
        (float("inf"), "critical"),
    ],
    "mem_usage": [
        (30.0, "low"),
        (70.0, "normal"),
        (90.0, "elevated"),
        (float("inf"), "critical"),
    ],
    "disk_usage": [
        (40.0, "low"),
        (75.0, "normal"),
        (90.0, "elevated"),
        (float("inf"), "critical"),
    ],
    "net_errors": [
        (1.0, "low"),
        (10.0, "normal"),
        (30.0, "elevated"),
        (float("inf"), "critical"),
    ],
    "temperature_max": [
        (40.0, "low"),
        (65.0, "normal"),
        (80.0, "elevated"),
        (float("inf"), "critical"),
    ],
    "bgp_down_count": [
        (0.5, "low"),       # 0 sessions down
        (1.5, "normal"),    # 1 session down
        (3.5, "elevated"),  # 2–3 sessions down
        (float("inf"), "critical"),  # 4+ sessions down
    ],
}

# ─── Fault network structure ─────────────────────────────────────────
# Each fault node and the metric(s) that are its parents in the DAG.
# Order of parents matters: it defines the CPT array axis ordering.
FAULT_PARENTS: dict[str, list[str]] = {
    "thermal_throttling":  ["cpu_usage", "temperature_max"],
    "memory_exhaustion":   ["mem_usage", "cpu_usage"],
    "disk_full":           ["disk_usage"],
    "network_degradation": ["net_errors", "bgp_down_count"],
    "switch_overheat":     ["temperature_max"],
    "bgp_failure":         ["bgp_down_count", "net_errors"],
}

# Human-readable descriptions
FAULT_DESCRIPTIONS: dict[str, str] = {
    "thermal_throttling": (
        "CPU thermal throttling due to sustained high temperature or load"
    ),
    "memory_exhaustion": (
        "Memory exhaustion from leaks, OOM conditions, or excessive allocation"
    ),
    "disk_full": "Disk capacity nearing critical threshold",
    "network_degradation": (
        "Network performance degradation from packet errors or BGP instability"
    ),
    "switch_overheat": "Switch/ASIC overheating risk requiring cooling intervention",
    "bgp_failure": (
        "BGP session instability or complete failure affecting routing"
    ),
}

# Actionable recommendations per fault
FAULT_RECOMMENDATIONS: dict[str, str] = {
    "thermal_throttling": (
        "Check cooling system, reduce CPU-intensive processes, inspect fan status"
    ),
    "memory_exhaustion": (
        "Investigate memory leaks, check OOM-killer logs, consider scaling memory"
    ),
    "disk_full": (
        "Free disk space, expand storage volume, check log rotation"
    ),
    "network_degradation": (
        "Check cable connections, inspect NIC health, review BGP peer config"
    ),
    "switch_overheat": (
        "Inspect fan trays, verify airflow path, review ambient temperature"
    ),
    "bgp_failure": (
        "Check BGP peer connectivity, review route advertisements, inspect links"
    ),
}


class BayesianDiagnostics:
    """Bayesian Network for probabilistic fault diagnosis.

    Uses a directed acyclic graph encoding expert knowledge about causal
    relationships in datacenter infrastructure.  Metrics are discretised
    into 4 states (low / normal / elevated / critical) and the network
    computes posterior fault probabilities via CPT lookup.

    In **history mode** CPTs are learned from InfluxDB data via MLE with
    Laplace smoothing.  In **point mode** expert-defined CPTs are used.
    """

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        # CPTs: fault_name → numpy array of P(fault=present | parents)
        #   1 parent:  shape (N_STATES,)
        #   2 parents: shape (N_STATES, N_STATES)
        self._cpts: dict[str, np.ndarray] = {}
        self._is_fitted: bool = False
        self._training_points: int = 0

    # ------------------------------------------------------------------
    # History mode — primary pipeline (InfluxDB data)
    # ------------------------------------------------------------------

    def diagnose_with_history(
        self,
        current_flat: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Run Bayesian diagnosis using historical data from InfluxDB.

        Parameters
        ----------
        current_flat:
            The latest flattened metrics dict.
        history:
            Time-sorted list of flattened feature dicts from InfluxDB.

        Returns
        -------
        dict with ``fault_probabilities``, ``most_likely_fault``,
        ``recommendation``, ``metric_states``, and ``history_points``.
        """
        if len(history) < settings.INFLUX_MIN_HISTORY_POINTS:
            return {
                "fault_probabilities": {},
                "most_likely_fault": None,
                "most_likely_probability": 0.0,
                "recommendation": (
                    "Insufficient historical data for Bayesian diagnosis"
                ),
                "metric_states": {},
                "history_points": len(history),
            }

        # Learn CPTs from historical data
        self._learn_cpts(history)

        # Discretise current observation and run inference
        evidence = self._discretise(current_flat)
        return self._infer(evidence, len(history))

    # ------------------------------------------------------------------
    # Point mode — fallback for REST API
    # ------------------------------------------------------------------

    def diagnose(self, current_flat: dict[str, Any]) -> dict[str, Any]:
        """Run Bayesian diagnosis using expert-defined CPTs (point mode).

        Parameters
        ----------
        current_flat:
            The latest flattened metrics dict.

        Returns
        -------
        dict with fault probabilities and recommendations.
        """
        if not self._is_fitted:
            self._build_expert_cpts()

        evidence = self._discretise(current_flat)
        return self._infer(evidence, 0)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "is_fitted": self._is_fitted,
            "training_points": self._training_points,
            "model_type": "BayesianNetwork (numpy, expert DAG)",
        }

    # ------------------------------------------------------------------
    # Internal: discretisation
    # ------------------------------------------------------------------

    @staticmethod
    def _discretise_value(metric_name: str, value: float) -> int:
        """Convert a continuous metric value to a discrete state index.

        Returns
        -------
        int — index into STATES: 0=low, 1=normal, 2=elevated, 3=critical
        """
        thresholds = DISCRETISATION.get(metric_name)
        if thresholds is None:
            return 1  # default: normal
        for idx, (upper_bound, _label) in enumerate(thresholds):
            if value <= upper_bound:
                return idx
        return len(thresholds) - 1

    @classmethod
    def _discretise(cls, flat: dict[str, Any]) -> dict[str, int]:
        """Discretise a full metrics observation into state indices."""
        return {
            metric: cls._discretise_value(
                metric, float(flat.get(metric, 0.0) or 0.0)
            )
            for metric in DISCRETISATION
        }

    # ------------------------------------------------------------------
    # Internal: derive fault labels from discretised metrics
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_fault_labels(disc: dict[str, int]) -> dict[str, int]:
        """Derive binary fault labels (0/1) from discretised metric states.

        These are used as training targets when learning CPTs from data.
        Rules encode domain knowledge:
          - A fault is ``1`` when the driving metrics are elevated/critical.
        """
        return {
            "thermal_throttling": int(
                disc["cpu_usage"] >= 3
                or (disc["cpu_usage"] >= 2 and disc["temperature_max"] >= 2)
            ),
            "memory_exhaustion": int(
                disc["mem_usage"] >= 3
                or (disc["mem_usage"] >= 2 and disc["cpu_usage"] >= 2)
            ),
            "disk_full": int(disc["disk_usage"] >= 3),
            "network_degradation": int(
                disc["net_errors"] >= 2 or disc["bgp_down_count"] >= 2
            ),
            "switch_overheat": int(disc["temperature_max"] >= 3),
            "bgp_failure": int(
                disc["bgp_down_count"] >= 3
                or (disc["bgp_down_count"] >= 2 and disc["net_errors"] >= 2)
            ),
        }

    # ------------------------------------------------------------------
    # Internal: learn CPTs from data (MLE + Laplace smoothing)
    # ------------------------------------------------------------------

    def _learn_cpts(self, history: list[dict[str, Any]]) -> None:
        """Learn CPTs from discretised historical data.

        Uses Maximum-Likelihood Estimation with Laplace smoothing
        (alpha = 1.0) to avoid zero-probability entries.
        """
        alpha = 1.0  # Laplace smoothing parameter

        # Discretise every historical data point
        disc_history = [self._discretise(h) for h in history]
        fault_labels = [self._derive_fault_labels(d) for d in disc_history]

        for fault_name, parent_metrics in FAULT_PARENTS.items():
            n_parents = len(parent_metrics)

            if n_parents == 1:
                # CPT shape: (N_STATES,) — P(fault=1 | parent)
                hits = np.zeros(N_STATES, dtype=np.float64)
                counts = np.zeros(N_STATES, dtype=np.float64)

                for d, f in zip(disc_history, fault_labels):
                    p0 = d[parent_metrics[0]]
                    hits[p0] += f[fault_name]
                    counts[p0] += 1.0

                # MLE with Laplace: (hits + α) / (counts + 2α)
                cpt = (hits + alpha) / (counts + 2.0 * alpha)

            elif n_parents == 2:
                # CPT shape: (N_STATES, N_STATES) — P(fault=1 | parent0, parent1)
                hits = np.zeros((N_STATES, N_STATES), dtype=np.float64)
                counts = np.zeros((N_STATES, N_STATES), dtype=np.float64)

                for d, f in zip(disc_history, fault_labels):
                    p0 = d[parent_metrics[0]]
                    p1 = d[parent_metrics[1]]
                    hits[p0, p1] += f[fault_name]
                    counts[p0, p1] += 1.0

                cpt = (hits + alpha) / (counts + 2.0 * alpha)
            else:
                # Should not happen with current FAULT_PARENTS
                continue

            self._cpts[fault_name] = cpt

        self._is_fitted = True
        self._training_points = len(history)

        logger.debug(
            "BayesianDiagnostics: learned CPTs from %d points for agent=%s",
            len(history),
            self.agent_id,
        )

    # ------------------------------------------------------------------
    # Internal: expert-defined CPTs (no data required)
    # ------------------------------------------------------------------

    def _build_expert_cpts(self) -> None:
        """Build CPTs from expert domain knowledge.

        Called when insufficient historical data is available.
        Values encode P(fault=present | parent_states) based on
        operational experience with datacenter infrastructure.
        """
        # ── thermal_throttling: P(fault | cpu_usage, temperature_max) ──
        #          temp=low  temp=normal  temp=elev  temp=crit
        # cpu=low    0.01      0.02        0.08       0.15
        # cpu=norm   0.02      0.05        0.15       0.30
        # cpu=elev   0.05      0.12        0.35       0.60
        # cpu=crit   0.10      0.25        0.55       0.90
        self._cpts["thermal_throttling"] = np.array([
            [0.01, 0.02, 0.08, 0.15],
            [0.02, 0.05, 0.15, 0.30],
            [0.05, 0.12, 0.35, 0.60],
            [0.10, 0.25, 0.55, 0.90],
        ])

        # ── memory_exhaustion: P(fault | mem_usage, cpu_usage) ─────────
        #          cpu=low  cpu=normal  cpu=elev  cpu=crit
        # mem=low    0.01     0.02       0.03      0.05
        # mem=norm   0.03     0.05       0.10      0.15
        # mem=elev   0.10     0.20       0.35      0.50
        # mem=crit   0.40     0.60       0.80      0.90
        self._cpts["memory_exhaustion"] = np.array([
            [0.01, 0.02, 0.03, 0.05],
            [0.03, 0.05, 0.10, 0.15],
            [0.10, 0.20, 0.35, 0.50],
            [0.40, 0.60, 0.80, 0.90],
        ])

        # ── disk_full: P(fault | disk_usage) ──────────────────────────
        # disk: low=0.02, normal=0.10, elevated=0.55, critical=0.95
        self._cpts["disk_full"] = np.array([0.02, 0.10, 0.55, 0.95])

        # ── network_degradation: P(fault | net_errors, bgp_down_count)
        #            bgp=low  bgp=norm  bgp=elev  bgp=crit
        # net=low     0.01     0.05      0.15      0.30
        # net=norm    0.05     0.10      0.25      0.45
        # net=elev    0.15     0.30      0.55      0.75
        # net=crit    0.30     0.50      0.75      0.90
        self._cpts["network_degradation"] = np.array([
            [0.01, 0.05, 0.15, 0.30],
            [0.05, 0.10, 0.25, 0.45],
            [0.15, 0.30, 0.55, 0.75],
            [0.30, 0.50, 0.75, 0.90],
        ])

        # ── switch_overheat: P(fault | temperature_max) ──────────────
        # temp: low=0.01, normal=0.08, elevated=0.45, critical=0.90
        self._cpts["switch_overheat"] = np.array([0.01, 0.08, 0.45, 0.90])

        # ── bgp_failure: P(fault | bgp_down_count, net_errors) ───────
        #            net=low  net=norm  net=elev  net=crit
        # bgp=low     0.01     0.02      0.05      0.10
        # bgp=norm    0.05     0.10      0.15      0.25
        # bgp=elev    0.15     0.25      0.40      0.55
        # bgp=crit    0.40     0.55      0.70      0.90
        self._cpts["bgp_failure"] = np.array([
            [0.01, 0.02, 0.05, 0.10],
            [0.05, 0.10, 0.15, 0.25],
            [0.15, 0.25, 0.40, 0.55],
            [0.40, 0.55, 0.70, 0.90],
        ])

        self._is_fitted = True
        self._training_points = 0

        logger.info(
            "BayesianDiagnostics: expert CPTs built for agent=%s",
            self.agent_id,
        )

    # ------------------------------------------------------------------
    # Internal: inference (CPT lookup)
    # ------------------------------------------------------------------

    def _infer(
        self, evidence: dict[str, int], history_points: int
    ) -> dict[str, Any]:
        """Compute fault probabilities given observed metric states.

        Since ALL parent metric nodes are always observed, inference
        reduces to a direct CPT lookup for each fault node.
        """
        # Build human-readable metric state names
        metric_states = {
            metric: STATES[state_idx]
            for metric, state_idx in evidence.items()
        }

        # Look up fault probabilities
        fault_probs: dict[str, float] = {}
        for fault_name, parent_metrics in FAULT_PARENTS.items():
            cpt = self._cpts.get(fault_name)
            if cpt is None:
                fault_probs[fault_name] = 0.0
                continue

            n_parents = len(parent_metrics)
            if n_parents == 1:
                p0 = evidence[parent_metrics[0]]
                prob = float(cpt[p0])
            elif n_parents == 2:
                p0 = evidence[parent_metrics[0]]
                p1 = evidence[parent_metrics[1]]
                prob = float(cpt[p0, p1])
            else:
                prob = 0.0

            fault_probs[fault_name] = round(prob, 4)

        # Sort by probability descending
        sorted_faults = sorted(
            fault_probs.items(), key=lambda x: x[1], reverse=True
        )

        # Identify the most likely fault (only if probability > threshold)
        if sorted_faults and sorted_faults[0][1] > settings.BAYESIAN_ALERT_THRESHOLD:
            most_likely_fault = sorted_faults[0][0]
            most_likely_prob = sorted_faults[0][1]
        else:
            most_likely_fault = None
            most_likely_prob = sorted_faults[0][1] if sorted_faults else 0.0

        recommendation = (
            FAULT_RECOMMENDATIONS.get(
                most_likely_fault,
                "System operating within normal parameters",
            )
            if most_likely_fault
            else "System operating within normal parameters"
        )

        description = (
            FAULT_DESCRIPTIONS.get(most_likely_fault, "")
            if most_likely_fault
            else ""
        )

        return {
            "fault_probabilities": dict(sorted_faults),
            "most_likely_fault": most_likely_fault,
            "most_likely_probability": most_likely_prob,
            "recommendation": recommendation,
            "description": description,
            "metric_states": metric_states,
            "history_points": history_points,
        }
