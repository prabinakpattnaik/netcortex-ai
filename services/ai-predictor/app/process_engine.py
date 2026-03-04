"""ProcessEngine - Background asyncio task that drives the ML pipeline.

Architecture:
  1. Kafka consumer receives a metrics message (trigger signal)
  2. Extract agent_id from the message
  3. Debounce: skip if we already processed this agent recently
  4. Query InfluxDB for the last N minutes of historical data
  5. Run the full ML pipeline on the historical window
  6. Publish any predictions to Kafka

This design means Telemetry Hub is CRITICAL — it persists all data to
InfluxDB first, and AI Predictor reads the stored history for ML.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.consumer import MetricsConsumer
from app.influx_reader import InfluxReader
from app.insight_ai import InsightAI
from app.producer import PredictionProducer
from app.config import settings

logger = logging.getLogger(__name__)


class ProcessEngine:
    """Orchestrates: Kafka trigger -> InfluxDB query -> ML predict -> Kafka publish."""

    def __init__(self) -> None:
        self.consumer = MetricsConsumer()
        self.producer = PredictionProducer()
        self.influx_reader = InfluxReader()
        self.insight_ai = InsightAI()
        self._task: asyncio.Task[None] | None = None
        self._running: bool = False

        # Debounce: track last prediction time per agent_id
        self._last_predict_time: dict[str, float] = {}

    async def start(self) -> None:
        """Start Kafka consumer/producer, InfluxDB reader, and processing loop."""
        logger.info("ProcessEngine starting ...")
        await self.influx_reader.start()
        await self.consumer.start()
        await self.producer.start()
        self._running = True
        self._task = asyncio.create_task(self._run(), name="process-engine")
        logger.info("ProcessEngine started (InfluxDB-backed pipeline)")

    async def stop(self) -> None:
        """Signal the processing loop to stop and clean up resources."""
        logger.info("ProcessEngine stopping ...")
        self._running = False

        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        await self.consumer.stop()
        await self.producer.stop()
        await self.influx_reader.stop()
        logger.info("ProcessEngine stopped")

    async def _run(self) -> None:
        """Main processing loop: Kafka trigger -> InfluxDB -> ML -> publish."""
        try:
            async for message in self.consumer.consume():
                if not self._running:
                    break
                await self._process_message(message)
        except asyncio.CancelledError:
            logger.info("ProcessEngine loop cancelled")
        except Exception:
            logger.exception("ProcessEngine loop crashed")

    async def _process_message(self, message: dict[str, Any]) -> None:
        """Handle a single Kafka message as a trigger for the ML pipeline.

        Steps:
        1. Extract agent_id from the Kafka message
        2. Debounce: skip if recently processed
        3. Flatten the current message (for anomaly classification)
        4. Query InfluxDB for historical window
        5. Run ML pipeline on history
        6. Publish predictions to Kafka
        """
        agent_id = message.get("agent_id", "unknown")
        now = time.monotonic()

        # --- Debounce: don't re-query InfluxDB for the same agent too often ---
        last_time = self._last_predict_time.get(agent_id, 0.0)
        if (now - last_time) < settings.PREDICT_COOLDOWN_SECONDS:
            return

        self._last_predict_time[agent_id] = now

        timestamp_str = message.get("timestamp", "")

        # Flatten the current Kafka message for the anomaly detector
        current_flat = self.insight_ai._flatten_metrics(message)

        # --- Query InfluxDB for historical data ---
        try:
            history = await self.influx_reader.query_agent_history(
                agent_id=agent_id,
                lookback_minutes=settings.INFLUX_LOOKBACK_MINUTES,
            )
        except Exception:
            logger.exception(
                "Failed to query InfluxDB for agent=%s, falling back to point mode",
                agent_id,
            )
            history = []

        # --- Run the ML pipeline ---
        try:
            if history and len(history) >= settings.INFLUX_MIN_HISTORY_POINTS:
                # Primary path: history-based ML using InfluxDB data
                predictions = await asyncio.get_running_loop().run_in_executor(
                    None,
                    self.insight_ai.process_with_history,
                    agent_id,
                    timestamp_str,
                    current_flat,
                    history,
                )
                logger.debug(
                    "Agent %s: ML pipeline ran on %d InfluxDB points -> %d predictions",
                    agent_id,
                    len(history),
                    len(predictions),
                )
            else:
                # Fallback: single-point pipeline (insufficient InfluxDB data)
                predictions = await asyncio.get_running_loop().run_in_executor(
                    None,
                    self.insight_ai.process_metrics,
                    message,
                )
                logger.debug(
                    "Agent %s: ML pipeline ran in point mode (history=%d) -> %d predictions",
                    agent_id,
                    len(history),
                    len(predictions),
                )
        except Exception:
            logger.exception(
                "Error running ML pipeline for agent=%s", agent_id
            )
            return

        # --- Publish predictions to Kafka ---
        for prediction in predictions:
            try:
                await self.producer.publish(prediction)
            except Exception:
                logger.exception(
                    "Error publishing prediction %s",
                    prediction.get("prediction_id", "?"),
                )

    def status(self) -> dict[str, Any]:
        """Return runtime status of the engine and its sub-components."""
        return {
            "running": self._running,
            "task_alive": self._task is not None and not self._task.done(),
            "pipeline_mode": "influxdb_history",
            "cooldown_seconds": settings.PREDICT_COOLDOWN_SECONDS,
            "lookback_minutes": settings.INFLUX_LOOKBACK_MINUTES,
            "agents_tracked": list(self._last_predict_time.keys()),
            "models": self.insight_ai.status(),
        }
