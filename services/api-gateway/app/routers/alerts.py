"""Alerts endpoint - serves recent alerts from the in-memory buffer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])

# Map human-readable durations to timedelta
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
    # Simple fallback parser
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
async def get_alerts(
    severity: Optional[str] = Query(None, description="Filter by severity (critical, warning, info)"),
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
    last: str = Query("1h", description="Time range, e.g. 1h, 30m, 24h"),
) -> dict[str, Any]:
    """Return recent alerts from the in-memory buffer populated by Kafka.

    Supports optional filtering by severity and agent_id.
    """
    from app.main import alerts_buffer

    cutoff = datetime.now(timezone.utc) - _parse_duration(last)

    results: list[dict[str, Any]] = []
    for alert in alerts_buffer:
        # Time filtering
        received = alert.get("received_at") or alert.get("timestamp")
        if received:
            try:
                ts = datetime.fromisoformat(received)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
            except (ValueError, TypeError):
                pass

        # Severity filter
        if severity and alert.get("severity", "").lower() != severity.lower():
            continue

        # Agent filter
        if agent_id and alert.get("agent_id") != agent_id:
            continue

        results.append(alert)

    return {
        "count": len(results),
        "range": last,
        "severity": severity,
        "agent_id": agent_id,
        "data": results,
    }
