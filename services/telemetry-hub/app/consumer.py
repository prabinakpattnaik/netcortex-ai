"""Kafka consumer that subscribes to datacenter.metrics and datacenter.flows.

Messages are deserialized, written to InfluxDB, and evaluated by the AlertHub
rules engine.  The consumer runs as a long-lived asyncio task managed by the
FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from aiokafka import AIOKafkaConsumer

from .config import settings
from .models.schemas import FlowRecord, MetricsMessage

if TYPE_CHECKING:
    from .alert_hub import AlertHub
    from .influx_writer import InfluxWriter

logger = logging.getLogger(__name__)


class TelemetryConsumer:
    """Wraps an aiokafka consumer for the two datacenter topics."""

    def __init__(self, writer: InfluxWriter, alert_hub: AlertHub) -> None:
        self._writer = writer
        self._alert_hub = alert_hub
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        self._consumer = AIOKafkaConsumer(
            settings.kafka_metrics_topic,
            settings.kafka_flows_topic,
            bootstrap_servers=settings.kafka_broker,
            group_id=settings.kafka_consumer_group,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="latest",
            enable_auto_commit=True,
        )
        await self._consumer.start()
        logger.info(
            "Kafka consumer started (broker=%s, topics=[%s, %s], group=%s)",
            settings.kafka_broker,
            settings.kafka_metrics_topic,
            settings.kafka_flows_topic,
            settings.kafka_consumer_group,
        )
        self._task = asyncio.create_task(self._consume_loop(), name="telemetry-consumer")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._consumer:
            await self._consumer.stop()
            self._consumer = None
            logger.info("Kafka consumer stopped")

    # -- consume loop ----------------------------------------------------

    async def _consume_loop(self) -> None:
        """Continuously poll messages and dispatch by topic."""
        assert self._consumer is not None
        try:
            async for message in self._consumer:
                try:
                    await self._dispatch(message.topic, message.value)
                except Exception:
                    logger.exception(
                        "Error processing message from topic=%s offset=%s",
                        message.topic,
                        message.offset,
                    )
        except asyncio.CancelledError:
            logger.info("Consumer loop cancelled")
            raise

    async def _dispatch(self, topic: str, payload: dict | list) -> None:
        if topic == settings.kafka_metrics_topic:
            await self._handle_metrics(payload)
        elif topic == settings.kafka_flows_topic:
            await self._handle_flows(payload)
        else:
            logger.warning("Received message on unexpected topic: %s", topic)

    # -- handlers --------------------------------------------------------

    async def _handle_metrics(self, payload: dict) -> None:
        msg = MetricsMessage.model_validate(payload)

        # Write to InfluxDB
        await self._writer.write_metrics(msg)

        # Evaluate alert rules
        alerts = await self._alert_hub.evaluate(msg)
        if alerts:
            logger.info(
                "Generated %d alert(s) for agent %s", len(alerts), msg.agent_id
            )

    async def _handle_flows(self, payload: list | dict) -> None:
        # The flows topic can carry either a single record or a batch array
        if isinstance(payload, dict):
            records = [payload]
        else:
            records = payload

        flows = [FlowRecord.model_validate(r) for r in records]
        await self._writer.write_flows(flows)
        logger.debug("Wrote %d flow records for agent(s)", len(flows))
