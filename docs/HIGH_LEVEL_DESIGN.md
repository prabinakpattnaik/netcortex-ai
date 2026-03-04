# SONiC AI Datacenter Monitoring Platform — High-Level Design

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        EDGE LAYER (Datacenter)                         │
│                                                                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │sonic-border-01│  │sonic-spine-01│  │sonic-leaf-01 │  │sonic-leaf-02│ │
│  │  (Border)     │  │  (Spine)     │  │  (Leaf/ToR)  │  │ (Leaf/ToR) │ │
│  │              │  │              │  │              │  │            │ │
│  │ InsightAgent │  │ InsightAgent │  │ InsightAgent │  │InsightAgent│ │
│  │  (Go 1.23)  │  │  (Go 1.23)  │  │  (Go 1.23)  │  │ (Go 1.23) │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └─────┬──────┘ │
│         │                 │                 │                │        │
│         │   edge-fabric network (10.10.0.0/24)               │        │
└─────────┼─────────────────┼─────────────────┼────────────────┼────────┘
          │                 │                 │                │
          └────────┬────────┴────────┬────────┘────────────────┘
                   │     Kafka Producer (segmentio/kafka-go)
                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     EVENT BUS (Apache Kafka 3.9 KRaft)                 │
│                                                                        │
│   ┌───────────────────┐  ┌──────────────────┐  ┌───────────────────┐  │
│   │datacenter.metrics │  │ datacenter.flows │  │datacenter.alerts  │  │
│   │  (3 partitions)   │  │ (3 partitions)   │  │ (3 partitions)   │  │
│   └─────────┬─────────┘  └────────┬─────────┘  └────────┬──────────┘  │
│             │                     │                     ▲              │
│   ┌─────────┴──────────────────────────────────────┐    │              │
│   │              datacenter.predictions            │    │              │
│   │               (3 partitions)                   │    │              │
│   └─────────┬──────────────────────────────────────┘    │              │
│             │                                           │              │
└─────────────┼───────────────────────────────────────────┼──────────────┘
              │              dc-network                   │
┌─────────────┼───────────────────────────────────────────┼──────────────┐
│             ▼           CLOUD PLATFORM                  │              │
│                                                                        │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────┐ │
│  │  Telemetry Hub   │  │   AI Predictor   │  │    API Gateway       │ │
│  │  (Python/FastAPI) │  │ (Python/FastAPI) │  │  (Python/FastAPI)    │ │
│  │                  │  │                  │  │                      │ │
│  │ Kafka Consumer   │  │ Kafka Consumer   │  │ Kafka Consumer       │ │
│  │ InfluxDB Writer  │  │ InsightAI Engine │  │ REST API + WebSocket │ │
│  │ Alert Hub ───────┼──┤ Process Engine   │  │ Demo Trigger API     │ │
│  └───────┬──────────┘  └────────┬─────────┘  └──────────┬───────────┘ │
│          │                      │                        │             │
│          ▼                      │                        ▼             │
│  ┌──────────────────┐           │             ┌──────────────────────┐ │
│  │   InfluxDB 2.7   │           │             │  Live NOC Dashboard  │ │
│  │  (Time-series)   │           │             │  (HTML + WebSocket)  │ │
│  └───────┬──────────┘           │             └──────────────────────┘ │
│          ▼                      │                                      │
│  ┌──────────────────┐           │             ┌──────────────────────┐ │
│  │   Grafana 11     │           │             │     Kafka UI         │ │
│  │  (Dashboards)    │           │             │  (Topic Explorer)    │ │
│  └──────────────────┘           │             └──────────────────────┘ │
│                                 │                                      │
└─────────────────────────────────┼──────────────────────────────────────┘
                                  │
                         Published to Kafka
                      (datacenter.predictions)
```

---

## 2. Data Flow — End to End

```
 STEP 1: COLLECT          STEP 2: PUBLISH         STEP 3: PERSIST          STEP 4: PREDICT
 (Every 5 seconds)        (Kafka Producer)        (Telemetry Hub)          (AI Predictor)
 ─────────────────        ────────────────        ───────────────          ───────────────

 ┌─────────────────┐      ┌──────────────┐     ┌──────────────────┐
 │  InsightAgent   │      │              │     │  TELEMETRY HUB   │
 │                 │      │  Apache      │     │                  │
 │ ┌─────────────┐ │  ──► │  Kafka       │ ──► │ 1. Parse msg     │
 │ │ EdgeStats   │ │      │              │     │ 2. Write InfluxDB│──┐
 │ │ (gopsutil)  │ │      │  Topics:     │     │ 3. Alert rules   │  │
 │ └─────────────┘ │      │   .metrics   │     │ 4. Alerts→Kafka  │  │
 │ ┌─────────────┐ │      │   .flows     │     └──────────────────┘  │
 │ │ eBPFScope   │ │      │   .alerts    │                           │
 │ │ (simulator) │ │      │   .predict.  │     ┌──────────────────┐  │
 │ └─────────────┘ │      │              │     │    InfluxDB      │◄─┘
 │ ┌─────────────┐ │      └──────┬───────┘     │  (Time-Series)   │
 │ │ Simulator   │ │             │             │                  │
 │ │ (SONiC data)│ │             │             │ 30-min history   │
 │ └─────────────┘ │             │             │ per agent        │
 └─────────────────┘             │             └────────┬─────────┘
                                 │                      │
                          Kafka triggers           InfluxDB provides
                          "new data arrived"       historical window
                                 │                      │
                                 ▼                      ▼
                          ┌──────────────────────────────────────┐
                          │          AI PREDICTOR                │
                          │                                      │
                          │  On Kafka msg (trigger):             │
                          │   1. Debounce (15s per agent)        │
                          │   2. Query InfluxDB (last 30 min)    │
                          │   3. Train Isolation Forest on       │
                          │      historical window               │
                          │   4. Detect anomaly on latest point  │
                          │   5. Fit regression on time-series   │
                          │   6. Root cause correlation          │
                          │   7. Bayesian fault diagnosis         │
                          │   8. Publish predictions → Kafka     │
                          └──────────────────────────────────────┘
                                           │
                          ┌────────────────┴────────────────────┐
                          ▼                                     ▼
                   ┌──────────────┐                 ┌──────────────────┐
                   │  API GATEWAY │                 │   NOC Dashboard  │
                   │              │                 │                  │
                   │ Consumes:    │                 │  WebSocket feed  │
                   │  .metrics    │                 │  Real-time UI    │
                   │  .alerts     │                 └──────────────────┘
                   │  .predictions│
                   │              │
                   │ Serves:      │
                   │  REST + WS   │
                   └──────────────┘
```

**Key architectural decision:**  Telemetry Hub is the critical persistence
layer.  AI Predictor does NOT process individual Kafka messages — it uses
Kafka only as a trigger, then reads the last 30 minutes of historical data
from InfluxDB to run real time-series ML analysis.

---

## 3. Telemetry Hub — Detail Flow

```
    datacenter.metrics              datacenter.flows
          │                               │
          ▼                               ▼
 ┌─────────────────────────────────────────────────────┐
 │              TELEMETRY HUB (Port 8001)              │
 │                                                     │
 │  ┌──────────────────────────────────┐               │
 │  │     Kafka Consumer (aiokafka)    │               │
 │  │  group: telemetry-hub            │               │
 │  │  subscribes: .metrics + .flows   │               │
 │  └──────────┬───────────┬───────────┘               │
 │             │           │                           │
 │             ▼           ▼                           │
 │  ┌─────────────────┐  ┌──────────────┐             │
 │  │  InfluxDB Writer │  │  Alert Hub   │             │
 │  │                 │  │              │             │
 │  │ Writes points:  │  │ 6 Rules:     │             │
 │  │  • cpu          │  │  • cpu > 85% │             │
 │  │  • memory       │  │  • mem > 80% │             │
 │  │  • disk         │  │  • disk > 85%│             │
 │  │  • network      │  │  • net_err   │             │
 │  │  • bgp          │  │  • temp >75C │──► Kafka    │
 │  │  • temperature  │  │  • bgp_down  │  (alerts)   │
 │  │  • fan          │  │              │             │
 │  │  • psu          │  └──────────────┘             │
 │  │  • flow         │                               │
 │  └────────┬────────┘                               │
 │           │                                        │
 └───────────┼────────────────────────────────────────┘
             ▼
      ┌──────────────┐         ┌──────────────┐
      │  InfluxDB    │────────►│   Grafana    │
      │              │  Flux   │              │
      │ Bucket:      │ queries │ Dashboard:   │
      │  telemetry   │         │ SONiC NOC    │
      │              │         │              │
      │ Measurements:│         │ Panels:      │
      │  cpu         │         │  CPU/Memory  │
      │  memory      │         │  BGP Status  │
      │  disk        │         │  Temperature │
      │  network     │         │  Fan/PSU     │
      │  bgp         │         │  Interfaces  │
      │  temperature │         │  eBPF Flows  │
      │  fan         │         │              │
      │  psu         │         │              │
      │  flow        │         │              │
      └──────────────┘         └──────────────┘
