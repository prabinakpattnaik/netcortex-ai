# Multi-VM Deployment Guide

Deploy the AI Datacenter Monitoring Platform across 4 VirtualBox VMs with
a realistic spine-leaf BGP topology using FRR (Free Range Routing).

---

## Architecture Overview

```
 ┌───────────────────────────────────────────────────────────────────────┐
 │                    VirtualBox Host-Only Network                       │
 │                     192.168.56.0/24                                   │
 │                                                                       │
 │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │
 │  │    VM1        │  │    VM2        │  │    VM3        │               │
 │  │  Spine        │  │  Leaf-01      │  │  Leaf-02      │               │
 │  │  AS 65100     │  │  AS 65001     │  │  AS 65002     │               │
 │  │  .56.101      │  │  .56.102      │  │  .56.103      │               │
 │  │               │  │               │  │               │               │
 │  │ FRR (bgpd)    │  │ FRR (bgpd)    │  │ FRR (bgpd)    │               │
 │  │ InsightAgent  │  │ InsightAgent  │  │ InsightAgent  │               │
 │  │  (Go binary)  │  │  (Go binary)  │  │  (Go binary)  │               │
 │  └───────┬───────┘  └───────┬───────┘  └───────┬───────┘               │
 │          │                  │                  │                       │
 │          │    Kafka Producer (port 29092)       │                       │
 │          └──────────┬───────┴──────────────────┘                       │
 │                     │                                                   │
 │                     ▼                                                   │
 │  ┌──────────────────────────────────────────────────────────────────┐  │
 │  │                     VM4 — Cloud                                   │  │
 │  │                   192.168.56.104                                  │  │
 │  │                                                                   │  │
 │  │  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌───────────┐  │  │
 │  │  │   Kafka     │  │  InfluxDB   │  │  Grafana    │  │ Kafka UI  │  │  │
 │  │  │  :9092 int  │  │  :8086      │  │  :3000      │  │  :8080    │  │  │
 │  │  │  :29092 ext │  │             │  │             │  │           │  │  │
 │  │  └────────────┘  └────────────┘  └────────────┘  └───────────┘  │  │
 │  │  ┌────────────┐  ┌────────────┐  ┌────────────┐                 │  │
 │  │  │ Telemetry  │  │    AI       │  │    API      │                 │  │
 │  │  │    Hub     │  │ Predictor  │  │  Gateway   │                 │  │
 │  │  │  :8001     │  │  :8002     │  │  :8000     │                 │  │
 │  │  └────────────┘  └────────────┘  └────────────┘                 │  │
 │  └──────────────────────────────────────────────────────────────────┘  │
 └───────────────────────────────────────────────────────────────────────┘
```

**BGP Topology:**
```
           VM1 (Spine, AS 65100)
           192.168.56.101
            /           \
    eBGP peer         eBGP peer
          /               \
  VM2 (Leaf, AS 65001)   VM3 (Leaf, AS 65002)
  192.168.56.102         192.168.56.103
```

---

## Prerequisites

- **VirtualBox** 7.x with Host-Only Networking enabled
- **Ubuntu 22.04 LTS** (or newer) on all 4 VMs
- **Docker & Docker Compose** on VM4 (cloud) and optionally on VM1-VM3
- **Git** on all VMs (to clone the repository)
- At least **2 GB RAM per VM** (4 GB recommended for VM4)

---

## Step 1: VirtualBox Network Setup

### 1.1 Create Host-Only Network

In VirtualBox → File → Host Network Manager:

| Setting | Value |
|---------|-------|
| Network Name | vboxnet0 |
| IPv4 Address | 192.168.56.1 |
| IPv4 Mask | 255.255.255.0 |
| DHCP | Disabled |

### 1.2 VM Network Adapters

Each VM needs **two** network adapters:

| Adapter | Type | Purpose |
|---------|------|---------|
| Adapter 1 | NAT | Internet access (apt, docker pull) |
| Adapter 2 | Host-Only (vboxnet0) | Inter-VM communication |

### 1.3 Configure Static IPs

On each VM, configure the Host-Only adapter (`enp0s8`):

```bash
# /etc/netplan/01-host-only.yaml
network:
  version: 2
  ethernets:
    enp0s8:
      addresses:
        - 192.168.56.10X/24    # 101=VM1, 102=VM2, 103=VM3, 104=VM4
      routes: []
```

Apply: `sudo netplan apply`

Verify connectivity:
```bash
# From any VM, ping all others
ping -c 2 192.168.56.101
ping -c 2 192.168.56.102
ping -c 2 192.168.56.103
ping -c 2 192.168.56.104
```

---

## Step 2: Cloud VM Setup (VM4)

### 2.1 Install Docker

```bash
# Install Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# Log out and back in

# Install Docker Compose plugin
sudo apt install docker-compose-plugin
```

### 2.2 Clone Repository

```bash
git clone <your-repo-url> AI_Datacenter
cd AI_Datacenter
```

### 2.3 Configure Environment

