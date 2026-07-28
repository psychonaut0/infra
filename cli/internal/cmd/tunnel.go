package cmd

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/jedib0t/go-pretty/v6/table"
	"github.com/psychonaut0/infra/cli/internal/tunnel"
	"github.com/spf13/cobra"
)

// ingressRelPath is where the mirrored config lives inside the repo.
var ingressRelPath = filepath.Join("stacks", "ct-tunnel", "ingress.yml")

func newTunnelCmd() *cobra.Command {
	c := &cobra.Command{
		Use:   "tunnel",
		Short: "Mirror the Cloudflare Tunnel ingress config into the repo",
		Long: "tunnel reads the live Cloudflare Tunnel configuration (read-only) and\n" +
			"records it in stacks/ct-tunnel/ingress.yml so public routing is\n" +
			"version-controlled, diffable and recoverable from git history.\n\n" +
			"The Zero Trust dashboard remains the source of truth — these commands\n" +
			"only observe it. Requires ~/.config/infra/cloudflare.yml with a token\n" +
			"scoped to Account → Cloudflare Tunnel → Read.",
	}
	c.AddCommand(newTunnelLsCmd())
	return c
}

// loadTunnel resolves the local config and returns a client plus the config.
func loadTunnel() (*tunnel.Client, *tunnel.Config, error) {
	path, err := tunnel.DefaultConfigPath()
	if err != nil {
		return nil, nil, err
	}
	cfg, err := tunnel.LoadConfig(path)
	if err != nil {
		return nil, nil, err
	}
	// LoadConfig is a pure loader and does not print; presentation is ours.
	if w := tunnel.PermissionWarning(path); w != "" {
		fmt.Fprintf(os.Stderr, "warning: %s\n", w)
	}
	return tunnel.NewClient(cfg), cfg, nil
}

// fetch pulls the live config with a bounded timeout.
func fetch(c *tunnel.Client) (*tunnel.TunnelConfig, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	return c.FetchConfig(ctx)
}

// warnUnexpected prints a warning for hostnames outside the configured domain,
// which Render passes through verbatim.
func warnUnexpected(live *tunnel.TunnelConfig, domain string) {
	if stray := tunnel.UnexpectedDomains(live, domain); len(stray) > 0 {
		fmt.Fprintf(os.Stderr,
			"warning: %d hostname(s) are not on the configured public_domain and will be written verbatim: %v\n",
			len(stray), stray)
	}
}

func newTunnelLsCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "ls",
		Short: "Show the live tunnel ingress rules",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			client, cfg, err := loadTunnel()
			if err != nil {
				return err
			}
			live, err := fetch(client)
			if err != nil {
				return err
			}
			t := table.NewWriter()
			t.SetOutputMirror(cmd.OutOrStdout())
			t.AppendHeader(table.Row{"#", "HOSTNAME", "PATH", "SERVICE"})
			for i, in := range live.Ingress {
				host := in.Hostname
				if host == "" {
					host = "(catch-all)"
				}
				t.AppendRow(table.Row{i + 1, host, in.Path, in.Service})
			}
			t.Render()
			fmt.Fprintf(cmd.OutOrStdout(), "\nsource: %s   version: %d   rules: %d\n",
				live.Source, live.Version, len(live.Ingress))
			if live.Source != "cloudflare" {
				fmt.Fprintf(os.Stderr,
					"warning: source is %q, not \"cloudflare\" — this tunnel's management mode changed\n",
					live.Source)
			}
			warnUnexpected(live, cfg.PublicDomain)
			return nil
		},
	}
}
