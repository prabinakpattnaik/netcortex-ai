"""Predictions endpoint - serves recent AI predictions from the in-memory buffer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/v1/predictions", tags=["predictions"])

_DURATION_MAP = {
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "12h": timedelta(hours=12),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}


def _parse_duration(raw: str) -> timedelta:
    """Parse a duration string like '1h', '30m', '7d' into a timedelta."""
    if raw in _DURATION_MAP:
        return _DURATION_MAP[raw]
    unit = raw[-1]
    try:
        value = int(raw[:-1])
    except ValueError:
        return timedelta(hours=1)
    if unit == "m":
        return timedelta(minutes=value)
    if unit == "h":
        return timedelta(hours=value)
    if unit == "d":
        return timedelta(days=value)
    return timedelta(hours=1)


@router.get("")
async def get_predictions(
    model: Optional[str] = Query(None, description="Filter by model name"),
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
    last: str = Query("1h", description="Time range, e.g. 1h, 30m, 24h"),
) -> dict[str, Any]:
    """Return recent AI predictions from the in-memory buffer populated by Kafka.

    Supports optional filtering by model name and agent_id.
    """
    from app.main import predictions_buffer

    cutoff = datetime.now(timezone.utc) - _parse_duration(last)

    results: list[dict[str, Any]] = []
    for prediction in predictions_buffer:
        # Time filtering
        received = prediction.get("received_at") or prediction.get("timestamp")
        if received:
            try:
                ts = datetime.fromisoformat(received)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
            except (ValueError, TypeError):
                pass

        # Model filter
        if model and prediction.get("model", "").lower() != model.lower():
            continue

        # Agent filter
        if agent_id and prediction.get("agent_id") != agent_id:
            continue

        results.append(prediction)

    return {
        "count": len(results),
        "range": last,
        "model": model,
        "agent_id": agent_id,
        "data": results,
    }
