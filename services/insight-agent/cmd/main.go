package main

import (
	"context"
	"log"
	"net"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/ai-datacenter/insight-agent/internal/collector"
	"github.com/ai-datacenter/insight-agent/internal/config"
	"github.com/ai-datacenter/insight-agent/internal/relay"
)

func main() {
	cfg := config.Load()

	log.Printf("=== InsightAgent Starting ===")
	log.Printf("  Node ID:    %s", cfg.NodeID)
	log.Printf("  Node IP:    %s", cfg.NodeIP)
	log.Printf("  Kafka:      %s", cfg.KafkaBroker)
	log.Printf("  Simulate:   %v", cfg.Simulate)
	log.Printf("  Interval:   %s", cfg.CollectInterval)
	log.Printf("=============================")

	// Initialize collectors based on mode
	var metricCollector collector.MetricCollector
	var flowCollector collector.FlowCollector

	if cfg.Simulate {
		log.Println("[Mode] Simulation - generating synthetic metrics")
		sim := collector.NewSimulator(cfg)
		metricCollector = sim
		flowCollector = sim
	} else {
		log.Println("[Mode] Real - collecting actual system metrics")
		metricCollector = collector.NewEdgeStats(cfg)
		flowCollector = collector.NewEBPFScope(cfg)
	}

	// Wait for Kafka to be ready
	log.Println("Waiting for Kafka broker...")
	waitForKafka(cfg.KafkaBroker)
	log.Println("Kafka broker is ready")

	// Initialize Kafka producer (MessageRelay)
	messageRelay := relay.NewMessageRelay(cfg)
	defer messageRelay.Close()

	// Context with cancellation for graceful shutdown
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Handle shutdown signals
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	// Main collection loop
	ticker := time.NewTicker(cfg.CollectInterval)
	defer ticker.Stop()

	log.Printf("Starting collection loop (every %s)...", cfg.CollectInterval)

	for {
		select {
		case <-ticker.C:
			// Collect and publish metrics
			metrics, err := metricCollector.CollectMetrics()
			if err != nil {
				log.Printf("[ERROR] Collect metrics: %v", err)
				continue
			}

			if err := messageRelay.PublishMetrics(ctx, metrics); err != nil {
				log.Printf("[ERROR] Publish metrics: %v", err)
			}

			// Collect and publish flows
			flows, err := flowCollector.CollectFlows()
			if err != nil {
				log.Printf("[ERROR] Collect flows: %v", err)
				continue
			}

			if err := messageRelay.PublishFlows(ctx, flows); err != nil {
				log.Printf("[ERROR] Publish flows: %v", err)
			}

		case sig := <-sigCh:
			log.Printf("Received signal %v, shutting down...", sig)
			cancel()
			return
		}
	}
}

func waitForKafka(broker string) {
	for i := 0; i < 30; i++ {
		conn, err := net.DialTimeout("tcp", broker, 2*time.Second)
		if err == nil {
			conn.Close()
			return
		}
		log.Printf("  Kafka not ready, retrying in 2s... (%d/30)", i+1)
		time.Sleep(2 * time.Second)
	}
	log.Fatal("Failed to connect to Kafka after 60s")
}
