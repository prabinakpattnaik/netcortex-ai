"""InfluxDB reader for the AI Predictor service.

Queries historical metric data from InfluxDB so the ML models can work
with real time-series windows instead of in-memory point-by-point buffers.

Flow:
  Kafka message arrives (trigger)
  → InfluxReader.query_agent_history(agent_id, lookback_minutes)
  → Returns list of flattened feature dicts sorted by time
  → InsightAI runs ML pipeline on the full history window
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync

from app.config import settings

logger = logging.getLogger(__name__)


class InfluxReader:
    """Async InfluxDB reader that fetches historical metric windows."""

    def __init__(self) -> None:
        self._client: InfluxDBClientAsync | None = None

    async def start(self) -> None:
        """Connect to InfluxDB."""
        self._client = InfluxDBClientAsync(
            url=settings.INFLUXDB_URL,
            token=settings.INFLUXDB_TOKEN,
            org=settings.INFLUXDB_ORG,
        )
        # Verify connectivity
        try:
            ok = await self._client.ping()
            logger.info(
                "InfluxReader connected to %s (ping=%s)", settings.INFLUXDB_URL, ok
            )
        except Exception:
            logger.warning(
                "InfluxReader could not ping %s — will retry on queries",
                settings.INFLUXDB_URL,
            )

    async def stop(self) -> None:
        """Close the InfluxDB connection."""
        if self._client:
            await self._client.close()
            self._client = None
            logger.info("InfluxReader closed")

    # ------------------------------------------------------------------
    # Public query method
    # ------------------------------------------------------------------

    async def query_agent_history(
        self,
        agent_id: str,
        lookback_minutes: int | None = None,
    ) -> list[dict[str, Any]]:
        """Query InfluxDB for the last N minutes of metrics for one agent.

        Returns a list of flattened feature dicts, one per 5-second interval,
        sorted by time ascending.  Each dict has keys matching the ML
        feature set::

            {
                "timestamp": 1704067200.0,  # epoch seconds
                "cpu_usage": 45.2,
                "mem_usage": 62.1,
                "disk_usage": 55.0,
                "net_rx": 50000,
                "net_tx": 30000,
                "net_errors": 0,
                "temperature_max": 52.0,
                "bgp_down_count": 0.0,
            }
        """
        if not self._client:
            logger.warning("InfluxReader not connected — returning empty history")
            return []

        lookback = lookback_minutes or settings.INFLUX_LOOKBACK_MINUTES
        bucket = settings.INFLUXDB_BUCKET

        # Each metric lives in its own measurement in InfluxDB.
        # We query them independently and merge by timestamp in Python.
        try:
            cpu_data = await self._query_single_metric(
                bucket, agent_id, lookback,
                measurement="cpu", field="usage_percent", alias="cpu_usage",
            )
            mem_data = await self._query_single_metric(
                bucket, agent_id, lookback,
                measurement="memory", field="usage_percent", alias="mem_usage",
            )
            disk_data = await self._query_aggregated_metric(
                bucket, agent_id, lookback,
                measurement="disk", field="usage_percent", alias="disk_usage",
                agg_fn="mean",
            )
            net_rx_data = await self._query_aggregated_metric(
                bucket, agent_id, lookback,
                measurement="network", field="rx_bytes", alias="net_rx",
                agg_fn="sum",
            )
            net_tx_data = await self._query_aggregated_metric(
                bucket, agent_id, lookback,
                measurement="network", field="tx_bytes", alias="net_tx",
                agg_fn="sum",
            )
            net_err_data = await self._query_network_errors(
                bucket, agent_id, lookback,
            )
            temp_data = await self._query_aggregated_metric(
                bucket, agent_id, lookback,
                measurement="temperature", field="current", alias="temperature_max",
                agg_fn="max",
            )
            bgp_data = await self._query_bgp_down_count(
                bucket, agent_id, lookback,
            )
        except Exception:
            logger.exception("Failed to query InfluxDB history for agent=%s", agent_id)
            return []

        # Merge all metric series by timestamp
        return self._merge_series(
            cpu_data, mem_data, disk_data,
            net_rx_data, net_tx_data, net_err_data,
            temp_data, bgp_data,
        )

    # ------------------------------------------------------------------
    # Internal Flux query helpers
    # ------------------------------------------------------------------

    async def _query_single_metric(
        self,
        bucket: str,
        agent_id: str,
        lookback_minutes: int,
        measurement: str,
        field: str,
        alias: str,
    ) -> dict[float, float]:
        """Query a single field from a single measurement.

        Returns {epoch_seconds: value} dict.
        """
        query = f'''
from(bucket: "{bucket}")
  |> range(start: -{lookback_minutes}m)
  |> filter(fn: (r) => r["_measurement"] == "{measurement}")
  |> filter(fn: (r) => r["agent_id"] == "{agent_id}")
  |> filter(fn: (r) => r["_field"] == "{field}")
  |> aggregateWindow(every: 5s, fn: last, createEmpty: false)
  |> yield(name: "{alias}")
'''
        return await self._execute_to_dict(query)

    async def _query_aggregated_metric(
        self,
        bucket: str,
        agent_id: str,
        lookback_minutes: int,
        measurement: str,
        field: str,
        alias: str,
        agg_fn: str = "mean",
    ) -> dict[float, float]:
        """Query a field that may exist across multiple tags (devices/interfaces).

        Aggregates across tags using the specified function (mean, sum, max).
        """
        query = f'''
from(bucket: "{bucket}")
  |> range(start: -{lookback_minutes}m)
  |> filter(fn: (r) => r["_measurement"] == "{measurement}")
  |> filter(fn: (r) => r["agent_id"] == "{agent_id}")
  |> filter(fn: (r) => r["_field"] == "{field}")
  |> aggregateWindow(every: 5s, fn: last, createEmpty: false)
  |> group(columns: ["_time"])
  |> {agg_fn}(column: "_value")
  |> group()
  |> yield(name: "{alias}")
'''
        return await self._execute_to_dict(query)

    async def _query_network_errors(
        self,
        bucket: str,
        agent_id: str,
        lookback_minutes: int,
    ) -> dict[float, float]:
        """Query rx_errors + tx_errors summed across all interfaces."""
        query = f'''
import "experimental"

rx = from(bucket: "{bucket}")
  |> range(start: -{lookback_minutes}m)
  |> filter(fn: (r) => r["_measurement"] == "network")
  |> filter(fn: (r) => r["agent_id"] == "{agent_id}")
  |> filter(fn: (r) => r["_field"] == "rx_errors" or r["_field"] == "tx_errors")
  |> aggregateWindow(every: 5s, fn: last, createEmpty: false)
  |> group(columns: ["_time"])
  |> sum(column: "_value")
  |> group()
  |> yield(name: "net_errors")
'''
        return await self._execute_to_dict(query)

    async def _query_bgp_down_count(
        self,
        bucket: str,
        agent_id: str,
        lookback_minutes: int,
    ) -> dict[float, float]:
        """Count BGP sessions that are NOT Established (state_up == 0).

        state_up: 1 = Established, 0 = Down.
        We sum (1 - state_up) across all neighbors to get the down count.
        """
        query = f'''
from(bucket: "{bucket}")
  |> range(start: -{lookback_minutes}m)
  |> filter(fn: (r) => r["_measurement"] == "bgp")
  |> filter(fn: (r) => r["agent_id"] == "{agent_id}")
  |> filter(fn: (r) => r["_field"] == "state_up")
  |> aggregateWindow(every: 5s, fn: last, createEmpty: false)
  |> map(fn: (r) => ({{ r with _value: if r._value == 0 then 1.0 else 0.0 }}))
  |> group(columns: ["_time"])
  |> sum(column: "_value")
  |> group()
  |> yield(name: "bgp_down_count")
'''
        return await self._execute_to_dict(query)

    # ------------------------------------------------------------------
    # Execution & merge
    # ------------------------------------------------------------------

    async def _execute_to_dict(self, query: str) -> dict[float, float]:
        """Execute a Flux query and return {epoch_seconds: value}."""
        assert self._client is not None
        result: dict[float, float] = {}

        try:
            query_api = self._client.query_api()
            tables = await query_api.query(query, org=settings.INFLUXDB_ORG)

            for table in tables:
                for record in table.records:
                    ts: datetime = record.get_time()
                    val = record.get_value()
                    if ts is not None and val is not None:
                        epoch = ts.timestamp()
                        result[epoch] = float(val)
        except Exception:
            logger.debug("Flux query returned no data or failed", exc_info=True)

        return result

    @staticmethod
    def _merge_series(*series_list: dict[float, float]) -> list[dict[str, Any]]:
        """Merge multiple {epoch: value} dicts into a sorted list of feature dicts.

        Each series corresponds to a feature in order:
        cpu_usage, mem_usage, disk_usage, net_rx, net_tx, net_errors,
        temperature_max, bgp_down_count.
        """
        feature_names = [
            "cpu_usage", "mem_usage", "disk_usage",
            "net_rx", "net_tx", "net_errors",
            "temperature_max", "bgp_down_count",
        ]

        # Collect all unique timestamps across all series
        all_timestamps: set[float] = set()
        for series in series_list:
            all_timestamps.update(series.keys())

        if not all_timestamps:
            return []

        # Build feature dicts for each timestamp
        merged: list[dict[str, Any]] = []
        for ts in sorted(all_timestamps):
            row: dict[str, Any] = {"timestamp": ts}
            for i, name in enumerate(feature_names):
                series = series_list[i] if i < len(series_list) else {}
                row[name] = series.get(ts, 0.0)
            merged.append(row)

        return merged
