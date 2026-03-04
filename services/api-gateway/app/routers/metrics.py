"""Metrics endpoints - query historical data from InfluxDB."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

from app.config import settings

logger = logging.getLogger("api-gateway.metrics")

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])


async def _query_influx(flux: str) -> list[dict[str, Any]]:
    """Execute a Flux query against InfluxDB and return records as dicts."""
    async with InfluxDBClientAsync(
        url=settings.influxdb_url,
        token=settings.influxdb_token,
        org=settings.influxdb_org,
    ) as client:
        query_api = client.query_api()
        tables = await query_api.query(flux, org=settings.influxdb_org)
        results: list[dict[str, Any]] = []
        for table in tables:
            for record in table.records:
                results.append(
                    {
                        "time": record.get_time().isoformat() if record.get_time() else None,
                        "measurement": record.get_measurement(),
                        "field": record.get_field(),
                        "value": record.get_value(),
                        **{
                            k: v
                            for k, v in record.values.items()
                            if k not in ("_time", "_measurement", "_field", "_value", "result", "table")
                        },
                    }
                )
        return results


@router.get("")
async def get_metrics(
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
    last: str = Query("1h", description="Time range, e.g. 1h, 30m, 24h"),
    limit: int = Query(100, ge=1, le=10000, description="Maximum number of records"),
) -> dict[str, Any]:
    """Query recent metric snapshots from InfluxDB.

    Returns aggregated metrics over the requested time window.
    """
    agent_filter = ""
    if agent_id:
        agent_filter = f'  |> filter(fn: (r) => r["agent_id"] == "{agent_id}")\n'

    flux = (
        f'from(bucket: "{settings.influxdb_bucket}")\n'
        f"  |> range(start: -{last})\n"
        f"{agent_filter}"
        f"  |> limit(n: {limit})\n"
        f'  |> sort(columns: ["_time"], desc: true)'
    )

    try:
        records = await _query_influx(flux)
    except Exception as exc:
        logger.exception("InfluxDB query failed")
        raise HTTPException(status_code=502, detail=f"InfluxDB query error: {exc}") from exc

    return {
        "count": len(records),
        "range": last,
        "agent_id": agent_id,
        "data": records,
    }


@router.get("/{agent_id}")
async def get_agent_metrics(agent_id: str) -> dict[str, Any]:
    """Get the latest metrics for a specific agent.

    First checks the in-memory cache populated by Kafka; falls back to
    InfluxDB for the most recent record.
    """
    # Try in-memory latest metrics first (imported lazily to avoid circular
    # import at module level).
    from app.main import latest_metrics

    cached = latest_metrics.get(agent_id)
    if cached:
        return {"source": "realtime", "agent_id": agent_id, "data": cached}

    # Fallback: query InfluxDB for the last 5 minutes
    flux = (
        f'from(bucket: "{settings.influxdb_bucket}")\n'
        f"  |> range(start: -5m)\n"
        f'  |> filter(fn: (r) => r["agent_id"] == "{agent_id}")\n'
        f"  |> last()"
    )

    try:
        records = await _query_influx(flux)
    except Exception as exc:
        logger.exception("InfluxDB query failed for agent %s", agent_id)
        raise HTTPException(status_code=502, detail=f"InfluxDB query error: {exc}") from exc

    if not records:
        raise HTTPException(status_code=404, detail=f"No metrics found for agent {agent_id}")

    return {"source": "influxdb", "agent_id": agent_id, "data": records}
