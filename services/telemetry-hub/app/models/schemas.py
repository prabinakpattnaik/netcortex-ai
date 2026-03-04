"""Pydantic models matching the Kafka JSON message schemas."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

class NodeInfo(BaseModel):
    hostname: str
    ip: str
    role: str = ""      # leaf, spine, border
    platform: str = ""  # sonic, linux


# ---------------------------------------------------------------------------
# Metrics (datacenter.metrics)
# ---------------------------------------------------------------------------

class CpuMetrics(BaseModel):
    usage_percent: float
    cores: int
    load_avg: list[float]


class MemoryMetrics(BaseModel):
    total_bytes: int
    used_bytes: int
    usage_percent: float
    avail_bytes: int
    swap_total: int = 0
    swap_used: int = 0


class DiskMetrics(BaseModel):
    device: str
    read_bytes: int = 0
    write_bytes: int = 0
    usage_percent: float
    total_bytes: int
    free_bytes: int


class NetworkMetrics(BaseModel):
    interface: str
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_packets: int = 0
    tx_packets: int = 0
    rx_errors: int = 0
    tx_errors: int = 0
    rx_dropped: int = 0
    tx_dropped: int = 0


# ---------------------------------------------------------------------------
# SONiC-specific: BGP & Hardware
# ---------------------------------------------------------------------------

class BGPNeighborMetrics(BaseModel):
    neighbor_ip: str
    remote_as: int
    state: str  # Established, Active, Idle, Connect, OpenSent
    prefix_rcvd: int = 0
    uptime: str = ""
    msg_sent: int = 0
    msg_rcvd: int = 0
    description: str = ""


class TempSensorMetrics(BaseModel):
    name: str
    current: float
    high: float = 85.0
    crit_high: float = 100.0


class FanStatusMetrics(BaseModel):
    name: str
    speed: int = 0
    speed_pct: float = 0.0
    status: str = "OK"
    direction: str = ""


class PSUStatusMetrics(BaseModel):
    name: str
    status: str = "OK"
    power_watts: float = 0.0
    voltage: float = 0.0
    current_amps: float = 0.0


class HardwareMetrics(BaseModel):
    temperatures: list[TempSensorMetrics] = Field(default_factory=list)
    fans: list[FanStatusMetrics] = Field(default_factory=list)
    psus: list[PSUStatusMetrics] = Field(default_factory=list)


class MetricsMessage(BaseModel):
    agent_id: str
    timestamp: datetime
    node: NodeInfo
    cpu: CpuMetrics
    memory: MemoryMetrics
    disk: list[DiskMetrics] = Field(default_factory=list)
    network: list[NetworkMetrics] = Field(default_factory=list)
    bgp: list[BGPNeighborMetrics] = Field(default_factory=list)
    hardware: Optional[HardwareMetrics] = None


# ---------------------------------------------------------------------------
# Flow records (datacenter.flows)
# ---------------------------------------------------------------------------

class FlowRecord(BaseModel):
    agent_id: str
    timestamp: datetime
    node: NodeInfo
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    protocol: str
    direction: str
    bytes: int
    packets: int
    latency_ms: float = 0.0
    process: Optional[str] = None
    pid: Optional[int] = None


# ---------------------------------------------------------------------------
# Alerts (datacenter.alerts)
# ---------------------------------------------------------------------------

class AlertSeverity(str, enum.Enum):
    WARNING = "warning"
    CRITICAL = "critical"


class AlertMessage(BaseModel):
    alert_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    agent_id: str
    severity: AlertSeverity
    metric: str
    value: float
    threshold: float
    message: str


# ---------------------------------------------------------------------------
# Alert rule (used by AlertHub for runtime management)
# ---------------------------------------------------------------------------

class AlertRule(BaseModel):
    """A single threshold rule evaluated by the alert engine."""

    metric: str
    warning_threshold: float
    critical_threshold: float
    enabled: bool = True
    description: str = ""
