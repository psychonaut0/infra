package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/jedib0t/go-pretty/v6/table"
	"github.com/psychonaut0/infra/cli/internal/ssh"
	"github.com/spf13/cobra"
)

// pveNodes is the set of Proxmox hosts we query. Hardcoded for now —
// the infra has exactly two.
var pveNodes = []string{"proxmoxmain", "proxmoxnode"}

type ctInfo struct {
	Node     string `json:"node"`
	VMID     int    `json:"vmid"`
	Name     string `json:"name"`
	Status   string `json:"status"`
	IP       string `json:"ip"`
	CPUPct   string `json:"cpu_pct"`
	MemUsed  string `json:"mem_used"`
	MemTotal string `json:"mem_total"`
	DiskUsed string `json:"disk_used"`
	DiskTot  string `json:"disk_total"`
}

func newCtCmd() *cobra.Command {
	c := &cobra.Command{
		Use:   "ct",
		Short: "Proxmox CT/VM management",
	}
	c.AddCommand(newCtStatusCmd())
	return c
}

func newCtStatusCmd() *cobra.Command {
	var asJSON bool
	c := &cobra.Command{
		Use:   "status",
		Short: "Proxmox CT overview (VMID, state, IP, CPU/RAM/disk)",
		RunE: func(_ *cobra.Command, _ []string) error {
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()

			runner := ssh.New()
			var mu sync.Mutex
			var rows []ctInfo
			var wg sync.WaitGroup

			for _, node := range pveNodes {
				wg.Add(1)
				go func(node string) {
					defer wg.Done()
					info, err := gatherCTs(ctx, runner, node)
					if err != nil {
						fmt.Fprintf(os.Stderr, "warning: %s: %v\n", node, err)
						return
					}
					mu.Lock()
					rows = append(rows, info...)
					mu.Unlock()
				}(node)
			}
			wg.Wait()

			sort.Slice(rows, func(a, b int) bool {
				if rows[a].Node != rows[b].Node {
					return rows[a].Node < rows[b].Node
				}
				return rows[a].VMID < rows[b].VMID
			})

			if asJSON {
				enc := json.NewEncoder(os.Stdout)
				enc.SetIndent("", "  ")
				return enc.Encode(rows)
			}

			t := table.NewWriter()
			t.SetOutputMirror(os.Stdout)
			t.AppendHeader(table.Row{"NODE", "VMID", "NAME", "STATE", "IP", "CPU%", "MEM", "DISK"})
			for _, r := range rows {
				mem := fmt.Sprintf("%s / %s", r.MemUsed, r.MemTotal)
				disk := fmt.Sprintf("%s / %s", r.DiskUsed, r.DiskTot)
				t.AppendRow(table.Row{r.Node, r.VMID, r.Name, r.Status, r.IP, r.CPUPct, mem, disk})
			}
			t.Render()
			return nil
		},
	}
	c.Flags().BoolVar(&asJSON, "json", false, "Emit JSON instead of a table")
	return c
}

// gatherCTs queries a Proxmox host for its CTs via `pct list`, then per-CT
// hits `pvesh get /nodes/<node>/lxc/<vmid>/status/current --output-format json`
// for runtime resource stats.
func gatherCTs(ctx context.Context, runner *ssh.Runner, node string) ([]ctInfo, error) {
	listOut, err := runner.Output(ctx, node, "pct list")
	if err != nil {
		return nil, fmt.Errorf("pct list: %w", err)
	}
	var result []ctInfo
	// Header row; then columns: VMID Status Lock Name
	for i, line := range strings.Split(strings.TrimSpace(string(listOut)), "\n") {
		if i == 0 {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 3 {
			continue
		}
		vmid, err := strconv.Atoi(fields[0])
		if err != nil {
			continue
		}
		result = append(result, ctInfo{
			Node:   node,
			VMID:   vmid,
			Status: fields[1],
			Name:   fields[len(fields)-1], // last field is always name
		})
	}

	// Fan out per-CT detail queries in parallel — sequential SSH is the
	// bottleneck, not the Proxmox API.
	var wg sync.WaitGroup
	for i := range result {
		if result[i].Status != "running" {
			continue
		}
		wg.Add(1)
		go func(info *ctInfo) {
			defer wg.Done()
			fetchResources(ctx, runner, node, info)
			info.IP = extractIP(ctx, runner, node, info.VMID)
		}(&result[i])
	}
	wg.Wait()
	return result, nil
}

// fetchResources calls `pvesh get` for a CT's runtime status and fills in
// CPU / memory / disk on the info struct. Proxmox's JSON response includes
// cpu (0.0-1.0), mem / maxmem (bytes), disk / maxdisk (bytes).
func fetchResources(ctx context.Context, runner *ssh.Runner, node string, info *ctInfo) {
	cmd := fmt.Sprintf("pvesh get /nodes/%s/lxc/%d/status/current --output-format json 2>/dev/null",
		node, info.VMID)
	out, err := runner.Output(ctx, node, cmd)
	if err != nil {
		return
	}
	var s struct {
		CPU     float64 `json:"cpu"`
		Mem     int64   `json:"mem"`
		MaxMem  int64   `json:"maxmem"`
		Disk    int64   `json:"disk"`
		MaxDisk int64   `json:"maxdisk"`
	}
	if err := json.Unmarshal(out, &s); err != nil {
		return
	}
	info.CPUPct = fmt.Sprintf("%.1f%%", s.CPU*100)
	info.MemUsed = humanBytes(s.Mem)
	info.MemTotal = humanBytes(s.MaxMem)
	info.DiskUsed = humanBytes(s.Disk)
	info.DiskTot = humanBytes(s.MaxDisk)
}

func humanBytes(n int64) string {
	const (
		K = 1024
		M = 1024 * 1024
		G = 1024 * 1024 * 1024
	)
	switch {
	case n >= G:
		return fmt.Sprintf("%.1fG", float64(n)/G)
	case n >= M:
		return fmt.Sprintf("%dM", n/M)
	case n >= K:
		return fmt.Sprintf("%dK", n/K)
	default:
		return fmt.Sprintf("%d", n)
	}
}

func extractIP(ctx context.Context, runner *ssh.Runner, node string, vmid int) string {
	// `pct config <vmid>` has lines like "net0: name=eth0,...,ip=192.168.3.13/24,..."
	cmd := fmt.Sprintf("pct config %d 2>/dev/null", vmid)
	out, err := runner.Output(ctx, node, cmd)
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(out), "\n") {
		if !strings.HasPrefix(line, "net") {
			continue
		}
		for _, kv := range strings.Split(line, ",") {
			if k, v, ok := strings.Cut(kv, "="); ok && strings.TrimSpace(k) == "ip" {
				if i := strings.Index(v, "/"); i > 0 {
					return v[:i]
				}
				return v
			}
		}
	}
	return ""
}
