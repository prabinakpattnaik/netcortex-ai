package config

import (
	"os"
	"time"
)

type Config struct {
	NodeID          string
	NodeIP          string
	NodeRole        string // "leaf", "spine", "border"
	Platform        string // "sonic", "linux"
	KafkaBroker     string
	Simulate        bool
	CollectInterval time.Duration
	TopicMetrics    string
	TopicFlows      string
}

func Load() *Config {
	interval, err := time.ParseDuration(getEnv("COLLECT_INTERVAL", "5s"))
	if err != nil {
		interval = 5 * time.Second
	}

	return &Config{
		NodeID:          getEnv("NODE_ID", "agent-01"),
		NodeIP:          getEnv("NODE_IP", "10.0.0.1"),
		NodeRole:        getEnv("NODE_ROLE", "leaf"),
		Platform:        getEnv("PLATFORM", "sonic"),
		KafkaBroker:     getEnv("KAFKA_BROKER", "localhost:9092"),
		Simulate:        getEnv("SIMULATE", "true") == "true",
		CollectInterval: interval,
		TopicMetrics:    getEnv("KAFKA_TOPIC_METRICS", "datacenter.metrics"),
		TopicFlows:      getEnv("KAFKA_TOPIC_FLOWS", "datacenter.flows"),
	}
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
