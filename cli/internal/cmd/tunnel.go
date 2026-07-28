package cmd

import (
	"bytes"
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

// warnUnexpected prints a warning for hostnames outside the configured
// domain, which Render passes through verbatim, and returns that same list so
// a caller that needs to act on it (currently only export, which gates on it
// — see newTunnelExportCmd) doesn't have to recompute it.
func warnUnexpected(live *tunnel.TunnelConfig, domain string) []string {
	stray := tunnel.UnexpectedDomains(live, domain)
	if len(stray) > 0 {
		fmt.Fprintf(os.Stderr,
			"warning: %d hostname(s) are not on the configured public_domain and will be written verbatim: %v\n",
			len(stray), stray)
	}
	return stray
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

// renderLive fetches the live config and renders it to the bytes that belong
// in the repo. Shared by export and diff so the two can never disagree.
//
// It also returns the list of hostnames on a domain other than
// cfg.PublicDomain (see tunnel.UnexpectedDomains, and warnUnexpected which
// already prints the same warning both commands have always shown). Only
// export gates on that list — see newTunnelExportCmd; ls and diff keep the
// existing warn-only behaviour and may ignore the returned slice.
//
// renderLive also refuses to return rendered bytes that contain cfg.AccountID
// or cfg.TunnelID. tunnel.Render only knows about the public domain — it has
// no way to sanitise these — so a private-network route legitimately shaped
// like "service: <tunnel-uuid>.cfargotunnel.com" would otherwise write the
// tunnel ID straight into this public repo. Consistent with Render's own
// domain guard, this fails closed rather than substituting a placeholder.
func renderLive() (rendered []byte, path string, unexpected []string, err error) {
	client, cfg, err := loadTunnel()
	if err != nil {
		return nil, "", nil, err
	}
	live, err := fetch(client)
	if err != nil {
		return nil, "", nil, err
	}
	unexpected = warnUnexpected(live, cfg.PublicDomain)
	out, err := tunnel.Render(live, cfg.PublicDomain)
	if err != nil {
		return nil, "", nil, err
	}
	if id, value := leakedIdentifier(out, cfg); id != "" {
		return nil, "", nil, fmt.Errorf(
			"refusing to render: %s %q appears in the rendered config — this would leak an internal Cloudflare identifier into a public repo",
			id, value)
	}
	root, err := repo.Locate()
	if err != nil {
		return nil, "", nil, err
	}
	return out, filepath.Join(root, ingressRelPath), unexpected, nil
}

// leakedIdentifier reports whether rendered contains cfg's AccountID or
// TunnelID verbatim, returning a human-readable name for whichever one was
// found (and the value itself) or ("", "") if neither is present. These are
// Cloudflare account-internal identifiers, not secrets the renderer's domain
// guard has any reason to know about, but they are exactly as unsafe to
// commit to a public repo.
func leakedIdentifier(rendered []byte, cfg *tunnel.Config) (name, value string) {
	if cfg.AccountID != "" && bytes.Contains(rendered, []byte(cfg.AccountID)) {
		return "account ID", cfg.AccountID
	}
	if cfg.TunnelID != "" && bytes.Contains(rendered, []byte(cfg.TunnelID)) {
		return "tunnel ID", cfg.TunnelID
	}
	return "", ""
}

func newTunnelExportCmd() *cobra.Command {
	var allowUnexpectedDomains bool
	c := &cobra.Command{
		Use:   "export",
		Short: "Write the live ingress config to stacks/ct-tunnel/ingress.yml",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			out, path, unexpected, err := renderLive()
			if err != nil {
				return err
			}
			// export is the only command that writes a tracked file, so it is
			// the only one gated on unexpected domains — ls and diff keep the
			// existing warn-only behaviour (warnUnexpected, called inside
			// renderLive, already printed the same warning for all three).
			if len(unexpected) > 0 && !allowUnexpectedDomains {
				return fmt.Errorf(
					"refusing to write %s: %d hostname(s) are not on the configured public_domain: %v — pass --allow-unexpected-domains to write anyway",
					path, len(unexpected), unexpected)
			}
			prev, readErr := os.ReadFile(path)
			unchanged := readErr == nil && string(prev) == string(out)

			dir := filepath.Dir(path)
			if err := os.MkdirAll(dir, 0o755); err != nil {
				return fmt.Errorf("create %s: %w", dir, err)
			}
			if err := writeFileAtomic(path, out, 0o644); err != nil {
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
	c.Flags().BoolVar(&allowUnexpectedDomains, "allow-unexpected-domains", false,
		"write ingress.yml even if it contains hostnames on a domain other than public_domain")
	return c
}

// writeFileAtomic writes data to a temporary file in the same directory as
// path, then renames it into place, so that a failure partway through the
// write (e.g. a full disk) never leaves path holding truncated or partial
// content — the visible file is always either the previous complete content
// or the new complete content, never a fragment. The temp file is created in
// the same directory as path (not the OS default temp dir) so the rename is
// same-filesystem and therefore atomic. On any failure before the rename, the
// temp file is removed.
func writeFileAtomic(path string, data []byte, mode os.FileMode) error {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, filepath.Base(path)+".tmp-*")
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	// If we return before the rename succeeds, clean up the temp file rather
	// than leaving it behind.
	succeeded := false
	defer func() {
		if !succeeded {
			os.Remove(tmpPath)
		}
	}()

	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Chmod(tmpPath, mode); err != nil {
		return err
	}
	if err := os.Rename(tmpPath, path); err != nil {
		return err
	}
	succeeded = true
	return nil
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
			// diff keeps the warn-only behaviour for unexpected domains — only
			// export gates on that list — so it's ignored here.
			out, path, _, err := renderLive()
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
