"""Demo trigger endpoints for live customer demos.

Injects fault scenarios directly into Kafka so the AI Predictor
pipeline can detect and respond to them in real-time.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from typing import Any

from aiokafka import AIOKafkaProducer
from fastapi import APIRouter, HTTPException

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/demo", tags=["demo"])

# Reusable producer (lazy init)
_producer: AIOKafkaProducer | None = None


async def _get_producer() -> AIOKafkaProducer:
    global _producer
    if _producer is None:
        _producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_broker,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )
        await _producer.start()
    return _producer


# ---------------------------------------------------------------------------
# Scenario builders
# ---------------------------------------------------------------------------

def _build_cpu_spike(device: str) -> dict[str, Any]:
    """CPU spike scenario: sudden 95%+ CPU on a switch."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "agent_id": device,
        "timestamp": now,
        "node": {"hostname": device, "ip": "10.0.1.1", "role": "leaf", "platform": "sonic"},
        "cpu": {"usage_percent": 92 + random.random() * 6, "cores": 4, "load_avg": [3.8, 3.2, 2.5]},
        "memory": {"total_bytes": 17179869184, "used_bytes": 13958643712, "usage_percent": 81.2, "avail_bytes": 3221225472, "swap_total": 0, "swap_used": 0},
        "disk": [{"device": "/dev/sda", "read_bytes": 50000, "write_bytes": 30000, "usage_percent": 35.0, "total_bytes": 34359738368, "free_bytes": 22333849804}],
        "network": [{"interface": "Ethernet0", "rx_bytes": 150000000, "tx_bytes": 120000000, "rx_packets": 187500, "tx_packets": 150000, "rx_errors": 12, "tx_errors": 5, "rx_dropped": 8, "tx_dropped": 3}],
        "bgp": [
            {"neighbor_ip": "10.0.0.1", "remote_as": 65100, "state": "Established", "prefix_rcvd": 350, "uptime": "72h0m0s", "msg_sent": 25000, "msg_rcvd": 24000, "description": "spine-01"},
        ],
        "hardware": {
            "temperatures": [
                {"name": "CPU Core", "current": 78 + random.random() * 8, "high": 85, "crit_high": 100},
                {"name": "ASIC Memory", "current": 72 + random.random() * 6, "high": 95, "crit_high": 110},
            ],
            "fans": [{"name": "FAN1", "speed": 11000, "speed_pct": 95.0, "status": "OK", "direction": "intake"}],
            "psus": [{"name": "PSU1", "status": "OK", "power_watts": 310.0, "voltage": 12.1, "current_amps": 25.6}],
        },
    }


