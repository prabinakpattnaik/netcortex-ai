"""FastAPI application entry point with lifespan management."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from aiokafka import AIOKafkaConsumer
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import alerts, demo, metrics, predictions, ws

logger = logging.getLogger("api-gateway")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

# ---------------------------------------------------------------------------
# Shared in-memory state
# ---------------------------------------------------------------------------

# Recent alerts and predictions kept in bounded deques
alerts_buffer: deque[dict[str, Any]] = deque(maxlen=settings.alerts_buffer_size)
predictions_buffer: deque[dict[str, Any]] = deque(maxlen=settings.predictions_buffer_size)

# Latest metric snapshot per agent_id
latest_metrics: dict[str, dict[str, Any]] = {}

# WebSocket connection manager
ws_manager = ws.ConnectionManager()

# ---------------------------------------------------------------------------
# Kafka consumer background tasks
# ---------------------------------------------------------------------------


async def _consume_topic(
    topic: str,
    buffer: deque[dict[str, Any]] | None,
    msg_type: str,
) -> None:
    """Consume messages from a Kafka topic, store in buffer, and broadcast."""
    consumer: AIOKafkaConsumer | None = None
    while True:
        try:
            consumer = AIOKafkaConsumer(
                topic,
                bootstrap_servers=settings.kafka_broker,
                group_id=settings.kafka_group_id,
                value_deserializer=lambda v: json.loads(v.decode("utf-8")),
                auto_offset_reset="latest",
                enable_auto_commit=True,
            )
            await consumer.start()
            logger.info("Kafka consumer started for topic %s", topic)
            async for msg in consumer:
                payload: dict[str, Any] = msg.value
                payload.setdefault(
                    "received_at",
                    datetime.now(timezone.utc).isoformat(),
                )

                # Store in the appropriate buffer
                if msg_type == "metric":
                    agent_id = payload.get("agent_id", "unknown")
                    latest_metrics[agent_id] = payload
                elif buffer is not None:
                    buffer.append(payload)

                # Broadcast to WebSocket clients
                await ws_manager.broadcast(
                    json.dumps({"type": msg_type, "data": payload})
                )
        except asyncio.CancelledError:
            logger.info("Kafka consumer for %s cancelled", topic)
            break
        except Exception:
            logger.exception(
                "Kafka consumer error on topic %s, reconnecting in 5s", topic
            )
            await asyncio.sleep(5)
        finally:
            if consumer is not None:
                try:
                    await consumer.stop()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# FastAPI lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start Kafka consumers on startup, cancel on shutdown."""
    tasks: list[asyncio.Task] = [
        asyncio.create_task(
            _consume_topic("datacenter.alerts", alerts_buffer, "alert"),
            name="kafka-alerts",
        ),
        asyncio.create_task(
            _consume_topic("datacenter.predictions", predictions_buffer, "prediction"),
            name="kafka-predictions",
        ),
        asyncio.create_task(
            _consume_topic("datacenter.metrics", None, "metric"),
            name="kafka-metrics",
        ),
    ]
    logger.info("Background Kafka consumers started")
    yield
    # Shutdown: cancel all background tasks
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("Background Kafka consumers stopped")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AI Datacenter API Gateway",
    description="Unified REST API and WebSocket streaming for the AI Datacenter monitoring platform.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS (allow all origins for development)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(metrics.router)
app.include_router(alerts.router)
app.include_router(predictions.router)
app.include_router(ws.router)
app.include_router(demo.router)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


@app.get("/api/v1/health", tags=["health"])
async def health():
    """Basic health check."""
    return {
        "status": "healthy",
        "service": "api-gateway",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ws_clients": ws_manager.active_count,
    }
