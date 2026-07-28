package cmd

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/jedib0t/go-pretty/v6/table"
	"github.com/psychonaut0/infra/cli/internal/repo"
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
	c.AddCommand(newTunnelExportCmd())
	c.AddCommand(newTunnelDiffCmd())
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
		fmt.Fprintln(os.Stderr, w)
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
			// Print warnings before table so operator sees them before data they undermine
			if live.Source != "cloudflare" {
				fmt.Fprintf(os.Stderr,
					"warning: source is %q, not \"cloudflare\" — this tunnel's management mode changed\n",
					live.Source)
			}
			warnUnexpected(live, cfg.PublicDomain)
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
			return nil
		},
	}
}

// ErrDrift is returned by `infra tunnel diff` when the repo file and the live
// config differ. main() maps it to exit code 2, which is distinct from 1
// (error) so a scheduled caller can tell drift from a failed check — treating
// an API outage as drift would cry wolf.
//
// Returned as a sentinel rather than calling os.Exit here, so that cobra's
// PersistentPostRun still runs and the command stays testable. internal/cmd
// contains no os.Exit by design.
var ErrDrift = errors.New("drift detected between the repo and the live tunnel config")

// renderLive fetches the live config and renders it to the bytes that belong in
// the repo. Shared by export and diff so the two can never disagree.
func renderLive() (rendered []byte, path string, err error) {
	client, cfg, err := loadTunnel()
	if err != nil {
		return nil, "", err
	}
	live, err := fetch(client)
	if err != nil {
		return nil, "", err
	}
	warnUnexpected(live, cfg.PublicDomain)
	out, err := tunnel.Render(live, cfg.PublicDomain)
	if err != nil {
		return nil, "", err
	}
	root, err := repo.Locate()
	if err != nil {
		return nil, "", err
	}
	return out, filepath.Join(root, ingressRelPath), nil
}

func newTunnelExportCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "export",
		Short: "Write the live ingress config to stacks/ct-tunnel/ingress.yml",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			out, path, err := renderLive()
			if err != nil {
				return err
			}
			prev, readErr := os.ReadFile(path)
			unchanged := readErr == nil && string(prev) == string(out)

			if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
				return fmt.Errorf("create %s: %w", filepath.Dir(path), err)
			}
			if err := os.WriteFile(path, out, 0o644); err != nil {
				return fmt.Errorf("write %s: %w", path, err)
			}
			if unchanged {
				fmt.Fprintf(cmd.OutOrStdout(), "%s (no change)\n", path)
			} else {
				fmt.Fprintf(cmd.OutOrStdout(), "%s (updated — review and commit)\n", path)
			}
			return nil
		},
	}
}

func newTunnelDiffCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "diff",
		Short: "Report drift between the committed ingress.yml and live config",
		Long: "diff exits 0 when the repo matches live, 2 when they differ, and 1 on\n" +
			"error. The distinct drift code lets a scheduled caller tell real drift\n" +
			"from a failed check.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			out, path, err := renderLive()
			if err != nil {
				return err
			}
			repoBytes, err := os.ReadFile(path)
			if err != nil && !errors.Is(err, os.ErrNotExist) {
				return fmt.Errorf("read %s: %w", path, err)
			}
			d := tunnel.UnifiedDiff(repoBytes, out, path+" (repo)", "live")
			if d == "" {
				fmt.Fprintf(cmd.OutOrStdout(), "in sync: %s matches live\n", path)
				return nil
			}
			fmt.Fprint(cmd.OutOrStdout(), d)
			fmt.Fprintf(cmd.OutOrStdout(), "\nrun `infra tunnel export` to update the repo\n")
			return ErrDrift
		},
	}
}