def _build_bgp_flap(device: str) -> dict[str, Any]:
    """BGP flap scenario: neighbor goes down."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "agent_id": device,
        "timestamp": now,
        "node": {"hostname": device, "ip": "10.0.0.1", "role": "spine", "platform": "sonic"},
        "cpu": {"usage_percent": 45 + random.random() * 15, "cores": 4, "load_avg": [1.8, 1.5, 1.2]},
        "memory": {"total_bytes": 17179869184, "used_bytes": 8589934592, "usage_percent": 50.0, "avail_bytes": 8589934592, "swap_total": 0, "swap_used": 0},
        "disk": [{"device": "/dev/sda", "read_bytes": 30000, "write_bytes": 20000, "usage_percent": 28.0, "total_bytes": 34359738368, "free_bytes": 24738954035}],
        "network": [
            {"interface": "Ethernet0", "rx_bytes": 50000000, "tx_bytes": 40000000, "rx_packets": 62500, "tx_packets": 50000, "rx_errors": 45, "tx_errors": 22, "rx_dropped": 30, "tx_dropped": 15},
        ],
        "bgp": [
            {"neighbor_ip": "10.0.1.1", "remote_as": 65001, "state": "Established", "prefix_rcvd": 280, "uptime": "48h0m0s", "msg_sent": 20000, "msg_rcvd": 19500, "description": "leaf-01"},
            {"neighbor_ip": "10.0.1.2", "remote_as": 65002, "state": "Active", "prefix_rcvd": 0, "uptime": "0s", "msg_sent": 15000, "msg_rcvd": 14800, "description": "leaf-02"},
            {"neighbor_ip": "10.0.1.3", "remote_as": 65003, "state": "Active", "prefix_rcvd": 0, "uptime": "0s", "msg_sent": 12000, "msg_rcvd": 11500, "description": "leaf-03"},
        ],
        "hardware": {
            "temperatures": [
                {"name": "CPU Core", "current": 48 + random.random() * 5, "high": 85, "crit_high": 100},
                {"name": "ASIC Memory", "current": 58 + random.random() * 4, "high": 95, "crit_high": 110},
            ],
            "fans": [{"name": "FAN1", "speed": 8500, "speed_pct": 65.0, "status": "OK", "direction": "intake"}],
            "psus": [{"name": "PSU1", "status": "OK", "power_watts": 260.0, "voltage": 12.0, "current_amps": 21.7}],
        },
    }


def _build_thermal_alarm(device: str) -> dict[str, Any]:
    """Thermal alarm: ASIC overheating + fan failure."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "agent_id": device,
        "timestamp": now,
        "node": {"hostname": device, "ip": "10.0.1.2", "role": "leaf", "platform": "sonic"},
        "cpu": {"usage_percent": 72 + random.random() * 10, "cores": 4, "load_avg": [2.9, 2.5, 2.0]},
        "memory": {"total_bytes": 17179869184, "used_bytes": 12884901888, "usage_percent": 75.0, "avail_bytes": 4294967296, "swap_total": 0, "swap_used": 0},
        "disk": [{"device": "/dev/sda", "read_bytes": 40000, "write_bytes": 25000, "usage_percent": 30.0, "total_bytes": 34359738368, "free_bytes": 24051816858}],
        "network": [{"interface": "Ethernet0", "rx_bytes": 80000000, "tx_bytes": 65000000, "rx_packets": 100000, "tx_packets": 81250, "rx_errors": 3, "tx_errors": 1, "rx_dropped": 2, "tx_dropped": 1}],
        "bgp": [
            {"neighbor_ip": "10.0.0.1", "remote_as": 65100, "state": "Established", "prefix_rcvd": 310, "uptime": "120h0m0s", "msg_sent": 30000, "msg_rcvd": 29500, "description": "spine-01"},
        ],
        "hardware": {
            "temperatures": [
                {"name": "CPU Core", "current": 82 + random.random() * 8, "high": 85, "crit_high": 100},
                {"name": "ASIC Memory", "current": 92 + random.random() * 6, "high": 95, "crit_high": 110},
                {"name": "Ambient", "current": 45 + random.random() * 5, "high": 50, "crit_high": 60},
            ],
            "fans": [
                {"name": "FAN1", "speed": 11500, "speed_pct": 98.0, "status": "OK", "direction": "intake"},
                {"name": "FAN2", "speed": 0, "speed_pct": 0.0, "status": "NOT OK", "direction": "intake"},
            ],
            "psus": [
                {"name": "PSU1", "status": "OK", "power_watts": 340.0, "voltage": 11.8, "current_amps": 28.8},
                {"name": "PSU2", "status": "NOT OK", "power_watts": 0.0, "voltage": 0.0, "current_amps": 0.0},
            ],
        },
    }


SCENARIOS = {
    "cpu_spike": ("CPU spike on leaf switch", _build_cpu_spike, "sonic-leaf-01"),
    "bgp_flap": ("BGP session flap on spine", _build_bgp_flap, "sonic-spine-01"),
    "thermal": ("Thermal alarm with fan failure", _build_thermal_alarm, "sonic-leaf-02"),
}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/scenarios")
async def list_scenarios() -> dict[str, Any]:
    """List available demo fault scenarios."""
    return {
        "scenarios": [
            {"id": k, "description": desc, "target_device": dev}
            for k, (desc, _, dev) in SCENARIOS.items()
        ]
    }


@router.post("/trigger/{scenario_id}")
async def trigger_scenario(scenario_id: str, count: int = 5) -> dict[str, Any]:
    """Inject a fault scenario into Kafka.

    Sends ``count`` metric messages with the anomalous data so the
    AI Predictor's sliding window picks up the pattern.
    """
    if scenario_id not in SCENARIOS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown scenario '{scenario_id}'. Available: {list(SCENARIOS.keys())}",
        )

    description, builder, device = SCENARIOS[scenario_id]
    producer = await _get_producer()

    sent = 0
    for _ in range(count):
        msg = builder(device)
        await producer.send_and_wait("datacenter.metrics", value=msg)
        sent += 1

    logger.info("Demo trigger: sent %d '%s' messages for %s", sent, scenario_id, device)

    return {
        "scenario": scenario_id,
        "description": description,
        "target_device": device,
        "messages_sent": sent,
        "status": "injected",
    }
