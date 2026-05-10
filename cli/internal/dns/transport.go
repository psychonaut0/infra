// Package dns provides read/write helpers for live DNS and Caddy configuration
// over SSH. The helpers are thin wrappers over internal/ssh.Runner — all
// business logic lives in the caller.
package dns

import (
	"bytes"
	"context"
	"fmt"

	"github.com/psychonaut0/infra/cli/internal/ssh"
)

const (
	// CtMgmtCaddyfilePath is where Caddy reads its config inside ct-mgmt's
	// stack directory (bind-mounted into the caddy container).
	CtMgmtCaddyfilePath = "/opt/stacks/ct-mgmt/Caddyfile"
	// CtDnsContainer is the docker container name to exec into on ct-dns.
	CtDnsContainer = "pihole"
	// CtDnsConfPath is the managed dnsmasq config inside the pihole container.
	CtDnsConfPath = "/etc/dnsmasq.d/02-infra-dns.conf"
)

// ReadCaddyfile fetches the live Caddyfile from ct-mgmt.
func ReadCaddyfile(ctx context.Context, runner *ssh.Runner, target string) ([]byte, error) {
	return runner.Output(ctx, target, "cat "+CtMgmtCaddyfilePath)
}

// WriteCaddyfileAndReload pushes new content and recreates the caddy
// container so the new config is picked up. Caddy's reload-on-config-change
// isn't reliable through the Docker bind-mount in our setup; full recreate
// is the safe path.
func WriteCaddyfileAndReload(ctx context.Context, runner *ssh.Runner, target string, content []byte) error {
	// Stream the file via stdin to ssh+tee.
	if err := writeRemote(ctx, runner, target, CtMgmtCaddyfilePath, content); err != nil {
		return fmt.Errorf("push Caddyfile: %w", err)
	}
	if _, err := runner.Output(ctx, target,
		"cd /opt/stacks/ct-mgmt && docker compose up -d caddy"); err != nil {
		return fmt.Errorf("recreate caddy: %w", err)
	}
	return nil
}

// ReadDnsmasq fetches the managed dnsmasq file from inside the pihole container.
// Returns empty bytes if the file doesn't exist yet (pre-bootstrap state).
func ReadDnsmasq(ctx context.Context, runner *ssh.Runner, target string) ([]byte, error) {
	out, _ := runner.Output(ctx, target,
		"docker exec "+CtDnsContainer+" cat "+CtDnsConfPath+" 2>/dev/null || true")
	return out, nil
}

// WriteDnsmasqAndReload pushes new content into the pihole container and
// triggers a DNS reload.
func WriteDnsmasqAndReload(ctx context.Context, runner *ssh.Runner, target string, content []byte) error {
	cmd := "docker exec -i " + CtDnsContainer + " tee " + CtDnsConfPath + " > /dev/null"
	if err := runner.Stream(ctx, target, cmd, bytes.NewReader(content), nil, nil); err != nil {
		return fmt.Errorf("push dnsmasq config: %w", err)
	}
	if _, err := runner.Output(ctx, target,
		"docker exec "+CtDnsContainer+" pihole reloaddns"); err != nil {
		return fmt.Errorf("reloaddns: %w", err)
	}
	return nil
}

// ReadPiholeHostsArray reads the legacy `dns.hosts` array from pihole.toml
// for the bootstrap migration. Returns the raw lines like
// "192.168.3.12 jellyfin.lan".
func ReadPiholeHostsArray(ctx context.Context, runner *ssh.Runner, target string) ([]string, error) {
	out, err := runner.Output(ctx, target,
		`docker exec `+CtDnsContainer+` sh -c "awk '/^  hosts = \\[/,/^  \\]/' /etc/pihole/pihole.toml"`)
	if err != nil {
		return nil, fmt.Errorf("read pihole.toml: %w", err)
	}
	var lines []string
	for _, raw := range bytes.Split(out, []byte("\n")) {
		s := string(bytes.TrimSpace(raw))
		// Match `"<ip> <host>",`
		if len(s) < 4 || s[0] != '"' {
			continue
		}
		s = s[1:]
		end := bytes.IndexByte([]byte(s), '"')
		if end <= 0 {
			continue
		}
		lines = append(lines, s[:end])
	}
	return lines, nil
}

// writeRemote pipes content through `ssh ... tee <path>`.
func writeRemote(ctx context.Context, runner *ssh.Runner, target, path string, content []byte) error {
	cmd := "tee " + path + " > /dev/null"
	return runner.Stream(ctx, target, cmd, bytes.NewReader(content), nil, nil)
}
