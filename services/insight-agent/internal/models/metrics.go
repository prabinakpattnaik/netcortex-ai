package models

import "time"

// NodeMetrics represents the full metrics payload from an agent
type NodeMetrics struct {
	AgentID   string    `json:"agent_id"`
	Timestamp time.Time `json:"timestamp"`
	Node      NodeInfo  `json:"node"`
	CPU       CPUStats  `json:"cpu"`
	Memory    MemStats  `json:"memory"`
	Disk      []DiskIO  `json:"disk"`
	Network   []NetIO   `json:"network"`
	// SONiC-specific fields (optional, omitted when empty)
	BGP      []BGPNeighbor  `json:"bgp,omitempty"`
	Hardware *HardwareStats `json:"hardware,omitempty"`
}

type NodeInfo struct {
	Hostname string `json:"hostname"`
	IP       string `json:"ip"`
	Role     string `json:"role,omitempty"`  // "leaf", "spine", "border"
	Platform string `json:"platform,omitempty"` // "SONiC", "linux"
}

// BGPNeighbor represents a BGP peering session (SONiC gNMI/CLI)
type BGPNeighbor struct {
	NeighborIP    string `json:"neighbor_ip"`
	RemoteAS      int    `json:"remote_as"`
	State         string `json:"state"` // Established, Active, Idle, Connect, OpenSent
	PrefixRcvd    int    `json:"prefix_rcvd"`
	Uptime        string `json:"uptime"`
	MsgSent       int    `json:"msg_sent"`
	MsgRcvd       int    `json:"msg_rcvd"`
	Description   string `json:"description,omitempty"`
}

// HardwareStats represents switch hardware health (SONiC platform)
type HardwareStats struct {
	Temperatures []TempSensor `json:"temperatures"`
	Fans         []FanStatus  `json:"fans"`
	PSUs         []PSUStatus  `json:"psus"`
}

type TempSensor struct {
	Name    string  `json:"name"`
	Current float64 `json:"current"`
	High    float64 `json:"high"`
	CritHigh float64 `json:"crit_high"`
}

type FanStatus struct {
	Name      string  `json:"name"`
	Speed     int     `json:"speed"`
	SpeedPct  float64 `json:"speed_pct"`
	Status    string  `json:"status"` // "OK", "NOT OK"
	Direction string  `json:"direction"` // "intake", "exhaust"
}

type PSUStatus struct {
	Name   string  `json:"name"`
	Status string  `json:"status"` // "OK", "NOT OK"
	Power  float64 `json:"power_watts"`
	Voltage float64 `json:"voltage"`
	Current float64 `json:"current_amps"`
}

type CPUStats struct {
	UsagePercent float64   `json:"usage_percent"`
	Cores        int       `json:"cores"`
	LoadAvg      []float64 `json:"load_avg"`
}

type MemStats struct {
	TotalBytes   uint64  `json:"total_bytes"`
	UsedBytes    uint64  `json:"used_bytes"`
	UsagePercent float64 `json:"usage_percent"`
	AvailBytes   uint64  `json:"avail_bytes"`
	SwapTotal    uint64  `json:"swap_total"`
	SwapUsed     uint64  `json:"swap_used"`
}

type DiskIO struct {
	Device       string  `json:"device"`
	ReadBytes    uint64  `json:"read_bytes"`
	WriteBytes   uint64  `json:"write_bytes"`
	UsagePercent float64 `json:"usage_percent"`
	TotalBytes   uint64  `json:"total_bytes"`
	FreeBytes    uint64  `json:"free_bytes"`
}

type NetIO struct {
	Interface string `json:"interface"`
	RxBytes   uint64 `json:"rx_bytes"`
	TxBytes   uint64 `json:"tx_bytes"`
	RxPackets uint64 `json:"rx_packets"`
	TxPackets uint64 `json:"tx_packets"`
	RxErrors  uint64 `json:"rx_errors"`
	TxErrors  uint64 `json:"tx_errors"`
	RxDropped uint64 `json:"rx_dropped"`
	TxDropped uint64 `json:"tx_dropped"`
}

// FlowEvent represents an eBPF-captured network flow
type FlowEvent struct {
	AgentID   string    `json:"agent_id"`
	Timestamp time.Time `json:"timestamp"`
	Node      NodeInfo  `json:"node"`
	SrcIP     string    `json:"src_ip"`
	DstIP     string    `json:"dst_ip"`
	SrcPort   int       `json:"src_port"`
	DstPort   int       `json:"dst_port"`
	Protocol  string    `json:"protocol"`
	Direction string    `json:"direction"`
	Bytes     uint64    `json:"bytes"`
	Packets   uint64    `json:"packets"`
	LatencyMs float64   `json:"latency_ms"`
	Process   string    `json:"process"`
	PID       int       `json:"pid"`
}
