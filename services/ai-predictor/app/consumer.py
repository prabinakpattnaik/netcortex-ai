"""Kafka consumer for the ``datacenter.metrics`` topic.

Provides an async wrapper around aiokafka that deserialises JSON messages
and yields them to the processing pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from aiokafka import AIOKafkaConsumer

from app.config import settings

logger = logging.getLogger(__name__)


class MetricsConsumer:
    """Async Kafka consumer that yields decoded metric messages."""

    def __init__(self) -> None:
        self._consumer: AIOKafkaConsumer | None = None
        self._running: bool = False

    async def start(self) -> None:
        """Create and start the underlying Kafka consumer."""
        self._consumer = AIOKafkaConsumer(
            settings.KAFKA_METRICS_TOPIC,
            bootstrap_servers=settings.KAFKA_BROKER,
            group_id=settings.KAFKA_CONSUMER_GROUP,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset="latest",
            enable_auto_commit=True,
        )

        # Retry connection with exponential back-off so the service can boot
        # before Kafka is fully ready.
        max_retries = 10
        for attempt in range(1, max_retries + 1):
            try:
                await self._consumer.start()
                self._running = True
                logger.info(
                    "MetricsConsumer connected to %s (topic=%s)",
                    settings.KAFKA_BROKER,
                    settings.KAFKA_METRICS_TOPIC,
                )
                return
            except Exception:
                wait = min(2 ** attempt, 30)
                logger.warning(
                    "Kafka connection attempt %d/%d failed, retrying in %ds ...",
                    attempt,
                    max_retries,
                    wait,
                )
                await asyncio.sleep(wait)

        raise RuntimeError(
            f"Failed to connect to Kafka at {settings.KAFKA_BROKER} "
            f"after {max_retries} attempts"
        )

    async def stop(self) -> None:
        """Stop the Kafka consumer gracefully."""
        self._running = False
        if self._consumer is not None:
            await self._consumer.stop()
            logger.info("MetricsConsumer stopped")

    async def consume(self) -> AsyncIterator[dict[str, Any]]:
        """Yield decoded metric messages from Kafka.

        This is an infinite async generator that runs until ``stop`` is called
        or the consumer is otherwise interrupted.
        """
        if self._consumer is None:
            raise RuntimeError("Consumer has not been started")

        try:
            async for message in self._consumer:
                if not self._running:
                    break
                try:
                    yield message.value
                except Exception:
                    logger.exception("Error processing Kafka message")
        except Exception:
            if self._running:
                logger.exception("MetricsConsumer loop terminated unexpectedly")