Edit `.env.cloud` and verify `CLOUD_VM_IP`:

```bash
# Verify this matches VM4's Host-Only adapter IP
grep CLOUD_VM_IP .env.cloud
# Should show: CLOUD_VM_IP=192.168.56.104
```

### 2.4 Start Cloud Services

```bash
make cloud-up
```

This starts 7 containers:
- **Kafka** (ports 9092 internal, 29092 external)
- **Kafka UI** (port 8080)
- **InfluxDB** (port 8086)
- **Grafana** (port 3000)
- **Telemetry Hub** (port 8001)
- **AI Predictor** (port 8002)
- **API Gateway** (port 8000)

### 2.5 Verify Cloud Services

```bash
# Check all containers are running
make cloud-status

# Verify Kafka is healthy and listening on external port
docker exec dc-kafka kafka-topics.sh --bootstrap-server localhost:9092 --list

# Check API Gateway health
curl http://localhost:8000/health

# Open in browser from host machine:
# Grafana:  http://192.168.56.104:3000  (admin/admin)
# Kafka UI: http://192.168.56.104:8080
```

### 2.6 Firewall Rules

If ufw is enabled, allow edge VM connections:

```bash
sudo ufw allow from 192.168.56.0/24 to any port 29092  # Kafka external
sudo ufw allow from 192.168.56.0/24 to any port 8086   # InfluxDB (optional)
```

---

## Step 3: Edge VM Setup (VM1, VM2, VM3)

Repeat these steps on **each** edge VM.

### 3.1 Install FRR

```bash
# Add FRR repository
curl -s https://deb.frrouting.org/frr/keys.gpg | sudo tee /usr/share/keyrings/frrouting.gpg > /dev/null
FRRVER="frr-stable"
echo "deb [signed-by=/usr/share/keyrings/frrouting.gpg] https://deb.frrouting.org/frr $(lsb_release -s -c) $FRRVER" | \
  sudo tee /etc/apt/sources.list.d/frr.list

sudo apt update
sudo apt install -y frr frr-pythontools
```

### 3.2 Configure FRR Daemons

```bash
# Enable BGP daemon
sudo cp infra/frr/daemons /etc/frr/daemons
```

### 3.3 Configure FRR (per-VM)

Copy the appropriate FRR configuration:

```bash
# VM1 (Spine):
sudo cp infra/frr/vm1-spine/frr.conf /etc/frr/frr.conf

# VM2 (Leaf-01):
sudo cp infra/frr/vm2-leaf/frr.conf /etc/frr/frr.conf

# VM3 (Leaf-02):
sudo cp infra/frr/vm3-leaf/frr.conf /etc/frr/frr.conf
```

Set permissions and restart:
```bash
sudo chown frr:frr /etc/frr/frr.conf /etc/frr/daemons
sudo chmod 640 /etc/frr/frr.conf
sudo systemctl restart frr
sudo systemctl enable frr
```

### 3.4 Verify BGP Sessions

Wait ~30 seconds for BGP to establish, then:

```bash
# On VM1 (Spine) — should show 2 Established peers
sudo vtysh -c "show bgp summary"

# Expected output:
# Neighbor         V  AS   MsgRcvd  MsgSent  Up/Down    State/PfxRcd
# 192.168.56.102   4  65001  ...      ...      00:00:xx   1
# 192.168.56.103   4  65002  ...      ...      00:00:xx   1

# On VM2 or VM3 (Leaf) — should show 1 Established peer
sudo vtysh -c "show bgp summary"
```

### 3.5 Start InsightAgent

**Option A: Docker (recommended)**

```bash
# Copy and configure edge environment
cp .env.edge.template .env.edge

# Edit .env.edge for this VM:
# VM1: NODE_ID=frr-spine-01, NODE_IP=192.168.56.101, NODE_ROLE=spine
# VM2: NODE_ID=frr-leaf-01,  NODE_IP=192.168.56.102, NODE_ROLE=leaf
# VM3: NODE_ID=frr-leaf-02,  NODE_IP=192.168.56.103, NODE_ROLE=leaf
nano .env.edge

# Start the agent
make edge-up
```

**Option B: Native binary (systemd)**

```bash
# Build on any machine with Go 1.23+
make agent-build

# Copy binary to edge VM
sudo cp bin/insight-agent /usr/local/bin/

# Configure
sudo mkdir -p /etc/insight-agent
sudo cp .env.edge.template /etc/insight-agent/agent.env
sudo nano /etc/insight-agent/agent.env   # adjust NODE_ID, NODE_IP, etc.

# Install and start service
sudo cp infra/systemd/insight-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now insight-agent

# Check logs
journalctl -u insight-agent -f
```

---

## Step 4: End-to-End Verification

### 4.1 Check Kafka Topics

From VM4 (cloud):
```bash
# List topics — should see datacenter.metrics with messages
docker exec dc-kafka kafka-topics.sh --bootstrap-server localhost:9092 --list

# Consume recent metrics from edge VMs
docker exec dc-kafka kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic datacenter.metrics \
  --from-beginning --max-messages 5
```

