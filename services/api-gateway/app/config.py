"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """API Gateway settings populated from environment variables."""

    # Kafka
    kafka_broker: str = "kafka:9092"

    # InfluxDB
    influxdb_url: str = "http://influxdb:8086"
    influxdb_token: str = ""
    influxdb_org: str = "datacenter"
    influxdb_bucket: str = "telemetry"

    # Internal services
    telemetry_hub_url: str = "http://telemetry-hub:8001"
    ai_predictor_url: str = "http://ai-predictor:8002"

    # Server
    port: int = 8000

    # Kafka consumer group
    kafka_group_id: str = "api-gateway"

    # Buffer sizes for in-memory stores
    alerts_buffer_size: int = 1000
    predictions_buffer_size: int = 1000

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
