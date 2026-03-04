# SONiC AI Datacenter NOC - Demo Guide

## Quick Start

```powershell
# 1. Start all services (4 SONiC edge devices + cloud platform)
cd C:\Users\prabina\Code\AI_Datacenter
docker compose up -d --build

# 2. Wait ~60s for Kafka + InfluxDB to initialize, then verify
docker compose ps

# 3. Open the live NOC dashboard
start demo\dashboard.html
```

## Architecture

```
    ISP / WAN
        |
 ┌──────┴──────┐
 │ sonic-border │  ← SONiC border router (BGP upstream)
 └──────┬──────┘
        |
 ┌──────┴──────┐
 │ sonic-spine  │  ← SONiC spine switch (BGP to leafs)
 └──┬───────┬──┘
    |       |
┌───┴──┐ ┌─┴────┐
│leaf-01│ │leaf-02│ ← SONiC leaf switches (ToR)
└───┬──┘ └─┬────┘
    |       |            ── Kafka ──►   CLOUD PLATFORM
  Rack 1  Rack 2                      ┌──────────────────┐
                                      │ TelemetryHub     │
  Each device runs:                   │ AIPredictor      │
  • InsightAgent (Go)                 │ APIGateway       │
  • EdgeStats collector               │ InfluxDB+Grafana │
  • eBPFScope flow capture            └──────────────────┘
  • Kafka producer
```

---

## Demo Flow (Customer Presentation)

### Act 1: SONiC Topology (2 min)

**Open** `demo\dashboard.html`

Show the leaf-spine topology diagram at the top:
- **sonic-border-01** — ISP/WAN gateway with upstream BGP peering
- **sonic-spine-01** — Fabric spine connecting all leafs
- **sonic-leaf-01/02** — Top-of-rack switches serving server racks

**Key points:**
- Each SONiC device runs a lightweight Go agent inside the container
- Agents collect CPU, memory, network interfaces (Ethernet0/4/8..., PortChannels), BGP sessions, hardware health (temps, fans, PSUs)
- Kafka decouples edge from cloud (messages flow even if cloud restarts)

---

### Act 2: Live Telemetry (3 min)

Point to the dashboard sections:
- **Stats bar** — SONiC devices count, BGP up/total, metrics ingested
- **Device cards** — real-time CPU, memory, interface counts, BGP peers per switch
- **BGP panel** — per-device neighbor table with state (Established/Active)
- **Hardware panel** — temperatures, fan speeds, PSU power draw

**Show Kafka UI** → http://localhost:8080
- Topics tab → `datacenter.metrics`, `datacenter.flows`, `datacenter.alerts`, `datacenter.predictions`
- Click a message in `datacenter.metrics` → show the full SONiC JSON (BGP, hardware, 48 interfaces)

**Talking point:** "Every 5 seconds, each SONiC switch publishes complete device state — CPU, memory, all 48 interfaces, BGP sessions, fan speeds, temperatures. This is the same data you'd see in `show interfaces counters` and `show bgp summary` on a real switch."

---

### Act 3: AI/ML Live Demo (5 min) — THE SHOWSTOPPER

This is the core demo moment. The dashboard has three **Inject Fault** buttons.

#### Step 1: Show the AI Pipeline
Point to the "AI INSIGHT ENGINE" panel:
- **Kafka Ingest** → samples consumed from the stream
- **Anomaly Detection** → Isolation Forest counter
- **Fault Prediction** → trend extrapolation counter
- **Root Cause** → correlation analysis counter

Explain: "The AI engine runs three ML models in a pipeline. It consumes every metric from Kafka, analyzes it, and publishes predictions back."

#### Step 2: Trigger CPU Spike
Click **"CPU Spike"** button.

What happens (within 5-10 seconds):
1. Dashboard injects 5 high-CPU metrics for `sonic-leaf-01`
2. The leaf-01 device card turns red (92-98% CPU)
3. AI detects anomaly → "Anomaly Detection" counter increments
4. Fault predictor fires → "thermal_throttling in X minutes"
5. Root cause analysis → "cpu_usage is the primary contributor"
6. Alert appears in the Alerts panel

**Talking point:** "Watch — I'm simulating a CPU spike on a leaf switch. The AI catches it in seconds. It identifies it as anomalous, predicts thermal throttling, and pinpoints CPU as the root cause."

#### Step 3: Trigger BGP Flap
Click **"BGP Flap"** button.

What happens:
1. spine-01 reports 2 BGP neighbors in "Active" (down) state
2. BGP panel shows red "Active" badges
3. Stats bar shows reduced BGP up count
4. Network errors spike in the metrics
5. AI detects anomaly with `bgp_down_count` and `net_errors` as affected metrics

**Talking point:** "A BGP session flap — two leaf switches just lost peering. The AI correlates the BGP state change with the network error increase."

