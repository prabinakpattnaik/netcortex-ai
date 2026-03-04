package relay

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"time"

	"github.com/ai-datacenter/insight-agent/internal/config"
	"github.com/segmentio/kafka-go"
)

// MessageRelay publishes metrics and flows to Kafka topics
type MessageRelay struct {
	metricsWriter *kafka.Writer
	flowsWriter   *kafka.Writer
	cfg           *config.Config
}

func NewMessageRelay(cfg *config.Config) *MessageRelay {
	return &MessageRelay{
		cfg: cfg,
		metricsWriter: &kafka.Writer{
			Addr:         kafka.TCP(cfg.KafkaBroker),
			Topic:        cfg.TopicMetrics,
			Balancer:     &kafka.RoundRobin{},
			BatchTimeout: 100 * time.Millisecond,
			RequiredAcks: kafka.RequireOne,
		},
		flowsWriter: &kafka.Writer{
			Addr:         kafka.TCP(cfg.KafkaBroker),
			Topic:        cfg.TopicFlows,
			Balancer:     &kafka.RoundRobin{},
			BatchTimeout: 100 * time.Millisecond,
			RequiredAcks: kafka.RequireOne,
		},
	}
}

// PublishMetrics sends node metrics to the metrics topic
func (r *MessageRelay) PublishMetrics(ctx context.Context, data interface{}) error {
	payload, err := json.Marshal(data)
	if err != nil {
		return fmt.Errorf("marshal metrics: %w", err)
	}

	msg := kafka.Message{
		Key:   []byte(r.cfg.NodeID),
		Value: payload,
		Time:  time.Now(),
	}

	if err := r.metricsWriter.WriteMessages(ctx, msg); err != nil {
		return fmt.Errorf("publish metrics: %w", err)
	}

	log.Printf("[MessageRelay] Published metrics to %s (%d bytes)", r.cfg.TopicMetrics, len(payload))
	return nil
}

// PublishFlows sends flow events to the flows topic
func (r *MessageRelay) PublishFlows(ctx context.Context, flows interface{}) error {
	payload, err := json.Marshal(flows)
	if err != nil {
		return fmt.Errorf("marshal flows: %w", err)
	}

	msg := kafka.Message{
		Key:   []byte(r.cfg.NodeID),
		Value: payload,
		Time:  time.Now(),
	}

	if err := r.flowsWriter.WriteMessages(ctx, msg); err != nil {
		return fmt.Errorf("publish flows: %w", err)
	}

	log.Printf("[MessageRelay] Published flows to %s (%d bytes)", r.cfg.TopicFlows, len(payload))
	return nil
}

// Close shuts down the Kafka writers
func (r *MessageRelay) Close() error {
	if err := r.metricsWriter.Close(); err != nil {
		return err
	}
	return r.flowsWriter.Close()
}
