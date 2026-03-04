.PHONY: build up down logs restart clean status \
       cloud-up cloud-down cloud-logs cloud-status \
       edge-up edge-down edge-logs agent-build

# ════════════════════════════════════════════════
#  LOCAL DEV / DEMO (single machine)
# ════════════════════════════════════════════════

# Build all services
build:
	docker compose build

# Start all services
up:
	docker compose up -d

# Start with build
up-build:
	docker compose up -d --build

# Stop all services
down:
	docker compose down

# Stop and remove volumes
clean:
	docker compose down -v --remove-orphans

# View logs (all services)
logs:
	docker compose logs -f

# View logs for specific service
logs-%:
	docker compose logs -f $*

# Restart a specific service
restart-%:
	docker compose restart $*

# Show service status
status:
	docker compose ps

# Rebuild and restart a specific service
rebuild-%:
	docker compose up -d --build $*

# Create Kafka topics manually (if needed)
topics:
	docker compose exec kafka kafka-topics.sh --bootstrap-server localhost:9092 --create --if-not-exists --topic datacenter.metrics --partitions 3 --replication-factor 1
	docker compose exec kafka kafka-topics.sh --bootstrap-server localhost:9092 --create --if-not-exists --topic datacenter.flows --partitions 3 --replication-factor 1
	docker compose exec kafka kafka-topics.sh --bootstrap-server localhost:9092 --create --if-not-exists --topic datacenter.alerts --partitions 1 --replication-factor 1
	docker compose exec kafka kafka-topics.sh --bootstrap-server localhost:9092 --create --if-not-exists --topic datacenter.predictions --partitions 1 --replication-factor 1

# ════════════════════════════════════════════════
#  MULTI-VM DEPLOYMENT — Cloud VM (VM4)
#  Kafka + InfluxDB + Grafana + Microservices
# ════════════════════════════════════════════════

cloud-up:
	docker compose -f docker-compose.cloud.yml --env-file .env.cloud up -d --build

cloud-down:
	docker compose -f docker-compose.cloud.yml --env-file .env.cloud down

cloud-clean:
	docker compose -f docker-compose.cloud.yml --env-file .env.cloud down -v --remove-orphans

cloud-logs:
	docker compose -f docker-compose.cloud.yml --env-file .env.cloud logs -f

cloud-status:
	docker compose -f docker-compose.cloud.yml --env-file .env.cloud ps

# ════════════════════════════════════════════════
#  MULTI-VM DEPLOYMENT — Edge VMs (VM1-VM3)
#  InsightAgent with FRR platform
# ════════════════════════════════════════════════

edge-up:
	docker compose -f docker-compose.edge.yml --env-file .env.edge up -d --build

edge-down:
	docker compose -f docker-compose.edge.yml --env-file .env.edge down

edge-logs:
	docker compose -f docker-compose.edge.yml --env-file .env.edge logs -f

# ════════════════════════════════════════════════
#  NATIVE BUILD — InsightAgent binary
#  For systemd deployment (no Docker on edge)
# ════════════════════════════════════════════════

agent-build:
	cd services/insight-agent && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o ../../bin/insight-agent ./cmd/main.go
	@echo "Binary: bin/insight-agent (Linux amd64, static)"
