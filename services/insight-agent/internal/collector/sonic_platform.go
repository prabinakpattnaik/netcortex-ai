package collector

import (
	"encoding/json"
	"fmt"
	"log"
	"os/exec"
	"strconv"
	"strings"

	"github.com/ai-datacenter/insight-agent/internal/config"
	"github.com/ai-datacenter/insight-agent/internal/models"
)

// SONiCPlatform collects hardware telemetry from a real SONiC switch
// using CLI commands and the Redis STATE_DB.
//
// SONiC stores all platform data in Redis databases:
//   STATE_DB (db6)  — runtime state (temperatures, fans, PSU, BGP)
//   APPL_DB  (db0)  — application state (interfaces, routes)
//   CONFIG_DB(db4)  — configuration
//
// In production, use gNMI or the SONiC REST API for higher performance.
// This implementation uses CLI commands as a portable fallback.
type SONiCPlatform struct {
	cfg *config.Config
}

func NewSONiCPlatform(cfg *config.Config) *SONiCPlatform {
	return &SONiCPlatform{cfg: cfg}
}

// CollectHardware returns hardware health data from SONiC platform.
// Returns nil (not error) if not running on a SONiC device.
func (s *SONiCPlatform) CollectHardware() *models.HardwareStats {
	hw := &models.HardwareStats{}

	hw.Temperatures = s.collectTemperatures()
	hw.Fans = s.collectFans()
	hw.PSUs = s.collectPSUs()

	// Return nil if we got nothing (not a SONiC platform)
	if len(hw.Temperatures) == 0 && len(hw.Fans) == 0 && len(hw.PSUs) == 0 {
		return nil
	}

	return hw
}

// CollectBGP returns BGP neighbor data from SONiC FRR/vtysh.
func (s *SONiCPlatform) CollectBGP() []models.BGPNeighbor {
	return collectBGPNeighbors()
}

// ---------------------------------------------------------------------------
// Temperature: "show platform temperature" or STATE_DB
// ---------------------------------------------------------------------------
//
// Real SONiC output example:
//   $ show platform temperature
//   NAME             TEMPERATURE    HIGH TH    LOW TH    CRIT HIGH    CRIT LOW    WARNING    TIMESTAMP
//   CPU Core         45.0           85.0       N/A       100.0        N/A         false      20240301 12:00:00
//   ASIC Memory      58.0           95.0       N/A       110.0        N/A         false      20240301 12:00:00
//
// Alternative via Redis:
//   $ redis-cli -n 6 HGETALL "TEMPERATURE_INFO|CPU Core"
//   → temperature: 45.0, high_threshold: 85.0, critical_high_threshold: 100.0

func (s *SONiCPlatform) collectTemperatures() []models.TempSensor {
	// Method 1: Try SONiC CLI (JSON output)
	out, err := runCmd("show", "platform", "temperature", "--json")
	if err == nil && len(out) > 0 {
		return parseTemperatureJSON(out)
	}

	// Method 2: Try reading from sysfs (Linux thermal zones)
	// Available on any Linux: /sys/class/thermal/thermal_zone*/temp
	return collectLinuxThermalZones()
}

func parseTemperatureJSON(data []byte) []models.TempSensor {
	// SONiC "show platform temperature --json" returns array of objects
	var entries []struct {
		Name     string  `json:"name"`
		Temp     float64 `json:"temperature"`
		HighTh   float64 `json:"high_th"`
		CritHigh float64 `json:"crit_high_th"`
	}
	if err := json.Unmarshal(data, &entries); err != nil {
		log.Printf("[WARN] Failed to parse temperature JSON: %v", err)
		return nil
	}

	sensors := make([]models.TempSensor, 0, len(entries))
	for _, e := range entries {
		sensors = append(sensors, models.TempSensor{
			Name:     e.Name,
			Current:  e.Temp,
			High:     e.HighTh,
			CritHigh: e.CritHigh,
		})
	}
	return sensors
}

// collectLinuxThermalZones reads temperature from Linux sysfs thermal zones.
// Works on any Linux host (SONiC, FRR VM, bare-metal).
func collectLinuxThermalZones() []models.TempSensor {
	// Fallback: read Linux sysfs thermal zones
	// /sys/class/thermal/thermal_zone0/temp → millidegrees Celsius
	out, err := runCmd("sh", "-c", "ls /sys/class/thermal/thermal_zone*/temp 2>/dev/null")
	if err != nil || len(out) == 0 {
		return nil
	}

	var sensors []models.TempSensor
	paths := strings.Fields(string(out))
	for i, path := range paths {
		data, err := runCmd("cat", path)
		if err != nil {
			continue
		}
		millideg, err := strconv.ParseFloat(strings.TrimSpace(string(data)), 64)
		if err != nil {
			continue
		}
		sensors = append(sensors, models.TempSensor{
			Name:     fmt.Sprintf("thermal_zone%d", i),
			Current:  millideg / 1000.0, // Convert millidegrees to degrees
			High:     85.0,
			CritHigh: 100.0,
		})
	}
	return sensors
}

