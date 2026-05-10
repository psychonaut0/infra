package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"time"

	"github.com/jedib0t/go-pretty/v6/table"
	"github.com/psychonaut0/infra/cli/internal/discover"
	"github.com/psychonaut0/infra/cli/internal/dns"
	"github.com/psychonaut0/infra/cli/internal/repo"
	"github.com/psychonaut0/infra/cli/internal/ssh"
	"github.com/spf13/cobra"
)

func newDnsCmd() *cobra.Command {
	c := &cobra.Command{
		Use:   "dns",
		Short: "Manage Pi-hole DNS records and the matching Caddy reverse-proxy entries",
	}
	c.AddCommand(newDnsLsCmd())
	return c
}

// dnsCtx loads everything `infra dns` commands need: repo paths, parsed
// repo state, ssh targets, and an ssh runner.
type dnsCtx struct {
	root          string
	caddyfile     []byte
	caddyBlocks   []dns.Block
	extras        []dns.ExtraEntry
	desired       []dns.Record
	ctMgmtTarget  string
	ctMgmtIP      string
	ctDnsTarget   string
	runner        *ssh.Runner
}

func loadDnsCtx() (*dnsCtx, error) {
	root, err := repo.Locate()
	if err != nil {
		return nil, err
	}
	idx, err := discover.Load(repo.Locate)
	if err != nil {
		return nil, err
	}
	mgmt, ok := idx.Hosts["ct-mgmt"]
	if !ok {
		return nil, fmt.Errorf("ct-mgmt missing from inventory")
	}
	caddyPath := filepath.Join(root, "stacks", "ct-mgmt", "Caddyfile")
	cf, err := os.ReadFile(caddyPath)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", caddyPath, err)
	}
	blocks, err := dns.ParseCaddyfile(cf)
	if err != nil {
		return nil, err
	}
	extraPath := filepath.Join(root, "stacks", "dns-extra.yaml")
	extras, err := dns.ReadExtra(extraPath)
	if err != nil {
		return nil, err
	}
	return &dnsCtx{
		root:         root,
		caddyfile:    cf,
		caddyBlocks:  blocks,
		extras:       extras,
		desired:      dns.ComputeDesired(blocks, extras, mgmt.IP),
		ctMgmtTarget: idx.SSHTarget("ct-mgmt"),
		ctMgmtIP:     mgmt.IP,
		ctDnsTarget:  idx.SSHTarget("ct-dns"),
		runner:       ssh.New(),
	}, nil
}

func newDnsLsCmd() *cobra.Command {
	var asJSON bool
	c := &cobra.Command{
		Use:   "ls",
		Short: "List DNS + Caddy entries with drift status",
		RunE: func(_ *cobra.Command, _ []string) error {
			d, err := loadDnsCtx()
			if err != nil {
				return err
			}
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			liveBytes, err := dns.ReadDnsmasq(ctx, d.runner, d.ctDnsTarget)
			if err != nil {
				return err
			}
			live, err := dns.ParseDnsmasq(liveBytes)
			if err != nil {
				return err
			}
			rows := buildLsRows(d, live)
			if asJSON {
				enc := json.NewEncoder(os.Stdout)
				enc.SetIndent("", "  ")
				return enc.Encode(rows)
			}
			t := table.NewWriter()
			t.SetOutputMirror(os.Stdout)
			t.AppendHeader(table.Row{"HOSTNAME", "MODE", "UPSTREAM", "DNS", "STATUS"})
			for _, r := range rows {
				t.AppendRow(table.Row{r.Hostname, r.Mode, r.Upstream, r.DNS, r.Status})
			}
			t.Render()
			for _, r := range rows {
				if r.Status != "ok" && r.Status != "(raw)" {
					return fmt.Errorf("drift detected")
				}
			}
			return nil
		},
	}
	c.Flags().BoolVar(&asJSON, "json", false, "Emit JSON instead of a table")
	return c
}

type lsRow struct {
	Hostname string `json:"hostname"`
	Mode     string `json:"mode"`
	Upstream string `json:"upstream"`
	DNS      string `json:"dns"`
	Status   string `json:"status"`
}

func buildLsRows(d *dnsCtx, live []dns.Record) []lsRow {
	type acc struct {
		http, https bool
		upstream    string
		managed     bool
	}
	caddyByHost := map[string]*acc{}
	for _, b := range d.caddyBlocks {
		a, ok := caddyByHost[b.Hostname]
		if !ok {
			a = &acc{}
			caddyByHost[b.Hostname] = a
		}
		a.http = a.http || b.HasHTTP
		a.https = a.https || b.HasHTTPS
		if b.Upstream != "" {
			a.upstream = b.Upstream
		}
		if b.Managed {
			a.managed = true
		}
	}
	extraByName := map[string]string{}
	for _, e := range d.extras {
		extraByName[e.Name] = e.IP
	}
	liveByHost := map[string]string{}
	for _, r := range live {
		liveByHost[r.Hostname] = r.IP
	}
	wantByHost := map[string]string{}
	for _, r := range d.desired {
		wantByHost[r.Hostname] = r.IP
	}
	hosts := map[string]struct{}{}
	for h := range caddyByHost {
		hosts[h] = struct{}{}
	}
	for h := range extraByName {
		hosts[h] = struct{}{}
	}
	for h := range liveByHost {
		hosts[h] = struct{}{}
	}
	names := make([]string, 0, len(hosts))
	for h := range hosts {
		names = append(names, h)
	}
	sort.Strings(names)
	var rows []lsRow
	for _, h := range names {
		var row lsRow
		row.Hostname = h
		switch {
		case caddyByHost[h] != nil:
			a := caddyByHost[h]
			switch {
			case a.http && a.https:
				row.Mode = "http+https"
			case a.https:
				row.Mode = "https"
			default:
				row.Mode = "http"
			}
			if !a.managed {
				row.Mode += " (raw)"
			}
			row.Upstream = a.upstream
		case extraByName[h] != "":
			row.Mode = "direct"
		default:
			row.Mode = "—"
		}
		liveIP, hasLive := liveByHost[h]
		want, hasWant := wantByHost[h]
		switch {
		case hasLive && hasWant && liveIP == want:
			row.DNS = liveIP
			row.Status = "ok"
		case hasLive && hasWant && liveIP != want:
			row.DNS = liveIP
			row.Status = "⚠ wrong IP (want " + want + ")"
		case hasWant && !hasLive:
			row.DNS = "—"
			row.Status = "⚠ no DNS"
		case hasLive && !hasWant:
			row.DNS = liveIP
			row.Status = "⚠ no source"
		}
		// Override the (raw) suffix to a clean status when DNS is fine.
		if caddyByHost[h] != nil && !caddyByHost[h].managed && row.Status == "ok" {
			row.Status = "(raw)"
		}
		rows = append(rows, row)
	}
	return rows
}