```

---

## 4. AI Predictor — ML Pipeline Detail

**Key Design:** AI Predictor uses Kafka as a trigger and InfluxDB as its
data source.  This ensures the ML models work on real time-series history,
not just individual data points.

```
  Kafka: datacenter.metrics                      InfluxDB (30 min history)
          │ (trigger only)                              │ (data source)
          ▼                                             │
 ┌──────────────────────────────────────────────────────┼──────────────┐
 │                  AI PREDICTOR (Port 8002)             │              │
 │                                                      │              │
 │  ┌─────────────────────────────────────────────────┐ │              │
 │  │            Process Engine (asyncio)              │ │              │
 │  │                                                  │ │              │
 │  │  Kafka Consumer ──► Debounce (15s/agent)         │ │              │
 │  │                          │                       │ │              │
 │  │                    agent_id                      │ │              │
 │  │                          │                       │ │              │
 │  │                          ▼                       │ │              │
 │  │                    InfluxReader ◄────────────────┘ │              │
 │  │                    (Flux queries)                  │              │
 │  │                          │                         │              │
 │  │                   list[{timestamp,                 │              │
 │  │                    cpu_usage, mem_usage,            │              │
 │  │                    disk_usage, net_rx, ...}]       │              │
 │  │                          │                         │              │
 │  │                          ▼                         │              │
 │  │            ┌──────────────────────────────┐        │              │
 │  │            │   InsightAI Orchestrator     │        │              │
 │  │            │                              │        │              │
 │  │            │  process_with_history()      │        │              │
 │  │            │                              │        │              │
 │  │            │  8 features per timestamp:   │        │              │
 │  │            │   cpu_usage, mem_usage,       │        │              │
 │  │            │   disk_usage, net_rx, net_tx, │        │              │
 │  │            │   net_errors, temperature_max,│        │              │
 │  │            │   bgp_down_count              │        │              │
 │  │            └───────────┬──────────────────┘        │              │
 │  │                        │                           │              │
 │  │         ┌──────────────┼──────────────┬────────────┤              │
 │  │         ▼              ▼              ▼            ▼              │
 │  │  ┌───────────┐  ┌──────────┐  ┌──────────┐ ┌──────────┐         │
 │  │  │  ANOMALY  │  │  FAULT   │  │   ROOT   │ │ BAYESIAN │         │
 │  │  │ DETECTION │  │  PRED.   │  │  CAUSE   │ │DIAGNOSIS │         │
 │  │  │           │  │          │  │ ANALYSIS │ │          │         │
 │  │  │Isolation  │  │ OLS      │  │ Pearson  │ │ DAG +    │         │
 │  │  │Forest     │  │ Regress. │  │ Correl.  │ │ CPTs     │         │
 │  │  │           │  │          │  │          │ │          │         │
 │  │  │Train on   │  │ Fit on   │  │ Correl.  │ │ Learn    │         │
 │  │  │full       │  │ real     │  │ over     │ │ CPTs     │         │
 │  │  │InfluxDB   │  │ time     │  │ history  │ │ from     │         │
 │  │  │window     │  │ series   │  │ window   │ │ history  │         │
 │  │  │           │  │          │  │          │ │          │         │
 │  │  │Predict:   │  │Extrap.   │  │ Rank     │ │Posterior │         │
 │  │  │latest pt  │  │ 30min    │  │ metrics  │ │fault     │         │
 │  │  │           │  │          │  │          │ │probab.   │         │
 │  │  └─────┬─────┘  └────┬─────┘  └────┬────┘ └────┬────┘         │
 │  │        └──────────────┼─────────────┼──────────┘               │
 │  │                       ▼             ▼                           │
 │  │                  Kafka Producer                                 │
 │  └────────────────────┬────────────────────────────────────────────┘
 │                       ▼                                              │
 │              datacenter.predictions ──► Kafka                        │
 │                                                                      │
 │  REST API (manual inference + status):                               │
 │   POST /predict/anomaly    — single anomaly check                    │
 │   POST /predict/fault      — single fault prediction                 │
 │   POST /predict/root-cause — RCA for an agent                        │
 │   POST /predict/bayesian   — Bayesian fault diagnosis                │
 │   POST /predict            — full pipeline on one sample             │
 │   GET  /status             — model training state + InfluxDB stats   │
 │   GET  /models             — model descriptions + pipeline config    │
 └──────────────────────────────────────────────────────────────────────┘

  Why InfluxDB instead of in-memory buffers?
  ┌──────────────────────────────────────────────────────────────────────┐
  │ Problem with in-memory buffers:                                     │
  │  • Lost on restart — no training data after reboot                  │
  │  • Cold start — needs 100+ points before model works               │
  │  • Single-point — can't see trends from before the service started  │
  │                                                                     │
  │ With InfluxDB:                                                      │
  │  ✓ Instant startup — 30 min of history available immediately        │
  │  ✓ Survives restarts — historical data persists across deploys      │
  │  ✓ Real time-series — regression on actual timestamps, not indices  │
  │  ✓ Telemetry Hub validated — data is already parsed and written     │
  └──────────────────────────────────────────────────────────────────────┘
```

---

## 5. API Gateway — Aggregation Layer

```
 Kafka Topics (3 consumers)            InfluxDB (Flux queries)
          │                                     │
    ┌─────┴──────┐                              │
    ▼     ▼      ▼                              │
 .alerts .preds .metrics                        │
    │     │      │                              │
    ▼     ▼      ▼                              ▼
 ┌──────────────────────────────────────────────────────────┐
 │               API GATEWAY (Port 8000)                    │
 │                                                          │
 │  ┌───────────────────────────────────────────────────┐   │
 │  │            In-Memory Buffers                      │   │
 │  │                                                   │   │
 │  │  alerts_buffer   (deque, max 1000)                │   │
 │  │  predictions_buf (deque, max 1000)                │   │
 │  │  latest_metrics  (dict per agent_id)              │   │
 │  └───────────────────────┬───────────────────────────┘   │
 │                          │                               │
 │  ┌───────────────────────┼───────────────────────────┐   │
 │  │            REST API                               │   │
 │  │                                                   │   │
 │  │  GET /api/v1/health         → service status      │   │
 │  │  GET /api/v1/metrics        → InfluxDB Flux query │   │
 │  │  GET /api/v1/metrics/{id}   → latest from cache   │   │
 │  │  GET /api/v1/alerts         → from buffer         │   │
 │  │  GET /api/v1/predictions    → from buffer         │   │
 │  │  GET /api/v1/demo/scenarios → list fault types    │   │
 │  │  POST /api/v1/demo/trigger  → inject fault        │   │
 │  └───────────────────────────────────────────────────┘   │
 │                                                          │
 │  ┌───────────────────────────────────────────────────┐   │
 │  │            WebSocket (/ws/stream)                 │   │
 │  │                                                   │   │
 │  │  Broadcasts EVERY Kafka message to all clients:   │   │
 │  │   { "type": "metric",     "data": {...} }        │   │
 │  │   { "type": "alert",      "data": {...} }        │   │
 │  │   { "type": "prediction", "data": {...} }        │   │
 │  └───────────────────────────────────────────────────┘   │
 │                          │                               │
 └──────────────────────────┼───────────────────────────────┘
                            │
              ┌─────────────┼──────────────┐
              ▼             ▼              ▼
        NOC Dashboard    Grafana      3rd Party
        (WebSocket)      (REST)       (REST/WS)
