# ============================================================
# AI Datacenter Platform - API Test & Demo Script
# Run: .\demo\test-api.ps1
# ============================================================

$API = "http://localhost:8000"
$TELEMETRY = "http://localhost:8001"
$AI = "http://localhost:8002"

function Write-Section($title) {
    Write-Host ""
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host "  $title" -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor Cyan
}

function Test-Endpoint($name, $url) {
    try {
        $response = Invoke-RestMethod -Uri $url -Method Get -TimeoutSec 5
        Write-Host "  [PASS] $name" -ForegroundColor Green
        return $response
    } catch {
        Write-Host "  [FAIL] $name - $($_.Exception.Message)" -ForegroundColor Red
        return $null
    }
}

# ============================================================
# 1. Health Checks
# ============================================================
Write-Section "1. SERVICE HEALTH CHECKS"

Test-Endpoint "API Gateway" "$API/api/v1/health"
Test-Endpoint "Telemetry Hub" "$TELEMETRY/health"
Test-Endpoint "Telemetry Hub Ready" "$TELEMETRY/health/ready"
Test-Endpoint "AI Predictor" "$AI/health"

# ============================================================
# 2. Kafka UI
# ============================================================
Write-Section "2. KAFKA TOPICS"
Write-Host "  Open http://localhost:8080 in browser to see Kafka UI" -ForegroundColor Yellow
Write-Host "  Topics: datacenter.metrics, datacenter.flows, datacenter.alerts, datacenter.predictions" -ForegroundColor DarkGray

# ============================================================
# 3. Metrics from API Gateway
# ============================================================
Write-Section "3. METRICS (via API Gateway -> InfluxDB)"

$metrics = Test-Endpoint "GET /api/v1/metrics" "$API/api/v1/metrics?last=5m&limit=10"
if ($metrics) {
    Write-Host "  Records returned: $($metrics.count)" -ForegroundColor DarkGray
}

$agentMetrics = Test-Endpoint "GET /api/v1/metrics/server-rack-01" "$API/api/v1/metrics/server-rack-01"
if ($agentMetrics) {
    $src = $agentMetrics.source
    Write-Host "  Source: $src" -ForegroundColor DarkGray
    if ($agentMetrics.data -and $agentMetrics.data.cpu) {
        $cpu = $agentMetrics.data.cpu.usage_percent
        Write-Host "  CPU Usage: $cpu%" -ForegroundColor DarkGray
    }
}

# ============================================================
# 4. Alerts
# ============================================================
Write-Section "4. ALERTS"

$alertRules = Test-Endpoint "GET Alert Rules" "$TELEMETRY/alerts/rules"
if ($alertRules) {
    Write-Host "  Active rules: $($alertRules.Count)" -ForegroundColor DarkGray
    foreach ($rule in $alertRules) {
        Write-Host "    - $($rule.metric): warn=$($rule.warning_threshold) crit=$($rule.critical_threshold)" -ForegroundColor DarkGray
    }
}

$gatewayAlerts = Test-Endpoint "GET /api/v1/alerts" "$API/api/v1/alerts"
if ($gatewayAlerts) {
    Write-Host "  Recent alerts: $($gatewayAlerts.count)" -ForegroundColor DarkGray
}

# ============================================================
# 5. AI Predictions
# ============================================================
Write-Section "5. AI PREDICTIONS"

$models = Test-Endpoint "GET AI Models" "$AI/models"
if ($models -and $models.models) {
    foreach ($m in $models.models) {
        Write-Host "    - $($m.name): $($m.type)" -ForegroundColor DarkGray
    }
}

$status = Test-Endpoint "GET AI Status" "$AI/status"
if ($status) {
    Write-Host "  Processed: $($status.models.processed_count) samples" -ForegroundColor DarkGray
    Write-Host "  Predictions: $($status.models.prediction_count) generated" -ForegroundColor DarkGray
    Write-Host "  Agents tracked: $($status.models.total_agents)" -ForegroundColor DarkGray
}

$gatewayPred = Test-Endpoint "GET /api/v1/predictions" "$API/api/v1/predictions"
if ($gatewayPred) {
    Write-Host "  Recent predictions: $($gatewayPred.count)" -ForegroundColor DarkGray
}

# ============================================================
# 6. Manual AI Inference
# ============================================================
Write-Section "6. MANUAL AI INFERENCE (Anomaly Detection)"

$body = @{
    agent_id = "demo-server"
    cpu = @{ usage_percent = 95.5 }
    memory = @{ usage_percent = 88.2 }
    disk = @{ usage_percent = 72.0 }
    network = @{ rx_bytes = 50000000; tx_bytes = 30000000; rx_errors = 15; tx_errors = 5 }
} | ConvertTo-Json -Depth 4

try {
    $result = Invoke-RestMethod -Uri "$AI/predict" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 10
    Write-Host "  [PASS] Full pipeline prediction" -ForegroundColor Green
    Write-Host "  Predictions returned: $($result.predictions.Count)" -ForegroundColor DarkGray
    foreach ($p in $result.predictions) {
        Write-Host "    Model: $($p.model)" -ForegroundColor Yellow
        if ($p.result.is_anomaly -ne $null) {
            Write-Host "      Anomaly: $($p.result.is_anomaly) | Score: $($p.result.anomaly_score)" -ForegroundColor DarkGray
        }
        if ($p.result.predicted_fault) {
            Write-Host "      Fault: $($p.result.predicted_fault) | Confidence: $($p.result.confidence)" -ForegroundColor DarkGray
        }
        if ($p.result.recommendation) {
            Write-Host "      RCA: $($p.result.recommendation)" -ForegroundColor DarkGray
        }
    }
} catch {
    Write-Host "  [FAIL] Manual inference - $($_.Exception.Message)" -ForegroundColor Red
}

# ============================================================
# 7. Grafana
# ============================================================
Write-Section "7. GRAFANA DASHBOARD"
Write-Host "  URL:      http://localhost:3000" -ForegroundColor Yellow
Write-Host "  Login:    admin / admin" -ForegroundColor DarkGray
Write-Host "  Dashboard: AI Datacenter Monitor (auto-provisioned)" -ForegroundColor DarkGray

# ============================================================
# 8. Live Dashboard
# ============================================================
Write-Section "8. LIVE DEMO DASHBOARD"
Write-Host "  Open demo\dashboard.html in a browser" -ForegroundColor Yellow
Write-Host "  Connects via WebSocket to ws://localhost:8000/ws/stream" -ForegroundColor DarkGray
Write-Host "  Shows real-time: metrics, alerts, AI predictions, network flows" -ForegroundColor DarkGray

# ============================================================
# Summary
# ============================================================
Write-Section "DEMO COMPLETE"
Write-Host ""
Write-Host "  All endpoints tested. Platform is running with:" -ForegroundColor White
Write-Host "    - 2 InsightAgents generating simulated metrics every 5s" -ForegroundColor DarkGray
Write-Host "    - Kafka event bus (4 topics) routing all data" -ForegroundColor DarkGray
Write-Host "    - TelemetryHub storing to InfluxDB + evaluating alert rules" -ForegroundColor DarkGray
Write-Host "    - AIPredictor running Isolation Forest, fault prediction, RCA" -ForegroundColor DarkGray
Write-Host "    - API Gateway serving REST + WebSocket to external clients" -ForegroundColor DarkGray
Write-Host ""
