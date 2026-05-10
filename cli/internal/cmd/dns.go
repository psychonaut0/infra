package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/jedib0t/go-pretty/v6/table"
	"github.com/psychonaut0/infra/cli/internal/discover"
	"github.com/psychonaut0/infra/cli/internal/dns"
	"github.com/psychonaut0/infra/cli/internal/repo"
	"github.com/psychonaut0/infra/cli/internal/ssh"
	"github.com/psychonaut0/infra/cli/internal/ui"
	"github.com/spf13/cobra"
)

var uiConfirmReal = ui.Confirm

func newDnsCmd() *cobra.Command {
	c := &cobra.Command{
		Use:   "dns",
		Short: "Manage Pi-hole DNS records and the matching Caddy reverse-proxy entries",
	}
	c.AddCommand(newDnsLsCmd())
	c.AddCommand(newDnsAddCmd())
	c.AddCommand(newDnsRmCmd())
	c.AddCommand(newDnsSyncCmd())
	c.AddCommand(newDnsReloadCmd())
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

func newDnsAddCmd() *cobra.Command {
	var asHTTP, asHTTPS, asBoth, noCaddy bool
	var directIP string
	c := &cobra.Command{
		Use:   "add <hostname> [<upstream-url>]",
		Short: "Add a DNS record (and matching Caddy block, unless --no-caddy)",
		Args:  cobra.RangeArgs(1, 2),
		RunE: func(_ *cobra.Command, args []string) error {
			hostname := args[0]
			if !strings.HasSuffix(hostname, ".lan") {
				return fmt.Errorf("hostname must end in .lan")
			}
			d, err := loadDnsCtx()
			if err != nil {
				return err
			}
			// Reject duplicates.
			for _, b := range d.caddyBlocks {
				if b.Hostname == hostname {
					return fmt.Errorf("%s already exists in Caddyfile (use `infra dns rm` first)", hostname)
				}
			}
			for _, e := range d.extras {
				if e.Name == hostname {
					return fmt.Errorf("%s already exists in dns-extra.yaml", hostname)
				}
			}
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
			defer cancel()
			if noCaddy {
				if directIP == "" {
					return fmt.Errorf("--no-caddy requires --ip")
				}
				newExtras := append(d.extras, dns.ExtraEntry{Name: hostname, IP: directIP})
				if err := dns.WriteExtra(filepath.Join(d.root, "stacks", "dns-extra.yaml"), newExtras); err != nil {
					return err
				}
				return reloadDnsmasq(ctx, d, append(d.desired, dns.Record{Hostname: hostname, IP: directIP}))
			}
			if len(args) != 2 {
				return fmt.Errorf("upstream URL required (or pass --no-caddy --ip <ip>)")
			}
			upstream := args[1]
			if err := validateUpstream(upstream); err != nil {
				return fmt.Errorf("invalid upstream %q: %w", upstream, err)
			}
			scheme := "http"
			switch {
			case asBoth:
				scheme = "both"
			case asHTTPS:
				scheme = "https"
			case asHTTP:
				scheme = "http"
			}
			newCaddy := d.caddyfile
			if scheme == "http" || scheme == "both" {
				newCaddy = dns.AppendBlock(newCaddy, hostname, "http", upstream)
			}
			if scheme == "https" || scheme == "both" {
				newCaddy = dns.AppendBlock(newCaddy, hostname, "https", upstream)
			}
			caddyPath := filepath.Join(d.root, "stacks", "ct-mgmt", "Caddyfile")
			if err := os.WriteFile(caddyPath, newCaddy, 0o644); err != nil {
				return err
			}
			if err := dns.WriteCaddyfileAndReload(ctx, d.runner, d.ctMgmtTarget, newCaddy); err != nil {
				return err
			}
			return reloadDnsmasq(ctx, d, append(d.desired, dns.Record{Hostname: hostname, IP: d.ctMgmtIP}))
		},
	}
	c.Flags().BoolVar(&asHTTP, "http", false, "Emit only the http:// listener (default)")
	c.Flags().BoolVar(&asHTTPS, "https", false, "Emit only the HTTPS listener (with tls internal)")
	c.Flags().BoolVar(&asBoth, "both", false, "Emit both http:// and HTTPS listeners")
	c.Flags().BoolVar(&noCaddy, "no-caddy", false, "Direct DNS record only (no Caddy block)")
	c.Flags().StringVar(&directIP, "ip", "", "(--no-caddy only) target IP for the DNS record")
	return c
}

// reloadDnsmasq re-renders the managed dnsmasq config from the given full
// record set and pushes it to ct-dns.
func reloadDnsmasq(ctx context.Context, d *dnsCtx, records []dns.Record) error {
	rendered := dns.RenderDnsmasq(records)
	return dns.WriteDnsmasqAndReload(ctx, d.runner, d.ctDnsTarget, rendered)
}

func newDnsRmCmd() *cobra.Command {
	var yes bool
	c := &cobra.Command{
		Use:   "rm <hostname>",
		Short: "Remove a DNS record and matching Caddy block(s)",
		Args:  cobra.ExactArgs(1),
		RunE: func(_ *cobra.Command, args []string) error {
			hostname := args[0]
			d, err := loadDnsCtx()
			if err != nil {
				return err
			}
			caddyHits := 0
			for _, b := range d.caddyBlocks {
				if b.Hostname == hostname {
					caddyHits++
				}
			}
			extraHit := false
			for _, e := range d.extras {
				if e.Name == hostname {
					extraHit = true
					break
				}
			}
			if caddyHits == 0 && !extraHit {
				return fmt.Errorf("%s not found in Caddyfile or dns-extra.yaml", hostname)
			}
			if !yes {
				ok, err := uiConfirm(fmt.Sprintf("Remove %s (Caddy blocks: %d, extra: %v)?", hostname, caddyHits, extraHit))
				if err != nil {
					return err
				}
				if !ok {
					return nil
				}
			}
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
			defer cancel()
			if caddyHits > 0 {
				newCaddy, _ := dns.RemoveBlocks(d.caddyfile, hostname)
				caddyPath := filepath.Join(d.root, "stacks", "ct-mgmt", "Caddyfile")
				if err := os.WriteFile(caddyPath, newCaddy, 0o644); err != nil {
					return err
				}
				if err := dns.WriteCaddyfileAndReload(ctx, d.runner, d.ctMgmtTarget, newCaddy); err != nil {
					return err
				}
			}
			if extraHit {
				newExtras := make([]dns.ExtraEntry, 0, len(d.extras)-1)
				for _, e := range d.extras {
					if e.Name != hostname {
						newExtras = append(newExtras, e)
					}
				}
				if err := dns.WriteExtra(filepath.Join(d.root, "stacks", "dns-extra.yaml"), newExtras); err != nil {
					return err
				}
			}
			// Recompute desired and push.
			desired := make([]dns.Record, 0, len(d.desired))
			for _, r := range d.desired {
				if r.Hostname != hostname {
					desired = append(desired, r)
				}
			}
			return reloadDnsmasq(ctx, d, desired)
		},
	}
	c.Flags().BoolVarP(&yes, "yes", "y", false, "Skip confirmation")
	return c
}

func newDnsReloadCmd() *cobra.Command {
	c := &cobra.Command{
		Use:   "reload",
		Short: "Re-push current repo state to ct-mgmt + ct-dns and reload services",
		RunE: func(_ *cobra.Command, _ []string) error {
			d, err := loadDnsCtx()
			if err != nil {
				return err
			}
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
			defer cancel()
			if err := dns.WriteCaddyfileAndReload(ctx, d.runner, d.ctMgmtTarget, d.caddyfile); err != nil {
				return err
			}
			return reloadDnsmasq(ctx, d, d.desired)
		},
	}
	return c
}

// uiConfirm wraps the existing ui.Confirm so we don't add a new helper.
func uiConfirm(prompt string) (bool, error) {
	return uiConfirmReal(prompt)
}

func newDnsSyncCmd() *cobra.Command {
	var apply, bootstrap bool
	c := &cobra.Command{
		Use:   "sync",
		Short: "Reconcile live DNS to repo state (dry-run by default)",
		RunE: func(_ *cobra.Command, _ []string) error {
			d, err := loadDnsCtx()
			if err != nil {
				return err
			}
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
			defer cancel()
			liveBytes, err := dns.ReadDnsmasq(ctx, d.runner, d.ctDnsTarget)
			if err != nil {
				return err
			}
			live, err := dns.ParseDnsmasq(liveBytes)
			if err != nil {
				return err
			}
			if bootstrap {
				piholeLines, err := dns.ReadPiholeHostsArray(ctx, d.runner, d.ctDnsTarget)
				if err != nil {
					return err
				}
				// Each line is "<ip> <hostname>".
				type pihRec struct{ ip, host string }
				var legacy []pihRec
				for _, l := range piholeLines {
					parts := strings.Fields(l)
					if len(parts) != 2 {
						continue
					}
					if net.ParseIP(parts[0]) == nil {
						continue
					}
					legacy = append(legacy, pihRec{ip: parts[0], host: parts[1]})
				}
				known := map[string]struct{}{}
				for _, r := range d.desired {
					known[r.Hostname] = struct{}{}
				}
				orphans := []pihRec{}
				for _, l := range legacy {
					if _, ok := known[l.host]; !ok {
						orphans = append(orphans, l)
					}
				}
				if len(orphans) > 0 {
					fmt.Fprintln(os.Stderr, "Pi-hole records with no source in Caddyfile/dns-extra.yaml:")
					for _, o := range orphans {
						fmt.Fprintf(os.Stderr, "  %s -> %s\n", o.host, o.ip)
					}
					fmt.Fprintln(os.Stderr, "Add them to stacks/dns-extra.yaml and re-run --bootstrap.")
					if !apply {
						return fmt.Errorf("orphan legacy records present")
					}
				}
				// On --bootstrap --apply we just write whatever the repo
				// already says — orphans are dropped.
			}
			drift := dns.ComputeDrift(d.desired, live)
			if len(drift.ToAdd)+len(drift.ToRemove)+len(drift.ToChange) == 0 {
				fmt.Println("In sync.")
				return nil
			}
			for _, r := range drift.ToAdd {
				fmt.Printf("+ would add:    %-30s %s\n", r.Hostname, r.IP)
			}
			for _, r := range drift.ToRemove {
				fmt.Printf("- would remove: %-30s %s\n", r.Hostname, r.IP)
			}
			for _, c := range drift.ToChange {
				fmt.Printf("~ would change: %-30s %s (was %s)\n", c.New.Hostname, c.New.IP, c.Old.IP)
			}
			if !apply {
				return fmt.Errorf("drift detected (run with --apply to commit)")
			}
			return reloadDnsmasq(ctx, d, d.desired)
		},
	}
	c.Flags().BoolVar(&apply, "apply", false, "Actually apply changes (default is dry-run)")
	c.Flags().BoolVar(&bootstrap, "bootstrap", false, "Migrate from pihole.toml dns.hosts to the managed dnsmasq file")
	return c
}

// validateUpstream accepts either a full http(s) URL with an IP host, or a
// bare host:port where host is an IPv4/IPv6 literal.
func validateUpstream(s string) error {
	if strings.Contains(s, "://") {
		u, err := url.Parse(s)
		if err != nil {
			return err
		}
		if u.Scheme != "http" && u.Scheme != "https" {
			return fmt.Errorf("scheme must be http or https")
		}
		host := u.Hostname()
		if net.ParseIP(host) == nil {
			return fmt.Errorf("host %q must be an IP literal", host)
		}
		if u.Port() == "" {
			return fmt.Errorf("missing port")
		}
		return nil
	}
	host, port, err := net.SplitHostPort(s)
	if err != nil {
		return err
	}
	if net.ParseIP(host) == nil {
		return fmt.Errorf("host %q must be an IP literal", host)
	}
	if port == "" {
		return fmt.Errorf("missing port")
	}
	return nil
}
