"""AlertHub -- lightweight alert-rules engine.

Evaluates incoming MetricsMessages against configurable threshold rules and
publishes alert messages to the ``datacenter.alerts`` Kafka topic.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from aiokafka import AIOKafkaProducer

from .config import settings
from .models.schemas import AlertMessage, AlertRule, AlertSeverity

if TYPE_CHECKING:
    from .models.schemas import MetricsMessage

logger = logging.getLogger(__name__)


def _default_rules() -> list[AlertRule]:
    """Return the default set of threshold rules seeded from config."""
    return [
        AlertRule(
            metric="cpu_usage",
            warning_threshold=settings.alert_cpu_warning,
            critical_threshold=settings.alert_cpu_critical,
            description="CPU usage percent",
        ),
        AlertRule(
            metric="memory_usage",
            warning_threshold=settings.alert_memory_warning,
            critical_threshold=settings.alert_memory_critical,
            description="Memory usage percent",
        ),
        AlertRule(
            metric="disk_usage",
            warning_threshold=settings.alert_disk_warning,
            critical_threshold=settings.alert_disk_critical,
            description="Disk usage percent",
        ),
        AlertRule(
            metric="network_errors",
            warning_threshold=float(settings.alert_network_errors_warning),
            critical_threshold=float(settings.alert_network_errors_warning * 10),
            description="Total network errors per sample",
        ),
        AlertRule(
            metric="temperature",
            warning_threshold=settings.alert_temperature_warning,
            critical_threshold=settings.alert_temperature_critical,
            description="Switch sensor temperature (°C)",
        ),
        AlertRule(
            metric="bgp_down",
            warning_threshold=float(settings.alert_bgp_down_warning),
            critical_threshold=float(settings.alert_bgp_down_critical),
            description="Number of BGP sessions not Established",
        ),
    ]


class AlertHub:
    """Manages alert rules and publishes alerts to Kafka."""

    def __init__(self) -> None:
        self._rules: list[AlertRule] = _default_rules()
        self._producer: AIOKafkaProducer | None = None

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_broker,
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
        )
        await self._producer.start()
        logger.info("AlertHub Kafka producer started (topic=%s)", settings.kafka_alerts_topic)

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()
            self._producer = None
            logger.info("AlertHub Kafka producer stopped")

    # -- rule management -------------------------------------------------

    @property
    def rules(self) -> list[AlertRule]:
        return list(self._rules)

    def get_rule(self, metric: str) -> AlertRule | None:
        for r in self._rules:
            if r.metric == metric:
                return r
        return None

    def upsert_rule(self, rule: AlertRule) -> AlertRule:
        """Insert or update a rule. Returns the stored rule."""
        for idx, existing in enumerate(self._rules):
            if existing.metric == rule.metric:
                self._rules[idx] = rule
                logger.info("Updated alert rule: %s", rule.metric)
                return rule
        self._rules.append(rule)
        logger.info("Added alert rule: %s", rule.metric)
        return rule

    def delete_rule(self, metric: str) -> bool:
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.metric != metric]
        removed = len(self._rules) < before
        if removed:
            logger.info("Deleted alert rule: %s", metric)
        return removed

    # -- evaluation ------------------------------------------------------

    async def evaluate(self, msg: MetricsMessage) -> list[AlertMessage]:
        """Run all enabled rules against a MetricsMessage.

        Returns the list of alerts that were generated *and* published.
        """
        alerts: list[AlertMessage] = []

        for rule in self._rules:
            if not rule.enabled:
                continue

            pairs = self._extract_values(rule.metric, msg)
            for label, value in pairs:
                severity = self._check_threshold(value, rule)
                if severity is None:
                    continue
                threshold = (
                    rule.critical_threshold
                    if severity == AlertSeverity.CRITICAL
                    else rule.warning_threshold
                )
                alert = AlertMessage(
                    alert_id=str(uuid.uuid4()),
                    timestamp=datetime.now(timezone.utc),
                    agent_id=msg.agent_id,
                    severity=severity,
                    metric=label,
                    value=value,
                    threshold=threshold,
                    message=f"{label} {value:.1f} exceeds threshold {threshold:.1f}",
                )
                alerts.append(alert)

        # Publish all alerts
        for alert in alerts:
            await self._publish(alert)

        return alerts

    # -- internals -------------------------------------------------------

    @staticmethod
    def _extract_values(
        metric: str, msg: MetricsMessage
    ) -> list[tuple[str, float]]:
        """Return a list of (label, value) pairs for the given rule metric."""
        if metric == "cpu_usage":
            return [("cpu_usage", msg.cpu.usage_percent)]
        if metric == "memory_usage":
            return [("memory_usage", msg.memory.usage_percent)]
        if metric == "disk_usage":
            return [(f"disk_usage[{d.device}]", d.usage_percent) for d in msg.disk]
        if metric == "network_errors":
            results: list[tuple[str, float]] = []
            for n in msg.network:
                total_errors = float(
                    n.rx_errors + n.tx_errors + n.rx_dropped + n.tx_dropped
                )
                results.append((f"network_errors[{n.interface}]", total_errors))
            return results
        if metric == "temperature":
            if msg.hardware:
                return [
                    (f"temperature[{t.name}]", t.current)
                    for t in msg.hardware.temperatures
                ]
            return []
        if metric == "bgp_down":
            if msg.bgp:
                down_count = sum(
                    1 for b in msg.bgp if b.state != "Established"
                )
                if down_count > 0:
                    return [("bgp_sessions_down", float(down_count))]
            return []
        return []

    @staticmethod
    def _check_threshold(value: float, rule: AlertRule) -> AlertSeverity | None:
        if value >= rule.critical_threshold:
            return AlertSeverity.CRITICAL
        if value >= rule.warning_threshold:
            return AlertSeverity.WARNING
        return None

    async def _publish(self, alert: AlertMessage) -> None:
        if not self._producer:
            logger.warning("AlertHub producer not initialised; dropping alert %s", alert.alert_id)
            return
        try:
            await self._producer.send_and_wait(
                settings.kafka_alerts_topic,
                value=alert.model_dump(mode="json"),
                key=alert.agent_id.encode("utf-8"),
            )
            logger.info(
                "Published alert %s [%s] %s=%s",
                alert.alert_id,
                alert.severity.value,
                alert.metric,
                alert.value,
            )
        except Exception:
            logger.exception("Failed to publish alert %s", alert.alert_id)
