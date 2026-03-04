"""Kafka producer for the ``datacenter.predictions`` topic.

Publishes JSON-encoded prediction results produced by the ML pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiokafka import AIOKafkaProducer

from app.config import settings

logger = logging.getLogger(__name__)


class PredictionProducer:
    """Async Kafka producer that publishes prediction messages."""

    def __init__(self) -> None:
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        """Create and start the underlying Kafka producer."""
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.KAFKA_BROKER,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )

        max_retries = 10
        for attempt in range(1, max_retries + 1):
            try:
                await self._producer.start()
                logger.info(
                    "PredictionProducer connected to %s", settings.KAFKA_BROKER
                )
                return
            except Exception:
                wait = min(2 ** attempt, 30)
                logger.warning(
                    "Kafka producer connection attempt %d/%d failed, "
                    "retrying in %ds ...",
                    attempt,
                    max_retries,
                    wait,
                )
                await asyncio.sleep(wait)

        raise RuntimeError(
            f"Failed to connect Kafka producer to {settings.KAFKA_BROKER} "
            f"after {max_retries} attempts"
        )

    async def stop(self) -> None:
        """Flush pending messages and stop the producer."""
        if self._producer is not None:
            await self._producer.stop()
            logger.info("PredictionProducer stopped")

    async def publish(self, prediction: dict[str, Any]) -> None:
        """Publish a single prediction message to Kafka.

        Parameters
        ----------
        prediction:
            A dict matching the ``datacenter.predictions`` schema.
        """
        if self._producer is None:
            raise RuntimeError("Producer has not been started")

        try:
            await self._producer.send_and_wait(
                settings.KAFKA_PREDICTIONS_TOPIC,
                value=prediction,
            )
            logger.debug(
                "Published prediction %s", prediction.get("prediction_id", "?")
            )
        except Exception:
            logger.exception("Failed to publish prediction to Kafka")
