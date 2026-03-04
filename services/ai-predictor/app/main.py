"""FastAPI application for the AIPredictor service.

Exposes REST endpoints for health checks, model status, and manual
inference while running the ML pipeline as a background task via
the ``ProcessEngine``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.process_engine import ProcessEngine

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared engine instance (created during lifespan)
# ---------------------------------------------------------------------------
engine: ProcessEngine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the ProcessEngine lifecycle alongside the FastAPI app."""
    global engine
    engine = ProcessEngine()

    try:
        await engine.start()
        logger.info("AIPredictor service is ready")
    except Exception:
        logger.exception("Failed to start ProcessEngine - running in API-only mode")
        engine = ProcessEngine()  # reset to a clean (non-started) instance

    yield

    if engine is not None:
        await engine.stop()
        logger.info("AIPredictor service shut down")


app = FastAPI(
    title="AIPredictor",
    description="AI/ML prediction service for datacenter metrics",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: str


class MetricsPayload(BaseModel):
    """Payload for manual inference endpoints."""
    agent_id: str = Field(..., description="Unique identifier of the agent/server")
    cpu: dict[str, Any] = Field(default_factory=dict)
    memory: dict[str, Any] = Field(default_factory=dict)
    disk: dict[str, Any] = Field(default_factory=dict)
    network: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class RCARequest(BaseModel):
    agent_id: str
    affected_metrics: list[str] | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health_check() -> HealthResponse:
    """Return service health status."""
    return HealthResponse(
        status="healthy",
        service=settings.SERVICE_NAME,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/status", tags=["status"])
async def get_status() -> dict[str, Any]:
    """Return detailed status of the processing engine and all ML models."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Engine not initialised")
    return engine.status()


@app.get("/models", tags=["models"])
async def list_models() -> dict[str, Any]:
    """Return summary information about registered ML models."""
    return {
        "pipeline": {
            "mode": "influxdb_history",
            "description": (
                "Kafka messages trigger the ML pipeline. Historical data is "
                "read from InfluxDB (written by Telemetry Hub) to provide "
                "time-series context for accurate predictions."
            ),
            "lookback_minutes": settings.INFLUX_LOOKBACK_MINUTES,
            "min_history_points": settings.INFLUX_MIN_HISTORY_POINTS,
            "cooldown_seconds": settings.PREDICT_COOLDOWN_SECONDS,
        },
        "models": [
            {
                "name": "anomaly_detection",
                "type": "Isolation Forest",
                "description": (
                    "Trains on InfluxDB historical window, detects if the "
                    "latest sample is anomalous. 8 features including "
                    "temperature_max and bgp_down_count."
                ),
                "data_source": "InfluxDB (30 min window)",
            },
            {
                "name": "fault_prediction",
                "type": "Linear Regression (OLS)",
                "description": (
                    "Fits trend regression on historical time-series from "
                    "InfluxDB. Extrapolates to predict threshold breaches "
                    "within the prediction horizon."
                ),
                "data_source": "InfluxDB (30 min window)",
                "horizon_minutes": settings.FAULT_PREDICTION_HORIZON_MINUTES,
            },
            {
                "name": "root_cause_analysis",
                "type": "Pearson Correlation",
                "description": (
                    "When anomaly detected, computes metric correlations "
                    "over the InfluxDB history window to identify root causes."
                ),
                "data_source": "InfluxDB (30 min window)",
            },
            {
                "name": "bayesian_diagnosis",
                "type": "Bayesian Network (DAG + CPTs)",
                "description": (
                    "Probabilistic causal diagnosis using a directed acyclic "
                    "graph with expert-defined structure. Discretises metrics "
                    "into 4 states, learns CPTs from history via MLE, and "
                    "computes posterior fault probabilities."
                ),
                "data_source": "InfluxDB (30 min window)",
                "fault_types": [
                    "thermal_throttling",
                    "memory_exhaustion",
                    "disk_full",
                    "network_degradation",
                    "switch_overheat",
                    "bgp_failure",
                ],
                "alert_threshold": settings.BAYESIAN_ALERT_THRESHOLD,
            },
        ],
    }


@app.post("/predict/anomaly", tags=["inference"])
async def predict_anomaly(payload: MetricsPayload) -> dict[str, Any]:
    """Run anomaly detection on a manually submitted metrics sample."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Engine not initialised")

    result = engine.insight_ai.run_anomaly_detection(
        agent_id=payload.agent_id,
        metrics=payload.model_dump(),
    )
    return {"agent_id": payload.agent_id, "model": "anomaly_detection", "result": result}


@app.post("/predict/fault", tags=["inference"])
async def predict_fault(payload: MetricsPayload) -> dict[str, Any]:
    """Run fault prediction on a manually submitted metrics sample."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Engine not initialised")

    results = engine.insight_ai.run_fault_prediction(
        agent_id=payload.agent_id,
        metrics=payload.model_dump(),
    )
    return {"agent_id": payload.agent_id, "model": "fault_prediction", "results": results}


@app.post("/predict/root-cause", tags=["inference"])
async def predict_root_cause(request: RCARequest) -> dict[str, Any]:
    """Run root cause analysis for a given agent using its existing buffer."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Engine not initialised")

    result = engine.insight_ai.run_root_cause_analysis(
        agent_id=request.agent_id,
        affected_metrics=request.affected_metrics,
    )
    return {"agent_id": request.agent_id, "model": "root_cause_analysis", "result": result}


@app.post("/predict/bayesian", tags=["inference"])
async def predict_bayesian(payload: MetricsPayload) -> dict[str, Any]:
    """Run Bayesian fault diagnosis on a manually submitted metrics sample.

    Uses expert-defined CPTs (point mode) to compute posterior fault
    probabilities based on the current metric states.
    """
    if engine is None:
        raise HTTPException(status_code=503, detail="Engine not initialised")

    result = engine.insight_ai.run_bayesian_diagnosis(
        agent_id=payload.agent_id,
        metrics=payload.model_dump(),
    )
    return {"agent_id": payload.agent_id, "model": "bayesian_diagnosis", "result": result}


@app.post("/predict", tags=["inference"])
async def predict_full_pipeline(payload: MetricsPayload) -> dict[str, Any]:
    """Run the full ML pipeline (anomaly + fault + RCA) on a single sample."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Engine not initialised")

    predictions = engine.insight_ai.process_metrics(payload.model_dump())
    return {
        "agent_id": payload.agent_id,
        "predictions": predictions,
    }