#### Step 4: Trigger Thermal Alarm
Click **"Thermal Alarm"** button.

What happens:
1. leaf-02 shows ASIC at 92-98°C (above high threshold)
2. Hardware panel shows red temperature + fan failure (FAN2 = NOT OK)
3. PSU2 also shows failure
4. AI predicts "switch_overheat" fault
5. Root cause: temperature_max correlated with fan failure

**Talking point:** "A real-world scenario — a fan fails, ASIC temperature climbs, PSU overloads. The AI doesn't just alert on temperature; it predicts the switch will overheat and identifies the failed fan as root cause."

---

### Act 4: AI Model Deep Dive (3 min)

```powershell
# Show model status (training state, buffer sizes per device)
Invoke-RestMethod http://localhost:8002/status | ConvertTo-Json -Depth 5

# List the 3 ML models
Invoke-RestMethod http://localhost:8002/models | ConvertTo-Json

# Manual anomaly detection via REST API
$body = '{"agent_id":"sonic-leaf-01","cpu":{"usage_percent":95},"memory":{"usage_percent":80},"disk":{"usage_percent":30},"network":{"rx_bytes":1000,"tx_bytes":500}}'
Invoke-RestMethod -Uri http://localhost:8002/predict/anomaly -Method Post -Body $body -ContentType "application/json" | ConvertTo-Json

# Full pipeline (anomaly + fault + RCA)
Invoke-RestMethod -Uri http://localhost:8002/predict -Method Post -Body $body -ContentType "application/json" | ConvertTo-Json -Depth 5
```

**Key ML features:**
| Model | Algorithm | What It Does |
|-------|-----------|-------------|
| Anomaly Detection | Isolation Forest (scikit-learn) | Detects unusual metric patterns across 8 features |
| Fault Prediction | Linear Regression Trend | Extrapolates metrics to predict faults 30 min ahead |
| Root Cause Analysis | Pearson Correlation | Identifies which metrics are driving the anomaly |

**Features analyzed:** cpu_usage, mem_usage, disk_usage, net_rx, net_tx, net_errors, temperature_max, bgp_down_count

---

### Act 5: Grafana (2 min)

**Open** http://localhost:3000 (admin/admin)

Dashboard: "SONiC Datacenter NOC"

Show panel sections:
- **Overview** — CPU/Memory gauges per device, BGP status, temperatures
- **CPU & Memory Trends** — time-series per device with anomaly spikes visible
- **BGP & Routing** — prefixes received, session state (1=up, 0=down) over time
- **Hardware Health** — temperature sensors, fan speed %, PSU power watts
- **Network Interfaces** — PortChannel traffic, interface errors/drops
- **eBPF Flows** — protocol breakdown, latency distribution

Use the **device** dropdown to filter by specific switch.

---

### Act 6: REST API (1 min)

**Open** http://localhost:8000/docs (Swagger UI)

Key endpoints:
```
GET  /api/v1/health                → Platform health
GET  /api/v1/metrics               → Historical metrics (InfluxDB)
GET  /api/v1/metrics/{agent}       → Real-time snapshot for one device
GET  /api/v1/alerts                → Recent alerts
GET  /api/v1/predictions           → AI predictions (filterable by model)
GET  /api/v1/demo/scenarios        → Available fault scenarios
POST /api/v1/demo/trigger/{id}     → Inject fault for demo
WS   /ws/stream                    → WebSocket real-time stream
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Services not starting | `docker compose logs kafka` — wait for Kafka healthcheck (~30s) |
| No data in dashboard | Wait 30-60s for agents to start publishing |
| AI not predicting | Model needs ~100 samples to train (~8 min at 5s interval) |
| Use demo triggers | Click the fault buttons — they bypass the training wait |
| WebSocket disconnected | Check api-gateway: `docker compose logs api-gateway` |
| Build fails | `docker compose build --no-cache` to rebuild |

## Service Ports

| Service | Port | URL |
|---------|------|-----|
| API Gateway | 8000 | http://localhost:8000/docs |
| Telemetry Hub | 8001 | http://localhost:8001/health |
| AI Predictor | 8002 | http://localhost:8002/status |
| Grafana | 3000 | http://localhost:3000 |
| Kafka UI | 8080 | http://localhost:8080 |
| InfluxDB | 8086 | http://localhost:8086 |

## SONiC Edge Devices

| Container | Role | IP | BGP Peers |
|-----------|------|-----|-----------|
| sonic-spine-01 | Spine | 10.0.0.1 | 4 leafs |
| sonic-leaf-01 | Leaf | 10.0.1.1 | 2 spines |
| sonic-leaf-02 | Leaf | 10.0.1.2 | 2 spines |
| sonic-border-01 | Border | 10.0.2.1 | 2 spines + ISP |
