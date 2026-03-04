package collector

import (
	"fmt"
	"math"
	"math/rand"
	"time"

	"github.com/ai-datacenter/insight-agent/internal/config"
	"github.com/ai-datacenter/insight-agent/internal/models"
)

// Simulator generates realistic synthetic metrics for a SONiC network device
type Simulator struct {
	cfg         *config.Config
	tick        int
	rng         *rand.Rand
	baselineCPU float64
	baselineMem float64
	bgpNeighbors []bgpSim
}

type bgpSim struct {
	ip          string
	remoteAS    int
	description string
	upSince     time.Time
}

func NewSimulator(cfg *config.Config) *Simulator {
	src := rand.NewSource(time.Now().UnixNano())
	rng := rand.New(src)

	s := &Simulator{
		cfg:         cfg,
		rng:         rng,
		baselineCPU: 15 + rng.Float64()*20,
		baselineMem: 30 + rng.Float64()*15,
	}
	s.initBGP()
	return s
}

func (s *Simulator) initBGP() {
	now := time.Now()
	switch s.cfg.NodeRole {
	case "spine":
		s.bgpNeighbors = []bgpSim{
			{ip: "10.0.1.1", remoteAS: 65001, description: "leaf-01", upSince: now.Add(-72 * time.Hour)},
			{ip: "10.0.1.2", remoteAS: 65002, description: "leaf-02", upSince: now.Add(-48 * time.Hour)},
			{ip: "10.0.1.3", remoteAS: 65003, description: "leaf-03", upSince: now.Add(-96 * time.Hour)},
			{ip: "10.0.1.4", remoteAS: 65004, description: "leaf-04", upSince: now.Add(-24 * time.Hour)},
		}
	case "border":
		s.bgpNeighbors = []bgpSim{
			{ip: "10.0.0.1", remoteAS: 65100, description: "spine-01", upSince: now.Add(-120 * time.Hour)},
			{ip: "10.0.0.2", remoteAS: 65100, description: "spine-02", upSince: now.Add(-120 * time.Hour)},
			{ip: "172.16.0.1", remoteAS: 64999, description: "ISP-upstream", upSince: now.Add(-240 * time.Hour)},
		}
	default: // leaf
		s.bgpNeighbors = []bgpSim{
			{ip: "10.0.0.1", remoteAS: 65100, description: "spine-01", upSince: now.Add(-120 * time.Hour)},
			{ip: "10.0.0.2", remoteAS: 65100, description: "spine-02", upSince: now.Add(-120 * time.Hour)},
		}
	}
}

func (s *Simulator) CollectMetrics() (*models.NodeMetrics, error) {
	s.tick++
	now := time.Now().UTC()

	hour := float64(now.Hour())
	diurnal := math.Sin((hour-6)*math.Pi/12) * 10

	cpuUsage := clamp(s.baselineCPU+diurnal+s.noise(4), 2, 95)
	memUsage := clamp(s.baselineMem+diurnal*0.3+s.noise(2), 15, 90)

	// Inject anomaly every ~120 ticks (~10 min at 5s)
	if s.tick%120 == 0 {
		cpuUsage = clamp(cpuUsage+35+s.noise(10), 60, 98)
	}

	totalMem := uint64(16 * 1024 * 1024 * 1024)
	usedMem := uint64(float64(totalMem) * memUsage / 100)

	return &models.NodeMetrics{
		AgentID:   s.cfg.NodeID,
		Timestamp: now,
		Node: models.NodeInfo{
			Hostname: s.cfg.NodeID,
			IP:       s.cfg.NodeIP,
			Role:     s.cfg.NodeRole,
			Platform: s.cfg.Platform,
		},
		CPU: models.CPUStats{
			UsagePercent: round2(cpuUsage),
			Cores:        4,
			LoadAvg:      []float64{round2(cpuUsage / 25), round2(cpuUsage / 30), round2(cpuUsage / 35)},
		},
		Memory: models.MemStats{
			TotalBytes:   totalMem,
			UsedBytes:    usedMem,
			UsagePercent: round2(memUsage),
			AvailBytes:   totalMem - usedMem,
		},
		Disk:     s.simulateDisk(),
		Network:  s.simulateSONiCInterfaces(),
		BGP:      s.simulateBGP(),
		Hardware: s.simulateHardware(),
	}, nil
}

func (s *Simulator) CollectFlows() ([]models.FlowEvent, error) {
	now := time.Now().UTC()
	count := 3 + s.rng.Intn(6)
	flows := make([]models.FlowEvent, count)

	protocols := []string{"TCP", "UDP"}
	directions := []string{"IN", "OUT"}
	processes := []string{"bgpd", "zebra", "swss", "syncd", "teamd", "lldpd"}
	ports := []int{179, 443, 6379, 8080, 9092, 3306, 5432, 22}

	for i := 0; i < count; i++ {
		flows[i] = models.FlowEvent{
			AgentID:   s.cfg.NodeID,
			Timestamp: now,
			Node: models.NodeInfo{
				Hostname: s.cfg.NodeID,
				IP:       s.cfg.NodeIP,
				Role:     s.cfg.NodeRole,
				Platform: s.cfg.Platform,
			},
			SrcIP:     fmt.Sprintf("10.%d.%d.%d", s.rng.Intn(4), s.rng.Intn(10), 1+s.rng.Intn(254)),
			DstIP:     fmt.Sprintf("10.%d.%d.%d", s.rng.Intn(4), s.rng.Intn(10), 1+s.rng.Intn(254)),
			SrcPort:   1024 + s.rng.Intn(64000),
			DstPort:   ports[s.rng.Intn(len(ports))],
			Protocol:  protocols[s.rng.Intn(2)],
			Direction: directions[s.rng.Intn(2)],
			Bytes:     uint64(500 + s.rng.Intn(500000)),
			Packets:   uint64(1 + s.rng.Intn(1000)),
			LatencyMs: round2(0.05 + s.rng.Float64()*10),
			Process:   processes[s.rng.Intn(len(processes))],
			PID:       100 + s.rng.Intn(500),
		}
	}
	return flows, nil
}