You should see JSON messages with `agent_id` matching your edge VMs
(e.g., `frr-spine-01`, `frr-leaf-01`, `frr-leaf-02`).

### 4.2 Check InfluxDB Data

```bash
# Query recent data points via API Gateway
curl http://192.168.56.104:8000/health
curl http://192.168.56.104:8000/api/status
```

### 4.3 Check Grafana Dashboards

Open http://192.168.56.104:3000 in your browser:
- Login: `admin` / `admin`
- Navigate to the datacenter dashboard
- You should see real metrics from all 3 edge VMs

### 4.4 Check AI Predictions

```bash
# View AI Predictor status
curl http://192.168.56.104:8002/status | python3 -m json.tool

# Check prediction topic for ML output
docker exec dc-kafka kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic datacenter.predictions \
  --from-beginning --max-messages 3
```

### 4.5 Verify BGP Data in Metrics

The InsightAgent on FRR VMs collects BGP neighbor state via `vtysh`.
Check that metrics include BGP data:

```bash
docker exec dc-kafka kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic datacenter.metrics \
  --from-beginning --max-messages 1 | python3 -m json.tool | grep -A 5 bgp
```

---

## Troubleshooting

### Edge VM cannot connect to Kafka

```bash
# Test connectivity from edge VM to cloud Kafka
nc -zv 192.168.56.104 29092

# If fails:
# 1. Check VM4 firewall: sudo ufw status
# 2. Check Kafka is listening: docker exec dc-kafka ss -tlnp | grep 29092
# 3. Check CLOUD_VM_IP in .env.cloud matches VM4's actual IP
# 4. Verify Host-Only adapter is up: ip addr show enp0s8
```

### BGP sessions not establishing

```bash
# Check FRR is running
sudo systemctl status frr

# Check BGP daemon is enabled
grep bgpd /etc/frr/daemons   # should show bgpd=yes

# Check FRR logs
sudo journalctl -u frr -n 50

# Verify configuration
sudo vtysh -c "show running-config"

# Test connectivity between VMs
ping -c 2 192.168.56.101   # from leaf to spine
```

### vtysh permission denied

```bash
# InsightAgent needs access to /var/run/frr/
ls -la /var/run/frr/

# Docker mode: volume mount should handle it
# Native mode: add user to frr group
sudo usermod -aG frr $USER

# Or adjust socket permissions
sudo chmod 770 /var/run/frr
```

### InsightAgent cannot read thermal zones

```bash
# Check sysfs thermal zones exist
ls /sys/class/thermal/thermal_zone*/temp

# If no thermal zones (common in VMs):
# InsightAgent gracefully handles this — temperatures will be 0
# Hardware stats will be nil (no fans/PSUs in VMs either)
```

### Kafka UI shows no messages

```bash
# 1. Check edge agents are running
make edge-logs   # or: journalctl -u insight-agent -f

# 2. Check agent can resolve Kafka broker
# Docker mode: KAFKA_BROKER should be 192.168.56.104:29092
# NOT kafka:9092 (that's Docker internal DNS on the cloud VM)

# 3. Check topic names match
# Edge agent uses: datacenter.metrics, datacenter.flows
# Cloud consumers expect the same topic names
```

### Memory issues on VM4

If VM4 has limited RAM, reduce Kafka memory:

```bash
# Add to docker-compose.cloud.yml under kafka service:
# environment:
#   KAFKA_HEAP_OPTS: "-Xmx256m -Xms256m"
```

---

## Quick Reference

| VM | Role | IP | Services | Make Target |
|----|------|----|----------|-------------|
| VM1 | Spine (AS 65100) | 192.168.56.101 | FRR + InsightAgent | `make edge-up` |
| VM2 | Leaf (AS 65001) | 192.168.56.102 | FRR + InsightAgent | `make edge-up` |
| VM3 | Leaf (AS 65002) | 192.168.56.103 | FRR + InsightAgent | `make edge-up` |
| VM4 | Cloud | 192.168.56.104 | Kafka, InfluxDB, Grafana, etc. | `make cloud-up` |

| Port | Service | Access From |
|------|---------|-------------|
| 29092 | Kafka (external) | Edge VMs → Cloud |
| 9092 | Kafka (internal) | Cloud containers only |
| 8086 | InfluxDB | Cloud containers |
| 3000 | Grafana | Browser |
| 8080 | Kafka UI | Browser |
| 8000 | API Gateway | Browser / curl |
| 8001 | Telemetry Hub | Cloud containers |
| 8002 | AI Predictor | Cloud containers |

---

## Cleanup

```bash
# Stop cloud services (VM4)
make cloud-down

# Stop edge agent (VM1-VM3)
make edge-down
# or: sudo systemctl stop insight-agent

# Remove all data (VM4)
make cloud-clean

# Stop FRR (VM1-VM3)
sudo systemctl stop frr
```
