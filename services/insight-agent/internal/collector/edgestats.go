package collector

import (
	"runtime"
	"time"

	"github.com/ai-datacenter/insight-agent/internal/config"
	"github.com/ai-datacenter/insight-agent/internal/models"
	"github.com/shirou/gopsutil/v3/cpu"
	"github.com/shirou/gopsutil/v3/disk"
	"github.com/shirou/gopsutil/v3/load"
	"github.com/shirou/gopsutil/v3/mem"
	"github.com/shirou/gopsutil/v3/net"
)

// EdgeStats collects real system metrics using gopsutil + platform-specific APIs
type EdgeStats struct {
	cfg      *config.Config
	platform PlatformCollector // hardware/BGP collector (nil on plain Linux)
}

func NewEdgeStats(cfg *config.Config) *EdgeStats {
	e := &EdgeStats{cfg: cfg}

	// Select platform collector based on PLATFORM env var
	switch cfg.Platform {
	case "sonic":
		e.platform = NewSONiCPlatform(cfg)
	case "frr":
		e.platform = NewFRRPlatform(cfg)
	// "linux" or anything else: no platform collector
	}

	return e
}

func (e *EdgeStats) CollectMetrics() (*models.NodeMetrics, error) {
	now := time.Now().UTC()

	cpuStats, err := e.collectCPU()
	if err != nil {
		return nil, err
	}

	memStats, err := e.collectMemory()
	if err != nil {
		return nil, err
	}

	diskStats, err := e.collectDisk()
	if err != nil {
		return nil, err
	}

	netStats, err := e.collectNetwork()
	if err != nil {
		return nil, err
	}

	metrics := &models.NodeMetrics{
		AgentID:   e.cfg.NodeID,
		Timestamp: now,
		Node: models.NodeInfo{
			Hostname: e.cfg.NodeID,
			IP:       e.cfg.NodeIP,
			Role:     e.cfg.NodeRole,
			Platform: e.cfg.Platform,
		},
		CPU:     *cpuStats,
		Memory:  *memStats,
		Disk:    diskStats,
		Network: netStats,
	}

	// Collect platform-specific data (BGP, temperatures, fans, PSUs)
	if e.platform != nil {
		metrics.Hardware = e.platform.CollectHardware()
		metrics.BGP = e.platform.CollectBGP()
	}

	return metrics, nil
}

func (e *EdgeStats) collectCPU() (*models.CPUStats, error) {
	percent, err := cpu.Percent(time.Second, false)
	if err != nil {
		return nil, err
	}

	usage := 0.0
	if len(percent) > 0 {
		usage = percent[0]
	}

	loadAvg := []float64{0, 0, 0}
	if runtime.GOOS == "linux" || runtime.GOOS == "darwin" {
		if avg, err := load.Avg(); err == nil {
			loadAvg = []float64{avg.Load1, avg.Load5, avg.Load15}
		}
	}

	return &models.CPUStats{
		UsagePercent: usage,
		Cores:        runtime.NumCPU(),
		LoadAvg:      loadAvg,
	}, nil
}

func (e *EdgeStats) collectMemory() (*models.MemStats, error) {
	v, err := mem.VirtualMemory()
	if err != nil {
		return nil, err
	}

	s, _ := mem.SwapMemory()
	swapTotal := uint64(0)
	swapUsed := uint64(0)
	if s != nil {
		swapTotal = s.Total
		swapUsed = s.Used
	}

	return &models.MemStats{
		TotalBytes:   v.Total,
		UsedBytes:    v.Used,
		UsagePercent: v.UsedPercent,
		AvailBytes:   v.Available,
		SwapTotal:    swapTotal,
		SwapUsed:     swapUsed,
	}, nil
}

func (e *EdgeStats) collectDisk() ([]models.DiskIO, error) {
	partitions, err := disk.Partitions(false)
	if err != nil {
		return nil, err
	}

	var disks []models.DiskIO
	for _, p := range partitions {
		usage, err := disk.Usage(p.Mountpoint)
		if err != nil {
			continue
		}
		disks = append(disks, models.DiskIO{
			Device:       p.Device,
			UsagePercent: usage.UsedPercent,
			TotalBytes:   usage.Total,
			FreeBytes:    usage.Free,
		})
	}

	// Add I/O stats
	ioCounters, _ := disk.IOCounters()
	for name, io := range ioCounters {
		for i, d := range disks {
			if d.Device == name || d.Device == "/dev/"+name {
				disks[i].ReadBytes = io.ReadBytes
				disks[i].WriteBytes = io.WriteBytes
				break
			}
		}
	}

	if len(disks) == 0 {
		disks = append(disks, models.DiskIO{Device: "unknown", UsagePercent: 0})
	}

	return disks, nil
}

func (e *EdgeStats) collectNetwork() ([]models.NetIO, error) {
	counters, err := net.IOCounters(true)
	if err != nil {
		return nil, err
	}

	var nets []models.NetIO
	for _, c := range counters {
		if c.Name == "lo" || c.Name == "lo0" {
			continue
		}
		nets = append(nets, models.NetIO{
			Interface: c.Name,
			RxBytes:   c.BytesRecv,
			TxBytes:   c.BytesSent,
			RxPackets: c.PacketsRecv,
			TxPackets: c.PacketsSent,
			RxErrors:  c.Errin,
			TxErrors:  c.Errout,
			RxDropped: c.Dropin,
			TxDropped: c.Dropout,
		})
	}

	if len(nets) == 0 {
		nets = append(nets, models.NetIO{Interface: "eth0"})
	}

	return nets, nil
}