// ---------------------------------------------------------------------------
// Fans: "show platform fan" or STATE_DB
// ---------------------------------------------------------------------------
//
// Real SONiC output:
//   $ show platform fan
//   FAN     Speed    Direction  Presence  Status   Timestamp
//   FAN1    8500     intake     Present   OK       20240301 12:00:00
//
// Redis: redis-cli -n 6 HGETALL "FAN_INFO|FAN1"

func (s *SONiCPlatform) collectFans() []models.FanStatus {
	out, err := runCmd("show", "platform", "fan", "--json")
	if err == nil && len(out) > 0 {
		return parseFanJSON(out)
	}
	return nil
}

func parseFanJSON(data []byte) []models.FanStatus {
	var entries []struct {
		Name      string  `json:"name"`
		Speed     int     `json:"speed"`
		SpeedPct  float64 `json:"speed_pct"`
		Direction string  `json:"direction"`
		Status    string  `json:"status"`
	}
	if err := json.Unmarshal(data, &entries); err != nil {
		return nil
	}

	fans := make([]models.FanStatus, 0, len(entries))
	for _, e := range entries {
		fans = append(fans, models.FanStatus{
			Name:      e.Name,
			Speed:     e.Speed,
			SpeedPct:  e.SpeedPct,
			Status:    e.Status,
			Direction: e.Direction,
		})
	}
	return fans
}

// ---------------------------------------------------------------------------
// PSU: "show platform psustatus" or STATE_DB
// ---------------------------------------------------------------------------
//
// Real SONiC output:
//   $ show platform psustatus --json
//   [{"name": "PSU1", "status": "OK", "power": 250.0, ...}]
//
// Redis: redis-cli -n 6 HGETALL "PSU_INFO|PSU1"

func (s *SONiCPlatform) collectPSUs() []models.PSUStatus {
	out, err := runCmd("show", "platform", "psustatus", "--json")
	if err == nil && len(out) > 0 {
		return parsePSUJSON(out)
	}
	return nil
}

func parsePSUJSON(data []byte) []models.PSUStatus {
	var entries []struct {
		Name    string  `json:"name"`
		Status  string  `json:"status"`
		Power   float64 `json:"power"`
		Voltage float64 `json:"voltage"`
		Current float64 `json:"current"`
	}
	if err := json.Unmarshal(data, &entries); err != nil {
		return nil
	}

	psus := make([]models.PSUStatus, 0, len(entries))
	for _, e := range entries {
		psus = append(psus, models.PSUStatus{
			Name:    e.Name,
			Status:  e.Status,
			Power:   e.Power,
			Voltage: e.Voltage,
			Current: e.Current,
		})
	}
	return psus
}

// ---------------------------------------------------------------------------
// BGP: "show bgp summary json" via vtysh (FRRouting)
// ---------------------------------------------------------------------------
//
// Real SONiC uses FRRouting (FRR). The command:
//   $ vtysh -c "show bgp summary json"
//
// Returns JSON like:
//   {
//     "ipv4Unicast": {
//       "peers": {
//         "10.0.0.1": {
//           "remoteAs": 65100,
//           "state": "Established",
//           "pfxRcd": 350,
//           "peerUptimeMsec": 86400000,
//           "msgRcvd": 25000,
//           "msgSent": 24500,
//           "desc": "spine-01"
//         }
//       }
//     }
//   }

// collectBGPNeighbors collects BGP peer data via vtysh (FRRouting).
// Works on both SONiC switches and standalone FRR installations.
func collectBGPNeighbors() []models.BGPNeighbor {
	out, err := runCmd("vtysh", "-c", "show bgp summary json")
	if err != nil {
		return nil
	}

	var bgpSummary struct {
		IPv4Unicast struct {
			Peers map[string]struct {
				RemoteAS    int    `json:"remoteAs"`
				State       string `json:"state"`
				PfxRcd      int    `json:"pfxRcd"`
				UptimeMs    int64  `json:"peerUptimeMsec"`
				MsgRcvd     int    `json:"msgRcvd"`
				MsgSent     int    `json:"msgSent"`
				Description string `json:"desc"`
			} `json:"peers"`
		} `json:"ipv4Unicast"`
	}

	if err := json.Unmarshal(out, &bgpSummary); err != nil {
		log.Printf("[WARN] Failed to parse BGP JSON: %v", err)
		return nil
	}

	neighbors := make([]models.BGPNeighbor, 0, len(bgpSummary.IPv4Unicast.Peers))
	for ip, peer := range bgpSummary.IPv4Unicast.Peers {
		state := peer.State
		if state == "" {
			state = "Idle"
		}

		uptime := fmt.Sprintf("%dh%dm%ds",
			peer.UptimeMs/3600000,
			(peer.UptimeMs%3600000)/60000,
			(peer.UptimeMs%60000)/1000,
		)

		neighbors = append(neighbors, models.BGPNeighbor{
			NeighborIP:  ip,
			RemoteAS:    peer.RemoteAS,
			State:       state,
			PrefixRcvd:  peer.PfxRcd,
			Uptime:      uptime,
			MsgSent:     peer.MsgSent,
			MsgRcvd:     peer.MsgRcvd,
			Description: peer.Description,
		})
	}

	return neighbors
}

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

func runCmd(name string, args ...string) ([]byte, error) {
	cmd := exec.Command(name, args...)
	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("command '%s %s' failed: %w", name, strings.Join(args, " "), err)
	}
	return out, nil
}
