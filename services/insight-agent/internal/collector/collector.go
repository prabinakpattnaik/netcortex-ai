package collector

import "github.com/ai-datacenter/insight-agent/internal/models"

// MetricCollector is the interface for collecting system metrics
type MetricCollector interface {
	CollectMetrics() (*models.NodeMetrics, error)
}

// FlowCollector is the interface for collecting network flow events
type FlowCollector interface {
	CollectFlows() ([]models.FlowEvent, error)
}

// PlatformCollector collects platform-specific hardware telemetry and
// BGP neighbor data.  Implementations:
//   - SONiCPlatform: full SONiC switch (CLI + sysfs + vtysh)
//   - FRRPlatform:   commodity Linux with FRR (sysfs + vtysh only)
type PlatformCollector interface {
	CollectHardware() *models.HardwareStats
	CollectBGP() []models.BGPNeighbor
}