```

---

## 6. SONiC Edge Device — InsightAgent Detail

```
 ┌───────────────────────────────────────────────────────────────┐
 │              SONiC Edge Device Container (Go 1.23)            │
 │              e.g. sonic-leaf-01                                │
 │                                                               │
 │  Config (from env vars):                                      │
 │   NODE_ID=sonic-leaf-01  NODE_ROLE=leaf  PLATFORM=sonic       │
 │   KAFKA_BROKER=kafka:9092  SIMULATE=true  INTERVAL=5s        │
 │                                                               │
 │  ┌─────────────────────────────────────────────────────────┐  │
 │  │              Collector Layer                             │  │
 │  │                                                         │  │
 │  │  ┌──────────────┐  ┌──────────────┐                     │  │
 │  │  │  EdgeStats   │  │  Simulator   │  ← (--simulate)     │  │
 │  │  │  (gopsutil)  │  │  (SONiC)     │                     │  │
 │  │  │              │  │              │                     │  │
 │  │  │ Real system: │  │ Generates:   │                     │  │
 │  │  │  CPU, Memory │  │  48 Ethernet │                     │  │
 │  │  │  Disk, Net   │  │  4 PortChan  │                     │  │
 │  │  │              │  │  BGP peers   │                     │  │
 │  │  │              │  │  4 temp sens │                     │  │
 │  │  │              │  │  4 fans      │                     │  │
 │  │  │              │  │  2 PSUs      │                     │  │
 │  │  └──────┬───────┘  └──────┬───────┘                     │  │
 │  │         │    OR           │                              │  │
 │  │         └────────┬────────┘                              │  │
 │  │                  │                                       │  │
 │  │                  ▼                                       │  │
 │  │         NodeMetrics struct                               │  │
 │  │         ┌───────────────────────────────────┐            │  │
 │  │         │ agent_id: "sonic-leaf-01"         │            │  │
 │  │         │ node: {hostname, ip, role, platf} │            │  │
 │  │         │ cpu:  {usage_percent, cores, load}│            │  │
 │  │         │ memory: {total, used, avail, pct} │            │  │
 │  │         │ disk: [{device, usage_pct, ...}]  │            │  │
 │  │         │ network: [{iface, rx, tx, err}]   │            │  │
 │  │         │ bgp: [{neighbor, AS, state, ...}] │            │  │
 │  │         │ hardware: {temps, fans, psus}     │            │  │
 │  │         └────────────────┬──────────────────┘            │  │
 │  │                          │                               │  │
 │  └──────────────────────────┼───────────────────────────────┘  │
 │                             │                                  │
 │  ┌──────────────────────────┼───────────────────────────────┐  │
 │  │        Message Relay (Kafka Producer)                    │  │
 │  │                          │                               │  │
 │  │   Serialize JSON ──► kafka.Writer ──► datacenter.metrics │  │
 │  │                                  ──► datacenter.flows    │  │
 │  └──────────────────────────────────────────────────────────┘  │
 │                                                               │
 │  Anomaly injection (simulator):                               │
 │   Every ~120 ticks: CPU/Memory spike                          │
 │   Every ~200 ticks: BGP session flap                          │
 └───────────────────────────────────────────────────────────────┘
```

---

## 7. Kafka Topics & Message Schemas

```
 ┌──────────────────────────────────────────────────────────────┐
 │                      KAFKA TOPICS                            │
 ├──────────────────────┬───────────────────────────────────────┤
 │                      │                                       │
 │  datacenter.metrics  │  Producer: InsightAgent (4 devices)   │
 │  (3 partitions)      │  Consumers: TelemetryHub,             │
 │                      │             AIPredictor, APIGateway   │
 │                      │  Key: agent_id                        │
 │                      │  Rate: 4 msgs / 5s = 48 msgs/min     │
 │                      │                                       │
 ├──────────────────────┼───────────────────────────────────────┤
 │                      │                                       │
 │  datacenter.flows    │  Producer: InsightAgent (4 devices)   │
 │  (3 partitions)      │  Consumers: TelemetryHub              │
 │                      │  Key: agent_id                        │
 │                      │  Rate: ~48 msgs/min                   │
 │                      │                                       │
 ├──────────────────────┼───────────────────────────────────────┤
 │                      │                                       │
 │  datacenter.alerts   │  Producer: TelemetryHub (AlertHub)    │
 │  (3 partitions)      │  Consumers: APIGateway                │
 │                      │  Key: agent_id                        │
 │                      │  Rate: on-demand (threshold breaches) │
 │                      │                                       │
 ├──────────────────────┼───────────────────────────────────────┤
 │                      │                                       │
 │  datacenter.         │  Producer: AIPredictor                │
 │  predictions         │  Consumers: APIGateway                │
 │  (3 partitions)      │  Key: agent_id                        │
 │                      │  Rate: on-demand (anomalies found)    │
 │                      │                                       │
 └──────────────────────┴───────────────────────────────────────┘
```

---

## 8. Alert & Prediction Flow — End to End

```
  SONiC Device: CPU hits 92%
          │
          ▼
  Kafka: datacenter.metrics
          │
          ├─────────────────────────────────────────────┐
          ▼                                             ▼
  ┌──────────────────┐                       ┌──────────────────┐
  │  Telemetry Hub   │                       │   AI Predictor   │
  │  (real-time)     │                       │   (trigger)      │
  │                  │                       │                  │
  │  1. Parse msg    │                       │  Kafka msg =     │
  │  2. Write InfluxDB├─────┐                │  "new data for   │
  │  3. AlertHub:    │     │                │   sonic-leaf-01"  │
  │     cpu=92%      │     │                │                  │
  │     warn >85%    │     │  InfluxDB      │  Query InfluxDB  │
  │     ✓ FIRES!     │     │  (persisted)   │  for last 30 min │
  │                  │     │      │         │◄─────────────────│
  │  Publishes to    │     └──────┤         │                  │
  │  Kafka .alerts:  │            │         │  360 data points │
  │  {severity: warn}│            └────────►│  8 features each │
  └────────┬─────────┘                      │                  │
           │                       Train Isolation Forest on 360 pts
           │                       Predict: latest pt = ANOMALY
           │                                │
           │                       Fit OLS regression on 360 real
           │                       timestamps: CPU trend → 95% in 12m
           │                                │
           │                       Correlate 8 metrics over 360 pts
           │                       Root cause: cpu_usage (r=0.94)
           │                                │
           │                       Bayesian Network: learn CPTs from
           │                       360 pts, P(thermal_throttling |
           │                       cpu=critical, temp=elevated) = 0.55
           │                                │
           │                       Publish to Kafka .predictions
           │                                │
           ▼                                ▼
  ┌──────────────────────────────────────────────────────────┐
  │                    API GATEWAY                           │
  │                                                          │
  │  Kafka consumer receives both simultaneously:            │
  │   • Alert  → alerts_buffer + WebSocket broadcast         │
  │   • Prediction → predictions_buffer + WS broadcast       │
  │                                                          │
  │  WebSocket message:                                      │
  │   { "type": "alert", "data": { severity, metric, ... }} │
  │   { "type": "prediction", "data": { model, result } }   │
  └──────────────────────┬───────────────────────────────────┘
                         │
                         ▼
  ┌──────────────────────────────────────────────────────────┐
  │                NOC DASHBOARD (Browser)                   │
  │                                                          │
  │  WebSocket receives → dispatches by type:                │
  │   "alert"      → Alerts panel turns red                  │
  │   "prediction" → AI Predictions panel shows fault        │
  │   "metric"     → Device card updates in real-time        │
  │                                                          │
  │  Sequence: Telemetry Hub alert arrives first (~1 sec)    │
  │            AI Predictor prediction follows (~3-5 sec)    │
  │            Total: < 5 seconds from CPU spike to insight  │
  └──────────────────────────────────────────────────────────┘
```

---

## 9. Demo Trigger Flow

```
  Customer clicks "CPU Spike" button on NOC Dashboard
          │
          │  POST /api/v1/demo/trigger/cpu_spike
          ▼
  ┌──────────────────┐
  │   API Gateway    │
  │                  │
  │  demo.py:        │
  │  builds 5 fake   │
  │  high-CPU msgs   │──► Kafka: datacenter.metrics
  │  for leaf-01     │         (5 messages with CPU=95%)
  └──────────────────┘
          │
          │  Same Kafka topic as real agents
          ▼
  ┌──────────────────┐    ┌──────────────────────────────┐
  │  Telemetry Hub   │    │   AI Predictor               │
  │                  │    │                              │
  │  Writes to       │    │  1. Kafka trigger received   │
  │  InfluxDB ───────┼───►│  2. Query InfluxDB history   │
  │                  │    │  3. Train on 30min window    │
  │  Fires alert:    │    │  4. Detect anomaly + predict │
  │  cpu_usage=95%   │    │  5. Publish 3 predictions    │
  │  CRITICAL        │    │                              │
  └────────┬─────────┘    └────────┬───────────────────┘
           │                       │
           ▼                       ▼
  ┌──────────────────────────────────────────┐
  │           API Gateway (WebSocket)        │
  └────────────────┬─────────────────────────┘
                   │
                   ▼
  ┌──────────────────────────────────────────┐
  │           NOC Dashboard                  │
  │                                          │
  │  Within 5 seconds:                       │
  │   • leaf-01 card turns red (CPU 95%)     │
  │   • AI pipeline counters increment       │
  │   • Alert appears: "cpu_usage CRITICAL"  │
  │   • Prediction: "thermal_throttling      │
  │     in 12 min, confidence 87%"           │
  │   • Root cause: "cpu_usage (r=0.94)"     │
  └──────────────────────────────────────────┘

  Available scenarios:
  ┌─────────────┬──────────────────────┬──────────────────────────┐
  │ Scenario    │ Target Device        │ What Happens             │
  ├─────────────┼──────────────────────┼──────────────────────────┤
  │ cpu_spike   │ sonic-leaf-01        │ CPU 92-98%, high temps   │
  │ bgp_flap    │ sonic-spine-01       │ 2 BGP peers go down      │
  │ thermal     │ sonic-leaf-02        │ ASIC 92-98°C, fan fail   │
  └─────────────┴──────────────────────┴──────────────────────────┘
