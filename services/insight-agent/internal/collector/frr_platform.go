package collector

import (
	"github.com/ai-datacenter/insight-agent/internal/config"
	"github.com/ai-datacenter/insight-agent/internal/models"
)

// FRRPlatform collects telemetry from a commodity Linux host running
// FRR (Free Range Routing).
//
// Compared to SONiCPlatform, this collector:
//   - Collects BGP via vtysh (identical — SONiC itself uses FRR)
//   - Collects temperatures via Linux sysfs thermal zones
//   - Does NOT collect fans or PSUs (not available on commodity Linux)
//
// Use this when PLATFORM=frr in the agent configuration.
type FRRPlatform struct {
	cfg *config.Config
}

// NewFRRPlatform creates a collector for a standalone FRR installation.
func NewFRRPlatform(cfg *config.Config) *FRRPlatform {
	return &FRRPlatform{cfg: cfg}
}

// CollectHardware returns thermal zone data from Linux sysfs.
// Fans and PSUs are not available on commodity Linux and are omitted.
func (f *FRRPlatform) CollectHardware() *models.HardwareStats {
	temps := collectLinuxThermalZones()
	if len(temps) == 0 {
		return nil
	}
	return &models.HardwareStats{
		Temperatures: temps,
	}
}

// CollectBGP returns BGP neighbor data from FRR via vtysh.
// The vtysh command is identical to SONiC because SONiC uses FRR internally.
func (f *FRRPlatform) CollectBGP() []models.BGPNeighbor {
	return collectBGPNeighbors()
}
