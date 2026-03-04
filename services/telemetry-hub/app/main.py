"""TelemetryHub FastAPI application.

Lifespan manages Kafka consumer, InfluxDB writer, and the AlertHub engine.
Exposes health-check and alert-rule management endpoints.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status

from .alert_hub import AlertHub
from .config import settings
from .consumer import TelemetryConsumer
from .influx_writer import InfluxWriter
from .models.schemas import AlertRule

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared instances (created once per process)
# ---------------------------------------------------------------------------
influx_writer = InfluxWriter()
alert_hub = AlertHub()
consumer = TelemetryConsumer(writer=influx_writer, alert_hub=alert_hub)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect to InfluxDB, start Kafka consumer & AlertHub producer.
    Shutdown: drain everything gracefully.
    """
    logger.info("TelemetryHub starting up ...")
    await influx_writer.start()
    await alert_hub.start()
    await consumer.start()
    logger.info("TelemetryHub is ready")

    yield

    logger.info("TelemetryHub shutting down ...")
    await consumer.stop()
    await alert_hub.stop()
    await influx_writer.stop()
    logger.info("TelemetryHub shutdown complete")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="TelemetryHub",
    description="Datacenter telemetry ingestion, storage, and alerting service",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------
@app.get("/health", tags=["health"])
async def health():
    """Basic liveness probe."""
    return {"status": "ok", "service": "telemetry-hub"}


@app.get("/health/ready", tags=["health"])
async def readiness():
    """Readiness probe -- checks InfluxDB connectivity."""
    influx_ok = await influx_writer.healthy()
    if not influx_ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="InfluxDB not reachable",
        )
    return {"status": "ready", "influxdb": influx_ok}


# ---------------------------------------------------------------------------
# Alert-rule management endpoints
# ---------------------------------------------------------------------------
@app.get("/alerts/rules", tags=["alerts"], response_model=list[AlertRule])
async def list_rules():
    """Return all configured alert rules."""
    return alert_hub.rules


@app.get("/alerts/rules/{metric}", tags=["alerts"], response_model=AlertRule)
async def get_rule(metric: str):
    """Return a single alert rule by metric name."""
    rule = alert_hub.get_rule(metric)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No rule for metric '{metric}'",
        )
    return rule


@app.put("/alerts/rules", tags=["alerts"], response_model=AlertRule)
async def upsert_rule(rule: AlertRule):
    """Create or update an alert rule."""
    return alert_hub.upsert_rule(rule)


@app.delete("/alerts/rules/{metric}", tags=["alerts"])
async def delete_rule(metric: str):
    """Delete an alert rule by metric name."""
    removed = alert_hub.delete_rule(metric)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No rule for metric '{metric}'",
        )
    return {"deleted": metric}