```

---

## 10. Network Topology

```
  Docker Networks:
  ┌─────────────────────────────────────────┐
  │  edge-fabric (10.10.0.0/24)             │
  │  Simulates datacenter switch fabric     │
  │                                         │
  │   10.10.0.20  sonic-border-01           │
  │   10.10.0.10  sonic-spine-01            │
  │   10.10.0.11  sonic-leaf-01             │
  │   10.10.0.12  sonic-leaf-02             │
  └─────────────────────────────────────────┘
          │ (all edge devices also on dc-network
          │  to reach Kafka)
          ▼
  ┌─────────────────────────────────────────┐
  │  dc-network (bridge, auto subnet)       │
  │  Cloud platform internal communication  │
  │                                         │
  │   kafka          (9092)                 │
  │   kafka-ui       (8080)                 │
  │   influxdb       (8086)                 │
  │   grafana        (3000)                 │
  │   telemetry-hub  (8001)                 │
  │   ai-predictor   (8002)                 │
  │   api-gateway    (8000)                 │
  │                                         │
  │   + all 4 edge devices (dual-homed)     │
  └─────────────────────────────────────────┘
```

---

## 11. Technology Stack

```
 ┌────────────────┬──────────────────────────────────────────────┐
 │ Layer          │ Technology                                   │
 ├────────────────┼──────────────────────────────────────────────┤
 │ Edge Agent     │ Go 1.23, gopsutil, segmentio/kafka-go       │
 │ Event Bus      │ Apache Kafka 3.9.0 (KRaft, no Zookeeper)    │
 │ Telemetry Hub  │ Python 3.12, FastAPI, aiokafka, influxdb-   │
 │                │ client-python                                │
 │ AI Predictor   │ Python 3.12, FastAPI, scikit-learn, numpy,   │
 │                │ Bayesian Network (custom DAG + CPTs)         │
 │ API Gateway    │ Python 3.12, FastAPI, aiokafka, WebSocket    │
 │ Time-Series DB │ InfluxDB 2.7 (Flux query language)           │
 │ Visualization  │ Grafana 11, custom HTML/JS NOC dashboard     │
 │ Orchestration  │ Docker Compose (11 containers, 2 networks)   │
 │ Monitoring UI  │ Kafka UI (Provectus)                         │
 └────────────────┴──────────────────────────────────────────────┘
```

---

## 12. Service Dependency Graph

```
                    ┌──────────┐
                    │  Kafka   │ ← starts first (healthcheck)
                    └────┬─────┘
                         │
          ┌──────────────┼──────────────────────┐
          │              │                      │
          ▼              ▼                      ▼
  ┌──────────────┐ ┌───────────┐       ┌──────────────┐
  │  InfluxDB    │ │ Kafka UI  │       │ 4x SONiC     │
  │              │ │           │       │ Edge Devices  │
  └──────┬───────┘ └───────────┘       └──────────────┘
         │
         ├─────────────────────┐
         ▼                     ▼
  ┌──────────────┐    ┌──────────────┐
  │   Grafana    │    │Telemetry Hub │ ← needs Kafka + InfluxDB
  │              │    └──────────────┘
  └──────────────┘
         │
         │         ┌──────────────┐
         │         │ AI Predictor │ ← needs Kafka + InfluxDB
         │         └──────────────┘   (reads history from InfluxDB
         │                             written by Telemetry Hub)
         ▼
  ┌────────────────────────┐
  │     API Gateway        │ ← needs Kafka + InfluxDB +
  │                        │   TelemetryHub + AIPredictor
  └────────────────────────┘

  Data dependency chain:
  ┌────────────────────────────────────────────────────────────┐
  │  SONiC Agents ──► Kafka ──► Telemetry Hub ──► InfluxDB    │
  │                     │                            │         │
  │                     └──trigger──► AI Predictor ◄─┘ reads   │
  │                                       │                    │
  │                                       ▼                    │
  │                                  Kafka (predictions)       │
  │                                       │                    │
  │                                       ▼                    │
  │                                  API Gateway → Dashboard   │
  └────────────────────────────────────────────────────────────┘
```

---

## 13. SONiC Platform Data Sources

This section documents how the InsightAgent collects hardware telemetry
from real SONiC switches in **production mode** (`SIMULATE=false`).

### 13.1 Collection Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│                SONiC Switch (e.g. Arista 7060CX)                 │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │                    InsightAgent (Go)                         │  │
│  │                                                             │  │
│  │  EdgeStats                                                  │  │
│  │  ├── gopsutil ───► CPU, Memory, Disk, Network  (any Linux)  │  │
│  │  │                                                          │  │
│  │  └── SONiCPlatform (cfg.Platform == "sonic")                │  │
│  │      │                                                      │  │
│  │      ├── collectTemperatures()                              │  │
│  │      │   ├─ Method 1: show platform temperature --json      │  │
│  │      │   └─ Method 2: /sys/class/thermal/thermal_zone*/temp │  │
│  │      │                                                      │  │
│  │      ├── collectFans()                                      │  │
│  │      │   └─ show platform fan --json                        │  │
│  │      │                                                      │  │
│  │      ├── collectPSUs()                                      │  │
│  │      │   └─ show platform psustatus --json                  │  │
│  │      │                                                      │  │
│  │      └── collectBGPNeighbors()                              │  │
│  │          └─ vtysh -c "show bgp summary json"                │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  SONiC OS Internals:                                              │
│  ┌──────────┐  ┌────────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ STATE_DB │  │ APPL_DB    │  │CONFIG_DB │  │   FRRouting   │  │
│  │ (Redis 6)│  │ (Redis 0)  │  │(Redis 4) │  │   (vtysh)     │  │
│  │          │  │            │  │          │  │               │  │
│  │ TEMP_INFO│  │ PORT_TABLE │  │ BGP_NEIG │  │ BGP Summary   │  │
│  │ FAN_INFO │  │ ROUTE_TABLE│  │ DEVICE_MD│  │ Route Table   │  │
│  │ PSU_INFO │  │            │  │          │  │ Neighbor Info │  │
│  └──────────┘  └────────────┘  └──────────┘  └───────────────┘  │
└───────────────────────────────────────────────────────────────────┘
```

### 13.2 Temperature Collection

**Primary: SONiC CLI (JSON output)**

```
$ show platform temperature --json
[
  {"name": "CPU Core",    "temperature": 45.0, "high_th": 85.0, "crit_high_th": 100.0},
  {"name": "ASIC Memory", "temperature": 58.0, "high_th": 95.0, "crit_high_th": 110.0},
  {"name": "Inlet",       "temperature": 32.0, "high_th": 70.0, "crit_high_th": 80.0},
  {"name": "Outlet",      "temperature": 38.0, "high_th": 75.0, "crit_high_th": 85.0}
]
```

**Alternative: SONiC Redis STATE_DB**

```
$ redis-cli -n 6 KEYS "TEMPERATURE_INFO|*"
1) "TEMPERATURE_INFO|CPU Core"
2) "TEMPERATURE_INFO|ASIC Memory"
3) "TEMPERATURE_INFO|Inlet"
4) "TEMPERATURE_INFO|Outlet"

$ redis-cli -n 6 HGETALL "TEMPERATURE_INFO|CPU Core"
 1) "temperature"
 2) "45.0"
 3) "high_threshold"
 4) "85.0"
 5) "critical_high_threshold"
 6) "100.0"
 7) "low_threshold"
 8) "N/A"
 9) "warning"
10) "false"
11) "timestamp"
12) "20240301 12:00:00"
```

**Fallback: Linux sysfs (any Linux host)**

```
$ cat /sys/class/thermal/thermal_zone0/temp
45000     ← millidegrees Celsius (÷ 1000 = 45.0°C)

$ cat /sys/class/thermal/thermal_zone0/type
x86_pkg_temp

$ ls /sys/class/thermal/thermal_zone*/temp
/sys/class/thermal/thermal_zone0/temp
/sys/class/thermal/thermal_zone1/temp
/sys/class/thermal/thermal_zone2/temp
```

