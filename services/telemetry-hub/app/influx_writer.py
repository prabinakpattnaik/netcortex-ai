"""Async batch writer for InfluxDB 2.7.

Receives parsed MetricsMessage / FlowRecord objects and converts them into
InfluxDB line-protocol points, which are flushed in configurable batches.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from influxdb_client import Point, WritePrecision
from influxdb_client.client.influxdb_client_async import InfluxDBClientAsync
from influxdb_client.client.write.retry import WritesRetry

from .config import settings

if TYPE_CHECKING:
    from .models.schemas import FlowRecord, MetricsMessage

logger = logging.getLogger(__name__)


class InfluxWriter:
    """Manages an async InfluxDB client with automatic batching."""

    def __init__(self) -> None:
        self._client: InfluxDBClientAsync | None = None

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        retries = WritesRetry(total=3, backoff_factor=0.5)
        self._client = InfluxDBClientAsync(
            url=settings.influxdb_url,
            token=settings.influxdb_token,
            org=settings.influxdb_org,
            enable_gzip=True,
        )
        logger.info(
            "InfluxDB writer connected to %s (org=%s, bucket=%s)",
            settings.influxdb_url,
            settings.influxdb_org,
            settings.influxdb_bucket,
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None
            logger.info("InfluxDB writer closed")

    # -- health ----------------------------------------------------------

    async def healthy(self) -> bool:
        if not self._client:
            return False
        try:
            return await self._client.ping()
        except Exception:
            return False

    # -- write helpers ---------------------------------------------------

    async def write_metrics(self, msg: MetricsMessage) -> None:
        """Convert a MetricsMessage into multiple InfluxDB points and write."""
        points: list[Point] = []
        ts = msg.timestamp
        role = msg.node.role or "unknown"
        platform = msg.node.platform or "linux"

        # CPU point
        points.append(
            Point("cpu")
            .tag("agent_id", msg.agent_id)
            .tag("hostname", msg.node.hostname)
            .tag("role", role)
            .tag("platform", platform)
            .field("usage_percent", msg.cpu.usage_percent)
            .field("cores", msg.cpu.cores)
            .field("load_1m", msg.cpu.load_avg[0] if msg.cpu.load_avg else 0.0)
            .field("load_5m", msg.cpu.load_avg[1] if len(msg.cpu.load_avg) > 1 else 0.0)
            .field("load_15m", msg.cpu.load_avg[2] if len(msg.cpu.load_avg) > 2 else 0.0)
            .time(ts, WritePrecision.MS)
        )

        # Memory point
        points.append(
            Point("memory")
            .tag("agent_id", msg.agent_id)
            .tag("hostname", msg.node.hostname)
            .tag("role", role)
            .tag("platform", platform)
            .field("total_bytes", msg.memory.total_bytes)
            .field("used_bytes", msg.memory.used_bytes)
            .field("usage_percent", msg.memory.usage_percent)
            .field("avail_bytes", msg.memory.avail_bytes)
            .field("swap_total", msg.memory.swap_total)
            .field("swap_used", msg.memory.swap_used)
            .time(ts, WritePrecision.MS)
        )

        # Disk points (one per device)
        for d in msg.disk:
            points.append(
                Point("disk")
                .tag("agent_id", msg.agent_id)
                .tag("hostname", msg.node.hostname)
                .tag("role", role)
                .tag("device", d.device)
                .field("read_bytes", d.read_bytes)
                .field("write_bytes", d.write_bytes)
                .field("usage_percent", d.usage_percent)
                .field("total_bytes", d.total_bytes)
                .field("free_bytes", d.free_bytes)
                .time(ts, WritePrecision.MS)
            )

        # Network points (one per interface)
        for n in msg.network:
            points.append(
                Point("network")
                .tag("agent_id", msg.agent_id)
                .tag("hostname", msg.node.hostname)
                .tag("role", role)
                .tag("interface", n.interface)
                .field("rx_bytes", n.rx_bytes)
                .field("tx_bytes", n.tx_bytes)
                .field("rx_packets", n.rx_packets)
                .field("tx_packets", n.tx_packets)
                .field("rx_errors", n.rx_errors)
                .field("tx_errors", n.tx_errors)
                .field("rx_dropped", n.rx_dropped)
                .field("tx_dropped", n.tx_dropped)
                .time(ts, WritePrecision.MS)
            )

        # BGP neighbor points (SONiC)
        for bgp in msg.bgp:
            state_val = 1 if bgp.state == "Established" else 0
            points.append(
                Point("bgp")
                .tag("agent_id", msg.agent_id)
                .tag("hostname", msg.node.hostname)
                .tag("role", role)
                .tag("neighbor_ip", bgp.neighbor_ip)
                .tag("description", bgp.description)
                .field("state", bgp.state)
                .field("state_up", state_val)
                .field("remote_as", bgp.remote_as)
                .field("prefix_rcvd", bgp.prefix_rcvd)
                .field("msg_sent", bgp.msg_sent)
                .field("msg_rcvd", bgp.msg_rcvd)
                .time(ts, WritePrecision.MS)
            )

        # Hardware points (SONiC)
        if msg.hardware:
            for t in msg.hardware.temperatures:
                points.append(
                    Point("temperature")
                    .tag("agent_id", msg.agent_id)
                    .tag("hostname", msg.node.hostname)
                    .tag("role", role)
                    .tag("sensor", t.name)
                    .field("current", t.current)
                    .field("high", t.high)
                    .field("crit_high", t.crit_high)
                    .time(ts, WritePrecision.MS)
                )
            for f in msg.hardware.fans:
                points.append(
                    Point("fan")
                    .tag("agent_id", msg.agent_id)
                    .tag("hostname", msg.node.hostname)
                    .tag("role", role)
                    .tag("fan", f.name)
                    .tag("direction", f.direction)
                    .field("speed", f.speed)
                    .field("speed_pct", f.speed_pct)
                    .field("status_ok", 1 if f.status == "OK" else 0)
                    .time(ts, WritePrecision.MS)
                )
            for p in msg.hardware.psus:
                points.append(
                    Point("psu")
                    .tag("agent_id", msg.agent_id)
                    .tag("hostname", msg.node.hostname)
                    .tag("role", role)
                    .tag("psu", p.name)
                    .field("power_watts", p.power_watts)
                    .field("voltage", p.voltage)
                    .field("current_amps", p.current_amps)
                    .field("status_ok", 1 if p.status == "OK" else 0)
                    .time(ts, WritePrecision.MS)
                )

        await self._write(points)

    async def write_flows(self, flows: list[FlowRecord]) -> None:
        """Convert a list of FlowRecords into InfluxDB points and write."""
        points: list[Point] = []
        for f in flows:
            points.append(
                Point("flow")
                .tag("agent_id", f.agent_id)
                .tag("hostname", f.node.hostname)
                .tag("protocol", f.protocol)
                .tag("direction", f.direction)
                .tag("process", f.process or "unknown")
                .field("src_ip", f.src_ip)
                .field("dst_ip", f.dst_ip)
                .field("src_port", f.src_port)
                .field("dst_port", f.dst_port)
                .field("bytes", f.bytes)
                .field("packets", f.packets)
                .field("latency_ms", f.latency_ms)
                .time(f.timestamp, WritePrecision.MS)
            )
        await self._write(points)

    # -- internal --------------------------------------------------------

    async def _write(self, points: list[Point]) -> None:
        if not self._client or not points:
            return
        write_api = self._client.write_api()
        try:
            await write_api.write(
                bucket=settings.influxdb_bucket,
                org=settings.influxdb_org,
                record=points,
                write_precision=WritePrecision.MS,
            )
            logger.debug("Wrote %d points to InfluxDB", len(points))
        except Exception:
            logger.exception("Failed to write %d points to InfluxDB", len(points))
