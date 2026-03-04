"""Application configuration loaded from environment variables via pydantic-settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All tunables are overridable through env vars (or a .env file)."""

    # -- Kafka -----------------------------------------------------------
    kafka_broker: str = "kafka:9092"
    kafka_metrics_topic: str = "datacenter.metrics"
    kafka_flows_topic: str = "datacenter.flows"
    kafka_alerts_topic: str = "datacenter.alerts"
    kafka_consumer_group: str = "telemetry-hub"

    # -- InfluxDB --------------------------------------------------------
    influxdb_url: str = "http://influxdb:8086"
    influxdb_token: str = ""
    influxdb_org: str = "datacenter"
    influxdb_bucket: str = "telemetry"
    influx_batch_size: int = 500
    influx_flush_interval_ms: int = 1_000

    # -- Alert thresholds ------------------------------------------------
    alert_cpu_warning: float = 85.0
    alert_cpu_critical: float = 95.0
    alert_memory_warning: float = 80.0
    alert_memory_critical: float = 92.0
    alert_disk_warning: float = 85.0
    alert_disk_critical: float = 95.0
    alert_network_errors_warning: int = 100
    # SONiC-specific thresholds
    alert_temperature_warning: float = 75.0
    alert_temperature_critical: float = 90.0
    alert_bgp_down_warning: int = 1
    alert_bgp_down_critical: int = 2

    # -- Service ---------------------------------------------------------
    port: int = 8001
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