**Priority chain in code:**
```
collectTemperatures()
  │
  ├─ try: "show platform temperature --json" → parseTemperatureJSON()
  │       (works on real SONiC switches)
  │
  └─ fallback: read /sys/class/thermal/thermal_zone*/temp
               (works on any Linux host including Docker containers)
```

### 13.3 Fan Collection

**SONiC CLI:**

```
$ show platform fan --json
[
  {"name": "FAN1", "speed": 8500, "speed_pct": 60.0, "direction": "intake", "status": "OK"},
  {"name": "FAN2", "speed": 8400, "speed_pct": 59.0, "direction": "intake", "status": "OK"},
  {"name": "FAN3", "speed": 8600, "speed_pct": 61.0, "direction": "exhaust", "status": "OK"},
  {"name": "FAN4", "speed": 8500, "speed_pct": 60.0, "direction": "exhaust", "status": "OK"}
]
```

**Redis STATE_DB:**

```
$ redis-cli -n 6 HGETALL "FAN_INFO|FAN1"
 1) "speed"
 2) "8500"
 3) "speed_target"
 4) "60"
 5) "direction"
 6) "intake"
 7) "presence"
 8) "Present"
 9) "status"
10) "OK"
```

### 13.4 PSU (Power Supply) Collection

**SONiC CLI:**

```
$ show platform psustatus --json
[
  {"name": "PSU1", "status": "OK", "power": 250.5, "voltage": 12.1, "current": 20.7},
  {"name": "PSU2", "status": "OK", "power": 248.3, "voltage": 12.0, "current": 20.5}
]
```

**Redis STATE_DB:**

```
$ redis-cli -n 6 HGETALL "PSU_INFO|PSU1"
 1) "status"
 2) "true"
 3) "power"
 4) "250.5"
 5) "voltage"
 6) "12.1"
 7) "current"
 8) "20.7"
 9) "presence"
10) "true"
```

### 13.5 BGP Neighbor Collection

**FRRouting (vtysh) — standard on all SONiC switches:**

```
$ vtysh -c "show bgp summary json"
{
  "ipv4Unicast": {
    "routerId": "10.0.0.1",
    "as": 65001,
    "peers": {
      "10.0.0.2": {
        "remoteAs": 65100,
        "state": "Established",
        "pfxRcd": 350,
        "peerUptimeMsec": 86400000,
        "msgRcvd": 25000,
        "msgSent": 24500,
        "desc": "spine-01"
      },
      "10.0.0.3": {
        "remoteAs": 65100,
        "state": "Established",
        "pfxRcd": 280,
        "peerUptimeMsec": 82800000,
        "msgRcvd": 23000,
        "msgSent": 22800,
        "desc": "spine-02"
      }
    }
  }
}
```

**BGP States:**
```
┌──────────────┬──────────────────────────────────────────┐
│ State        │ Meaning                                  │
├──────────────┼──────────────────────────────────────────┤
│ Idle         │ Initial state, no connection attempt     │
│ Connect      │ TCP connection in progress               │
│ Active       │ Retrying TCP connection                  │
│ OpenSent     │ TCP connected, OPEN message sent         │
│ OpenConfirm  │ OPEN received, waiting for KEEPALIVE     │
│ Established  │ ✓ Session up, exchanging routes          │
└──────────────┴──────────────────────────────────────────┘

Alert triggers when state ≠ "Established"
```

### 13.6 Simulate vs Real Mode

```
┌──────────────────────┬────────────────────────────────────┬──────────────────────────────┐
│ Parameter            │ SIMULATE=true (Docker demo)        │ SIMULATE=false (Production)  │
├──────────────────────┼────────────────────────────────────┼──────────────────────────────┤
│ CPU / Memory         │ Gaussian random (role-based range) │ gopsutil (procfs)            │
│ Disk                 │ Random per-device pattern          │ gopsutil (statfs)            │
│ Network interfaces   │ 48 Ethernet + 4 PortChannel       │ gopsutil (net.IOCounters)    │
│ Temperature sensors  │ 4 simulated: CPU, ASIC, Inlet, Out│ SONiC CLI → sysfs fallback   │
│ Fans                 │ 4 simulated with RPM variation     │ SONiC CLI (show platform fan)│
│ PSUs                 │ 2 simulated with wattage           │ SONiC CLI (show platform psu)│
│ BGP neighbors        │ 2-4 peers per device (role-based)  │ vtysh "show bgp summary json"│
│ Anomaly injection    │ Periodic CPU/BGP/thermal spikes    │ N/A (real anomalies only)    │
│ eBPF flows           │ Random src/dst port/IP flows       │ eBPF stub (placeholder)      │
└──────────────────────┴────────────────────────────────────┴──────────────────────────────┘
```

### 13.7 Production Upgrade Path

For high-performance production deployments, the CLI-based collector
can be replaced with more efficient methods:

```
                    CLI Commands (current)
                         │
                    ┌────┴────┐
                    ▼         ▼
              gNMI/gRPC   SONiC REST API
              (streaming)  (pull-based)
                    │         │
                    └────┬────┘
                         ▼
              Higher performance, lower overhead
              Supports streaming telemetry subscriptions

  gNMI example (OpenConfig paths):
    /openconfig-platform:components/component[name="CPU Core"]/state/temperature/instant
    /openconfig-platform:components/component[name="FAN1"]/state/...
    /openconfig-network-instance:network-instances/.../bgp/neighbors/neighbor/state

  SONiC REST API example:
    GET /restconf/data/openconfig-platform:components
    GET /restconf/data/openconfig-network-instance:network-instances
```

---

## 14. AI Predictor ↔ InfluxDB Communication

This section documents how the AI Predictor service reads historical
data from InfluxDB for ML inference.

