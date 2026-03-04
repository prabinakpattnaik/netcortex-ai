package collector

import (
	"github.com/ai-datacenter/insight-agent/internal/config"
	"github.com/ai-datacenter/insight-agent/internal/models"
)

// EBPFScope wraps the simulator's flow collection.
// In a real deployment on Linux with kernel access, this would
// interface with actual eBPF programs for packet/flow capture.
type EBPFScope struct {
	sim *Simulator
}

func NewEBPFScope(cfg *config.Config) *EBPFScope {
	return &EBPFScope{
		sim: NewSimulator(cfg),
	}
}

func (e *EBPFScope) CollectFlows() ([]models.FlowEvent, error) {
	return e.sim.CollectFlows()
}