func (s *Simulator) simulateSONiCInterfaces() []models.NetIO {
	var nets []models.NetIO

	portCount := 48
	if s.cfg.NodeRole == "spine" {
		portCount = 32
	}

	for i := 0; i < portCount; i++ {
		iface := fmt.Sprintf("Ethernet%d", i*4)
		active := i < portCount-8
		rxBase := uint64(0)
		txBase := uint64(0)
		if active {
			rxBase = uint64(10000000 + s.rng.Intn(100000000))
			txBase = uint64(8000000 + s.rng.Intn(80000000))
		}
		nets = append(nets, models.NetIO{
			Interface: iface,
			RxBytes:   rxBase,
			TxBytes:   txBase,
			RxPackets: rxBase / 800,
			TxPackets: txBase / 800,
			RxErrors:  uint64(s.rng.Intn(3)),
			TxErrors:  uint64(s.rng.Intn(2)),
			RxDropped: uint64(s.rng.Intn(5)),
			TxDropped: uint64(s.rng.Intn(3)),
		})
	}

	for i := 1; i <= 4; i++ {
		nets = append(nets, models.NetIO{
			Interface: fmt.Sprintf("PortChannel%d", i),
			RxBytes:   uint64(50000000 + s.rng.Intn(200000000)),
			TxBytes:   uint64(40000000 + s.rng.Intn(150000000)),
			RxPackets: uint64(50000 + s.rng.Intn(200000)),
			TxPackets: uint64(40000 + s.rng.Intn(150000)),
		})
	}

	return nets
}

func (s *Simulator) simulateBGP() []models.BGPNeighbor {
	neighbors := make([]models.BGPNeighbor, len(s.bgpNeighbors))
	for i, bg := range s.bgpNeighbors {
		state := "Established"
		prefixes := 100 + s.rng.Intn(500)
		uptime := time.Since(bg.upSince).Round(time.Second).String()

		// Occasional BGP flap
		if s.tick%200 == 0 && i == len(s.bgpNeighbors)-1 {
			state = "Active"
			prefixes = 0
			uptime = "0s"
		}

		neighbors[i] = models.BGPNeighbor{
			NeighborIP:  bg.ip,
			RemoteAS:    bg.remoteAS,
			State:       state,
			PrefixRcvd:  prefixes,
			Uptime:      uptime,
			MsgSent:     10000 + s.rng.Intn(50000),
			MsgRcvd:     10000 + s.rng.Intn(50000),
			Description: bg.description,
		}
	}
	return neighbors
}

func (s *Simulator) simulateHardware() *models.HardwareStats {
	return &models.HardwareStats{
		Temperatures: []models.TempSensor{
			{Name: "CPU Core", Current: round2(42 + s.noise(5)), High: 85, CritHigh: 100},
			{Name: "ASIC Memory", Current: round2(55 + s.noise(4)), High: 95, CritHigh: 110},
			{Name: "Memory Module", Current: round2(38 + s.noise(3)), High: 80, CritHigh: 95},
			{Name: "Ambient", Current: round2(28 + s.noise(2)), High: 50, CritHigh: 60},
		},
		Fans: []models.FanStatus{
			{Name: "FAN1", Speed: 8000 + s.rng.Intn(2000), SpeedPct: round2(60 + s.noise(10)), Status: "OK", Direction: "intake"},
			{Name: "FAN2", Speed: 8000 + s.rng.Intn(2000), SpeedPct: round2(60 + s.noise(10)), Status: "OK", Direction: "intake"},
			{Name: "FAN3", Speed: 8000 + s.rng.Intn(2000), SpeedPct: round2(60 + s.noise(10)), Status: "OK", Direction: "exhaust"},
			{Name: "FAN4", Speed: 8000 + s.rng.Intn(2000), SpeedPct: round2(60 + s.noise(10)), Status: "OK", Direction: "exhaust"},
		},
		PSUs: []models.PSUStatus{
			{Name: "PSU1", Status: "OK", Power: round2(250 + s.noise(30)), Voltage: round2(12.0 + s.noise(0.2)), Current: round2(20 + s.noise(2))},
			{Name: "PSU2", Status: "OK", Power: round2(240 + s.noise(25)), Voltage: round2(12.0 + s.noise(0.2)), Current: round2(19 + s.noise(2))},
		},
	}
}

func (s *Simulator) simulateDisk() []models.DiskIO {
	total := uint64(32 * 1024 * 1024 * 1024)
	usage := clamp(25+s.noise(5), 10, 80)
	return []models.DiskIO{{
		Device:       "/dev/sda",
		ReadBytes:    uint64(s.rng.Intn(1000000)),
		WriteBytes:   uint64(s.rng.Intn(500000)),
		UsagePercent: round2(usage),
		TotalBytes:   total,
		FreeBytes:    total - uint64(float64(total)*usage/100),
	}}
}

func (s *Simulator) noise(amplitude float64) float64 {
	return (s.rng.Float64()*2 - 1) * amplitude
}

func clamp(v, min, max float64) float64 {
	if v < min {
		return min
	}
	if v > max {
		return max
	}
	return v
}

func round2(v float64) float64 {
	return math.Round(v*100) / 100
}