### 14.1 Communication Stack

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│   Layer 1: Docker Network                                           │
│   ┌─────────────────────┐          ┌──────────────────────┐        │
│   │ dc-ai-predictor     │          │ dc-influxdb          │        │
│   │                     │  dc-net  │                      │        │
│   │ influx_reader.py    │◄────────►│  InfluxDB 2.7 HTTP   │        │
│   │                     │  bridge  │  API on port 8086    │        │
│   └─────────────────────┘          └──────────────────────┘        │
│   Docker DNS: "influxdb" → resolves to dc-influxdb IP              │
│                                                                     │
│   Layer 2: Authentication                                           │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │  .env: INFLUXDB_TOKEN=dc-monitor-super-secret-token-2024    │  │
│   │                                                              │  │
│   │  InfluxDB uses token as admin credential                     │  │
│   │  AI Predictor sends token in Authorization header            │  │
│   │  Same token → full read/write access to the "metrics" bucket │  │
│   └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│   Layer 3: Protocol                                                 │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │  HTTP POST  http://influxdb:8086/api/v2/query                │  │
│   │  Headers:                                                    │  │
│   │    Authorization: Token dc-monitor-super-secret-token-2024   │  │
│   │    Content-Type: application/vnd.flux                        │  │
│   │  Body: Flux query language (InfluxDB's query syntax)         │  │
│   │  Response: annotated CSV → parsed by Python client           │  │
│   └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│   Layer 4: Python Client Library                                    │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │  influxdb-client[async] == 1.46.0                            │  │
│   │                                                              │  │
│   │  InfluxDBClientAsync(url, token, org)                        │  │
│   │      │                                                       │  │
│   │      └── query_api().query(flux_query)                       │  │
│   │              │                                               │  │
│   │              ├── Sends HTTP POST to /api/v2/query            │  │
│   │              ├── Parses annotated CSV response                │  │
│   │              └── Returns List[FluxTable] with records         │  │
│   │                   each record has: get_time(), get_value()    │  │
│   └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 14.2 Query Flow (Triggered by Kafka)

```
  Kafka: datacenter.metrics
        │
        │ message: { "agent_id": "sonic-leaf-01", "cpu": {...}, ... }
        │
        ▼
  ┌───────────────────────────────────────────────────────────────────┐
  │  ProcessEngine._process_message()                                │
  │                                                                   │
  │  1. Extract agent_id = "sonic-leaf-01"                            │
  │                                                                   │
  │  2. Debounce check: last run 20s ago > 15s cooldown → proceed    │
  │                                                                   │
  │  3. influx_reader.query_agent_history("sonic-leaf-01", 30)       │
  │     │                                                             │
  │     │  Sends 8 parallel Flux queries to InfluxDB:                │
  │     │                                                             │
  │     │  ┌──────────────────────────────────────────────────────┐   │
  │     │  │ Query 1: CPU                                        │   │
  │     │  │ from(bucket: "metrics")                             │   │
  │     │  │   |> range(start: -30m)                             │   │
  │     │  │   |> filter(fn: (r) => r.agent_id == "sonic-leaf-01")   │
  │     │  │   |> filter(fn: (r) => r._measurement == "cpu")    │   │
  │     │  │   |> filter(fn: (r) => r._field == "usage_percent")│   │
  │     │  │   |> aggregateWindow(every: 5s, fn: last)          │   │
  │     │  │                                                     │   │
  │     │  │ → Returns: {epoch: value, epoch: value, ...}        │   │
  │     │  │   e.g. {1709294400: 45.2, 1709294405: 46.1, ...}   │   │
  │     │  └──────────────────────────────────────────────────────┘   │
  │     │                                                             │
  │     │  Query 2: Memory (same pattern, measurement="memory")      │
  │     │  Query 3: Disk (aggregated mean across devices)            │
  │     │  Query 4: Network RX (aggregated sum across interfaces)    │
  │     │  Query 5: Network TX (aggregated sum)                      │
  │     │  Query 6: Network Errors (rx_errors + tx_errors sum)       │
  │     │  Query 7: Temperature (max across sensors)                 │
  │     │  Query 8: BGP down count (sum of non-Established)          │
  │     │                                                             │
  │     ▼                                                             │
  │  4. _merge_series() → combines 8 queries by timestamp            │
  │     [                                                             │
  │       {timestamp: 1709294400, cpu_usage: 45.2, mem_usage: 62.1,  │
  │        disk_usage: 55.0, net_rx: 50000, net_tx: 30000,          │
  │        net_errors: 0, temperature_max: 52.0, bgp_down_count: 0},│
  │       {timestamp: 1709294405, cpu_usage: 46.1, mem_usage: 62.3,  │
  │        ...},                                                      │
  │       ...  (360 rows for 30 minutes at 5s intervals)              │
  │     ]                                                             │
  │                                                                   │
  │  5. insight_ai.process_with_history(agent_id, ts, current, hist) │
  │     → ML runs on the 360-point historical window                  │
  │                                                                   │
  │  6. Publish predictions → Kafka: datacenter.predictions           │
  │                                                                   │
  └───────────────────────────────────────────────────────────────────┘
```

### 14.3 InfluxDB Data Path (Write → Read)

```
  ┌─────────────┐                ┌──────────────────┐
  │ SONiC Agent  │ ──Kafka────► │  Telemetry Hub   │
  │ sonic-leaf-01│                │                  │
  └──────────────┘               │  WRITES to       │
                                 │  InfluxDB:       │
                                 │                  │
                                 │  Point("cpu")    │
                                 │   .tag("agent_id",│
                                 │    "sonic-leaf-01")
                                 │   .field(         │
                                 │    "usage_percent",│
                                 │     45.2)          │
                                 │   .time(ts)       │
                                 └────────┬──────────┘
                                          │
                                   HTTP POST /api/v2/write
                                   Line Protocol:
                                   cpu,agent_id=sonic-leaf-01
                                     usage_percent=45.2 1709294400000
                                          │
                                          ▼
                                 ┌──────────────────┐
                                 │    InfluxDB      │
                                 │                  │
                                 │  Bucket: metrics │
                                 │                  │
                                 │  Stores as       │
                                 │  time-series:    │
                                 │  ┌────────┬─────┐│
                                 │  │ time   │value││
                                 │  ├────────┼─────┤│
                                 │  │12:00:00│45.2 ││
                                 │  │12:00:05│46.1 ││
                                 │  │12:00:10│47.8 ││
                                 │  │12:00:15│45.9 ││
                                 │  │  ...   │ ... ││
                                 │  └────────┴─────┘│
                                 └────────┬──────────┘
                                          │
                                   HTTP POST /api/v2/query
                                   Flux: from(bucket: "metrics")
                                     |> range(start: -30m)
                                     |> filter(agent_id == ...)
                                          │
                                          ▼
                                 ┌──────────────────┐
                                 │  AI Predictor    │
                                 │                  │
                                 │  READS 30 min    │
                                 │  of history:     │
                                 │                  │
                                 │  360 data points │
                                 │  × 8 features    │
                                 │  = 2,880 values  │
                                 │                  │
                                 │  → ML pipeline   │
                                 └──────────────────┘
```

### 14.4 Timing & Configuration

```
┌─────────────────────────────────────────────────────────────────┐
│ Parameter                │ Value  │ Purpose                     │
├──────────────────────────┼────────┼─────────────────────────────┤
│ Agent publish interval   │ 5s     │ New Kafka msg every 5s      │
│ PREDICT_COOLDOWN_SECONDS │ 15s    │ Don't re-query InfluxDB     │
│                          │        │ for same agent within 15s   │
│ INFLUX_LOOKBACK_MINUTES  │ 30m    │ How far back to query       │
│ INFLUX_MIN_HISTORY_POINTS│ 10     │ Min data pts before ML runs │
│ aggregateWindow(every)   │ 5s     │ Flux query resolution       │
│                          │        │                             │
│ Result: 30 min ÷ 5s     │ = 360  │ Data points per query       │
│ × 8 features             │ = 2880 │ Total values fed to ML      │
│ Per 4 agents × every 15s │ ~16 q/m│ InfluxDB query rate         │
└─────────────────────────────────────────────────────────────────┘
```

---

## 15. Bayesian Network — Probabilistic Fault Diagnosis

The 4th ML model in the AI Predictor pipeline.  While the other models
detect anomalies (Isolation Forest), extrapolate trends (OLS regression),
and rank metric correlations (Pearson), the Bayesian Network provides
**probabilistic causal diagnosis** — computing the posterior probability
of each fault type given the current observed metric states.

### 15.1 Why Bayesian Networks?

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │                   What each model answers:                           │
 │                                                                      │
 │  Isolation Forest:    "Is the current state abnormal?"               │
 │  OLS Regression:      "Will a threshold be breached soon?"           │
 │  Pearson Correlation: "Which metrics are most correlated?"           │
 │  Bayesian Network:    "What fault is most likely, and with           │
 │                        what probability, given these metrics?"       │
 │                                                                      │
 │  The Bayesian Network adds:                                          │
 │   ✓ Causal reasoning (directed dependencies, not just correlation)  │
 │   ✓ Probabilistic output (0.0–1.0 per fault, not binary)           │
 │   ✓ Multi-fault assessment (all 6 faults scored simultaneously)     │
 │   ✓ Expert knowledge encoding (DAG structure from domain experts)   │
 │   ✓ Data-driven learning (CPTs updated from InfluxDB history)       │
 └──────────────────────────────────────────────────────────────────────┘
```

### 15.2 Network Structure (DAG)

```
 ┌───────────────────────────────────────────────────────────────────────┐
 │                    METRIC NODES (always observed)                     │
 │           Discretised: low / normal / elevated / critical            │
 │                                                                      │
 │   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
 │   │cpu_usage │  │mem_usage │  │disk_usage│  │net_errors│           │
 │   │          │  │          │  │          │  │          │           │
 │   │ <30: low │  │ <30: low │  │ <40: low │  │  <1: low │           │
 │   │ <70: norm│  │ <70: norm│  │ <75: norm│  │ <10: norm│           │
 │   │ <90: elev│  │ <90: elev│  │ <90: elev│  │ <30: elev│           │
 │   │ ≥90: crit│  │ ≥90: crit│  │ ≥90: crit│  │ ≥30: crit│           │
 │   └────┬──┬──┘  └────┬──┬──┘  └────┬─────┘  └────┬──┬──┘           │
 │        │  │          │  │          │              │  │              │
 │   ┌────┘  │     ┌────┘  │          │         ┌───┘  │              │
 │   │       │     │       │          │         │      │              │
 │   │  ┌────────┐ │  ┌────────┐      │    ┌────────┐  │              │
 │   │  │temp_max│ │  │        │      │    │bgp_down│  │              │
 │   │  │        │ │  │        │      │    │ _count │  │              │
 │   │  │ <40:low│ │  │        │      │    │<0.5:low│  │              │
 │   │  │ <65:nm │ │  │        │      │    │<1.5:nm │  │              │
 │   │  │ <80:elv│ │  │        │      │    │<3.5:elv│  │              │
 │   │  │ ≥80:crt│ │  │        │      │    │≥3.5:crt│  │              │
 │   │  └──┬──┬──┘ │  │        │      │    └──┬──┬──┘  │              │
 │   │     │  │    │  │        │      │       │  │     │              │
 │   ▼     ▼  ▼    ▼  ▼        ▼      ▼       ▼  ▼     ▼              │
 │ ┌─────────┐ ┌─────────┐ ┌───────┐ ┌──────────┐ ┌─────────┐       │
 │ │THERMAL  │ │MEMORY   │ │ DISK  │ │NETWORK   │ │  BGP    │       │
 │ │THROTTLE │ │EXHAUST. │ │ FULL  │ │DEGRADE   │ │FAILURE  │       │
 │ │         │ │         │ │       │ │          │ │         │       │
 │ │parents: │ │parents: │ │parent:│ │parents:  │ │parents: │       │
 │ │ cpu,    │ │ mem,    │ │ disk  │ │ net_err, │ │ bgp,    │       │
 │ │ temp    │ │ cpu     │ │       │ │ bgp      │ │ net_err │       │
 │ └─────────┘ └─────────┘ └───────┘ └──────────┘ └─────────┘       │
 │                                                                      │
 │         ┌───────────────────────────────────┐                       │
 │         │  + SWITCH_OVERHEAT (parent: temp) │                       │
 │         └───────────────────────────────────┘                       │
 └──────────────────────────────────────────────────────────────────────┘
```

### 15.3 Conditional Probability Tables (CPTs)

```
 Example: P(thermal_throttling = present | cpu_state, temperature_state)

 ┌──────────┬─────────────────────────────────────────────┐
 │          │            temperature_state                 │
 │ cpu_state│   low      normal    elevated   critical    │
 ├──────────┼─────────────────────────────────────────────┤
 │ low      │   0.01     0.02      0.08       0.15       │
 │ normal   │   0.02     0.05      0.15       0.30       │
 │ elevated │   0.05     0.12      0.35       0.60       │
 │ critical │   0.10     0.25      0.55       0.90       │
 └──────────┴─────────────────────────────────────────────┘

 Reading: When CPU is critical and temperature is elevated,
 there is a 55% probability of thermal throttling.

 CPT learning modes:
 ┌───────────────────┬─────────────────────────────────────┐
 │ History mode      │ MLE + Laplace smoothing (α = 1.0)   │
 │ (InfluxDB data)   │ CPT[a,b] = (hits + 1) / (count + 2)│
 │                   │ Adapts to actual data patterns       │
 ├───────────────────┼─────────────────────────────────────┤
 │ Point mode        │ Expert-defined CPT values            │
 │ (no data)         │ Based on domain knowledge            │
 │                   │ Reasonable priors for cold start      │
 └───────────────────┴─────────────────────────────────────┘
```

### 15.4 Inference Pipeline

```
 Current metrics from Kafka trigger:
 { cpu_usage: 92.5, mem_usage: 45.0, temperature_max: 78.0, ... }
           │
           ▼
 ┌───────────────────────────────────────────────────────────────┐
 │  Step 1: DISCRETISE                                           │
 │                                                               │
 │  cpu_usage = 92.5     → critical (≥90)                       │
 │  mem_usage = 45.0     → normal   (30–70)                     │
 │  disk_usage = 55.0    → normal   (40–75)                     │
 │  net_errors = 2.0     → normal   (1–10)                      │
 │  temperature_max=78.0 → elevated (65–80)                     │
 │  bgp_down_count = 0   → low      (<0.5)                     │
 └────────────────────────────┬──────────────────────────────────┘
                              │
                              ▼
 ┌───────────────────────────────────────────────────────────────┐
 │  Step 2: CPT LOOKUP (for each fault node)                     │
 │                                                               │
 │  Since ALL parent metrics are observed, no marginalization    │
 │  is needed — just look up the probability in the CPT:         │
 │                                                               │
 │  thermal_throttling:                                          │
 │    CPT[cpu=critical, temp=elevated] = 0.55                   │
 │                                                               │
 │  memory_exhaustion:                                           │
 │    CPT[mem=normal, cpu=critical] = 0.15                      │
 │                                                               │
 │  disk_full:                                                   │
 │    CPT[disk=normal] = 0.10                                   │
 │                                                               │
 │  network_degradation:                                         │
 │    CPT[net_err=normal, bgp=low] = 0.05                       │
 │                                                               │
 │  switch_overheat:                                             │
 │    CPT[temp=elevated] = 0.45                                 │
 │                                                               │
 │  bgp_failure:                                                 │
 │    CPT[bgp=low, net_err=normal] = 0.02                       │
 └────────────────────────────┬──────────────────────────────────┘
                              │
                              ▼
 ┌───────────────────────────────────────────────────────────────┐
 │  Step 3: RANK & REPORT                                        │
 │                                                               │
 │  Sort by probability descending:                              │
 │    1. thermal_throttling  = 0.55  ← MOST LIKELY              │
 │    2. switch_overheat     = 0.45                              │
 │    3. memory_exhaustion   = 0.15                              │
 │    4. disk_full           = 0.10                              │
 │    5. network_degradation = 0.05                              │
 │    6. bgp_failure         = 0.02                              │
 │                                                               │
 │  Alert threshold: 0.30 (configurable)                         │
 │  → Report thermal_throttling (0.55 > 0.30) ✓                 │
 │                                                               │
 │  Recommendation: "Check cooling system, reduce CPU-intensive  │
 │                   processes, inspect fan status"               │
 └───────────────────────────────────────────────────────────────┘
```

### 15.5 Output Schema

```json
{
  "prediction_id": "uuid",
  "timestamp": "2024-03-01T12:00:00Z",
  "agent_id": "sonic-leaf-01",
  "model": "bayesian_diagnosis",
  "result": {
    "fault_probabilities": {
      "thermal_throttling": 0.55,
      "switch_overheat": 0.45,
      "memory_exhaustion": 0.15,
      "disk_full": 0.10,
      "network_degradation": 0.05,
      "bgp_failure": 0.02
    },
    "most_likely_fault": "thermal_throttling",
    "most_likely_probability": 0.55,
    "recommendation": "Check cooling system, reduce CPU load, inspect fan status",
    "description": "CPU thermal throttling due to sustained high temperature or load",
    "metric_states": {
      "cpu_usage": "critical",
      "mem_usage": "normal",
      "disk_usage": "normal",
      "net_errors": "normal",
      "temperature_max": "elevated",
      "bgp_down_count": "low"
    },
    "history_points": 360
  }
}
```

### 15.6 Implementation Details

```
 File: services/ai-predictor/app/models/bayesian_diagnosis.py

 Dependencies: numpy only (no pgmpy, no pandas)
 Reason: All metric nodes are always observed during inference,
         so fault probabilities reduce to simple CPT lookups.
         No Variable Elimination or complex inference needed.

 Class: BayesianDiagnostics
 ┌──────────────────────────────────────────────────────────────┐
 │                                                              │
 │  diagnose_with_history(current_flat, history)                │
 │    → History mode: learn CPTs from data (MLE + Laplace)     │
 │    → Discretise current observation                          │
 │    → CPT lookup for each fault → ranked probabilities        │
 │                                                              │
 │  diagnose(current_flat)                                      │
 │    → Point mode: use expert-defined CPTs                     │
 │    → Discretise + lookup                                     │
 │                                                              │
 │  _learn_cpts(history)                                        │
 │    → Discretise all 360 data points                          │
 │    → Derive fault labels from metric thresholds              │
 │    → Compute CPTs: (hits + α) / (counts + 2α)               │
 │    → α = 1.0 (Laplace smoothing, avoids zero probabilities) │
 │                                                              │
 │  _build_expert_cpts()                                        │
 │    → Hard-coded CPT values from domain expertise             │
 │    → Used when insufficient InfluxDB history                 │
 │                                                              │
 │  _infer(evidence, history_points)                            │
 │    → For each fault: lookup CPT[parent_states]               │
 │    → Sort by probability, apply alert threshold (0.3)        │
 │    → Return probabilities + recommendation                   │
 │                                                              │
 └──────────────────────────────────────────────────────────────┘

 Pipeline position (in insight_ai.py):
 ┌──────────────────────────────────────────────────────────────┐
 │  1. Anomaly Detection  (Isolation Forest)  — always         │
 │  2. Root Cause Analysis (Pearson)          — if anomaly     │
 │  3. Fault Prediction    (OLS Regression)   — always         │
 │  4. Bayesian Diagnosis  (BN + CPTs)        — always         │
 │       └─ publishes only if P(fault) > 0.3                   │
 └──────────────────────────────────────────────────────────────┘
```

---

## 16. Multi-VM Deployment Architecture

The platform supports two deployment modes: single-machine (docker-compose)
for development/demo, and multi-VM for realistic end-to-end testing with
real system metrics, real BGP routing, and physically separated edge/cloud.

### 16.1 Deployment Topology

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                 VirtualBox Host-Only Network (192.168.56.0/24)           │
 │                                                                          │
 │  EDGE LAYER (VM1–VM3)                                                   │
 │  ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐        │
 │  │ VM1 — Spine       │ │ VM2 — Leaf-01    │ │ VM3 — Leaf-02    │        │
 │  │ 192.168.56.101    │ │ 192.168.56.102   │ │ 192.168.56.103   │        │
 │  │ AS 65100          │ │ AS 65001         │ │ AS 65002         │        │
 │  │                   │ │                  │ │                  │        │
 │  │ ┌───────────────┐ │ │ ┌──────────────┐ │ │ ┌──────────────┐ │        │
 │  │ │ FRR (bgpd)    │ │ │ │ FRR (bgpd)   │ │ │ │ FRR (bgpd)   │ │        │
 │  │ │ vtysh → BGP   │ │ │ │ vtysh → BGP  │ │ │ │ vtysh → BGP  │ │        │
 │  │ └───────────────┘ │ │ └──────────────┘ │ │ └──────────────┘ │        │
 │  │ ┌───────────────┐ │ │ ┌──────────────┐ │ │ ┌──────────────┐ │        │
 │  │ │ InsightAgent   │ │ │ │ InsightAgent  │ │ │ │ InsightAgent  │ │        │
 │  │ │ PLATFORM=frr   │ │ │ │ PLATFORM=frr │ │ │ │ PLATFORM=frr │ │        │
 │  │ │ SIMULATE=false │ │ │ │ SIMULATE=false│ │ │ │ SIMULATE=false│ │        │
 │  │ └───────┬───────┘ │ │ └──────┬───────┘ │ │ └──────┬───────┘ │        │
 │  └─────────┼─────────┘ └────────┼─────────┘ └────────┼─────────┘        │
 │            │                    │                     │                   │
 │            │   Kafka Producer → EXTERNAL listener :29092                  │
 │            └──────────┬─────────┴─────────────────────┘                   │
 │                       │                                                   │
 │                       ▼                                                   │
 │  CLOUD LAYER (VM4)                                                       │
 │  ┌──────────────────────────────────────────────────────────────────┐    │
 │  │ VM4 — Cloud  192.168.56.104                                      │    │
 │  │                                                                   │    │
 │  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │    │
 │  │  │  Kafka    │  │ InfluxDB │  │ Grafana  │  │ Telemetry Hub    │ │    │
 │  │  │:9092 int  │  │ :8086    │  │ :3000    │  │ :8001            │ │    │
 │  │  │:29092 ext │  │          │  │          │  │ Kafka→InfluxDB   │ │    │
 │  │  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘ │    │
 │  │  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐   │    │
 │  │  │  AI Predictor     │  │  API Gateway      │  │  Kafka UI    │   │    │
 │  │  │  :8002            │  │  :8000            │  │  :8080       │   │    │
 │  │  │  4 ML models      │  │  REST + WebSocket │  │              │   │    │
 │  │  └──────────────────┘  └──────────────────┘  └──────────────┘   │    │
 │  └──────────────────────────────────────────────────────────────────┘    │
 └──────────────────────────────────────────────────────────────────────────┘
```

### 16.2 Kafka Dual-Listener Architecture

```
 ┌──────────────────────────────────────────────────────────────┐
 │                    Kafka Broker (VM4)                         │
 │                                                              │
 │  PLAINTEXT listener (:9092)                                 │
 │  ├── Advertised as: kafka:9092  (Docker internal DNS)       │
 │  ├── Used by: Telemetry Hub, AI Predictor, API Gateway      │
 │  └── Resolves via Docker bridge network (dc-network)        │
 │                                                              │
 │  EXTERNAL listener (:29092)                                 │
 │  ├── Advertised as: 192.168.56.104:29092  (real IP)        │
 │  ├── Used by: Edge VM InsightAgents (VM1-VM3)              │
 │  └── Reachable over VirtualBox Host-Only network           │
 │                                                              │
 │  CONTROLLER listener (:9093)                                │
 │  └── KRaft internal (single-node controller)                │
 └──────────────────────────────────────────────────────────────┘

 Why two listeners?
 ─────────────────
 Cloud services run inside Docker and resolve "kafka" via Docker DNS.
 Edge VMs are on different machines and must use the real IP + port.
 Kafka requires separate listeners because the advertised address
 must match what each client can actually resolve and reach.
```

### 16.3 Platform Modes

```
 ┌───────────────┬─────────────────────────┬──────────────────────────────┐
 │ PLATFORM env  │ Collector Stack          │ Data Sources                 │
 ├───────────────┼─────────────────────────┼──────────────────────────────┤
 │ "sonic"       │ SONiCPlatform           │ SONiC CLI (show platform...) │
 │               │   └→ temperatures       │ + sysfs thermal zones        │
 │               │   └→ fans, PSUs         │ + vtysh (BGP)                │
 │               │   └→ BGP neighbors      │                              │
 ├───────────────┼─────────────────────────┼──────────────────────────────┤
 │ "frr"         │ FRRPlatform             │ sysfs thermal zones          │
 │               │   └→ temperatures       │ + vtysh (BGP)                │
 │               │   └→ BGP neighbors      │ (no SONiC CLI, no fans/PSUs)│
 ├───────────────┼─────────────────────────┼──────────────────────────────┤
 │ "linux" / ""  │ (no platform collector) │ gopsutil only                │
 │               │   └→ CPU, mem, disk, net│ (no hardware, no BGP)       │
 └───────────────┴─────────────────────────┴──────────────────────────────┘

 Common across all modes:
 ┌──────────────────────────────────────────────────────────────────────┐
 │ EdgeStats (always active)                                            │
 │  ├── collectCPU()     — gopsutil: cpu.Percent + load.Avg            │
 │  ├── collectMemory()  — gopsutil: mem.VirtualMemory + SwapMemory    │
 │  ├── collectDisk()    — gopsutil: disk.Partitions + Usage + IO      │
 │  └── collectNetwork() — gopsutil: net.IOCounters (all interfaces)   │
 └──────────────────────────────────────────────────────────────────────┘
```

### 16.4 Deployment Files

```
 ┌────────────────────────────────────────────────────────────────────┐
 │ Deployment Mode         │ Files                                    │
 ├─────────────────────────┼──────────────────────────────────────────┤
 │ Single-machine dev/demo │ docker-compose.yml + .env                │
 │  (all 11 services)      │  → make up-build                         │
 ├─────────────────────────┼──────────────────────────────────────────┤
 │ Cloud VM (VM4)          │ docker-compose.cloud.yml + .env.cloud    │
 │  (7 services)           │  → make cloud-up                         │
 ├─────────────────────────┼──────────────────────────────────────────┤
 │ Edge VM Docker           │ docker-compose.edge.yml + .env.edge      │
 │  (1 service)            │  → make edge-up                          │
 ├─────────────────────────┼──────────────────────────────────────────┤
 │ Edge VM native           │ bin/insight-agent + agent.env             │
 │  (systemd)              │  → systemctl start insight-agent          │
 ├─────────────────────────┼──────────────────────────────────────────┤
 │ FRR configuration       │ infra/frr/{vm1-spine,vm2-leaf,vm3-leaf}/ │
 │                         │  → sudo cp frr.conf /etc/frr/frr.conf    │
 └────────────────────────────────────────────────────────────────────┘
```

### 16.5 Data Flow (Multi-VM)

```
 VM1 (Spine)                VM4 (Cloud)
 ┌────────────────┐         ┌─────────────────────────────────────────┐
 │ InsightAgent    │         │                                         │
 │ ┌────────────┐ │  :29092  │ ┌───────┐  :9092  ┌───────────────┐   │
 │ │ EdgeStats  │ │─────────►│ │ Kafka │────────►│ Telemetry Hub │   │
 │ │ FRRPlatform│ │  produce  │ │       │ consume │  → InfluxDB   │   │
 │ └────────────┘ │         │ └───────┘         └───────────────┘   │
 └────────────────┘         │     │                     │            │
                            │     │ trigger         query│           │
 VM2 (Leaf-01)              │     ▼                     ▼            │
 ┌────────────────┐         │ ┌─────────────────────────────────┐    │
 │ InsightAgent   │─────────►│ │        AI Predictor             │    │
 └────────────────┘  :29092  │ │  InfluxDB → history → ML models │    │
                            │ │  → publish predictions to Kafka  │    │
 VM3 (Leaf-02)              │ └─────────────────────────────────┘    │
 ┌────────────────┐         │                                         │
 │ InsightAgent   │─────────►│ ┌──────────┐  ┌──────────┐           │
 └────────────────┘  :29092  │ │ Grafana   │  │ API GW   │           │
                            │ │ dashboards│  │ REST API │           │
                            │ └──────────┘  └──────────┘           │
                            └─────────────────────────────────────────┘
```
