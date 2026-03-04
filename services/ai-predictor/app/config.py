"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Settings for the AIPredictor service."""

    # Kafka
    KAFKA_BROKER: str = "kafka:9092"
    KAFKA_METRICS_TOPIC: str = "datacenter.metrics"
    KAFKA_PREDICTIONS_TOPIC: str = "datacenter.predictions"
    KAFKA_CONSUMER_GROUP: str = "ai-predictor-group"

    # InfluxDB — reads historical data written by Telemetry Hub
    INFLUXDB_URL: str = "http://influxdb:8086"
    INFLUXDB_TOKEN: str = ""
    INFLUXDB_ORG: str = "datacenter"
    INFLUXDB_BUCKET: str = "metrics"

    # InfluxDB query parameters
    INFLUX_LOOKBACK_MINUTES: int = 30        # how far back to query for ML
    INFLUX_MIN_HISTORY_POINTS: int = 10      # minimum data points to run ML
    PREDICT_COOLDOWN_SECONDS: int = 15       # debounce: don't re-query same agent within N seconds

    # Service
    PORT: int = 8002
    SERVICE_NAME: str = "ai-predictor"

    # ML model parameters
    SLIDING_WINDOW_SIZE: int = 100
    ANOMALY_CONTAMINATION: float = 0.1
    FAULT_PREDICTION_HORIZON_MINUTES: int = 30
    FAULT_THRESHOLD_CPU: float = 95.0
    FAULT_THRESHOLD_MEMORY: float = 95.0
    FAULT_THRESHOLD_DISK: float = 95.0
    FAULT_THRESHOLD_NET_ERRORS: float = 50.0
    FAULT_THRESHOLD_TEMPERATURE: float = 85.0

    # Bayesian Network parameters
    BAYESIAN_ALERT_THRESHOLD: float = 0.3  # min probability to report a fault

    model_config = {"env_prefix": "", "case_sensitive": True}


settings = Settings()
