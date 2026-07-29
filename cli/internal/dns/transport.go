// Package dns provides read/write helpers for live DNS and Caddy configuration
// over SSH. The helpers are thin wrappers over internal/ssh.Runner — all
// business logic lives in the caller.
package dns

import (
	"bytes"
	"context"
	"fmt"
	"strings"

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
	// CtDnsPiholeTomlPath is pihole's own config inside the container. Its
	// [dns] hosts array is NOT managed by infra dns — see ShadowRecord.
	CtDnsPiholeTomlPath = "/etc/pihole/pihole.toml"
)

// ReadCaddyfile fetches the live Caddyfile from ct-mgmt.
func ReadCaddyfile(ctx context.Context, runner *ssh.Runner, target string) ([]byte, error) {
	return runner.Output(ctx, target, "cat "+CtMgmtCaddyfilePath)
}

// WriteCaddyfileAndReload pushes new content and recreates the caddy
// container so the new config is picked up. --force-recreate is required:
// writeRemote uses tee+mv which swaps the inode of the bind-mounted file,
// leaving the running container attached to the orphan old inode. Without
// --force-recreate, `docker compose up -d` sees no compose-file delta and
// leaves the container untouched — the new Caddyfile silently never takes
// effect.
func WriteCaddyfileAndReload(ctx context.Context, runner *ssh.Runner, target string, content []byte) error {
	if err := writeRemote(ctx, runner, target, CtMgmtCaddyfilePath, content); err != nil {
		return fmt.Errorf("push Caddyfile: %w", err)
	}
	if _, err := runner.Output(ctx, target,
		"cd /opt/stacks/ct-mgmt && docker compose up -d --force-recreate caddy"); err != nil {
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
	if _, err := EnsureEtcDnsmasqD(ctx, runner, target); err != nil {
		return err
	}
	cmd := "docker exec -i " + CtDnsContainer + " sh -c '" +
		"tee " + CtDnsConfPath + ".infra-dns.tmp > /dev/null && " +
		"mv " + CtDnsConfPath + ".infra-dns.tmp " + CtDnsConfPath + "'"
	if err := runner.Stream(ctx, target, cmd, bytes.NewReader(content), nil, nil); err != nil {
		return fmt.Errorf("push dnsmasq config: %w", err)
	}
	if _, err := runner.Output(ctx, target, "docker restart "+CtDnsContainer); err != nil {
		return fmt.Errorf("restart pihole: %w", err)
	}
	return nil
}

// EnsureEtcDnsmasqD makes sure pihole.toml has `misc.etc_dnsmasq_d = true` so
// our managed /etc/dnsmasq.d/02-infra-dns.conf is actually read by pihole-FTL.
// Idempotent: returns (true, nil) if a change was applied, (false, nil) if
// already true.
func EnsureEtcDnsmasqD(ctx context.Context, runner *ssh.Runner, target string) (bool, error) {
	check := "docker exec " + CtDnsContainer +
		` grep -q '^[[:space:]]*etc_dnsmasq_d[[:space:]]*=[[:space:]]*true' /etc/pihole/pihole.toml && echo true || echo false`
	out, err := runner.Output(ctx, target, check)
	if err != nil {
		return false, fmt.Errorf("check etc_dnsmasq_d: %w", err)
	}
	if strings.TrimSpace(string(out)) == "true" {
		return false, nil
	}
	flip := "docker exec " + CtDnsContainer +
		` sed -i 's|^\([[:space:]]*\)etc_dnsmasq_d[[:space:]]*=[[:space:]]*false|\1etc_dnsmasq_d = true|' /etc/pihole/pihole.toml`
	if _, err := runner.Output(ctx, target, flip); err != nil {
		return false, fmt.Errorf("flip etc_dnsmasq_d: %w", err)
	}
	return true, nil
}

// ReadPiholeHostsArray returns the raw entries of pihole.toml's `[dns] hosts`
// array.
//
// The whole file is fetched and parsed in Go by ExtractDNSHosts rather than
// filtered with a remote awk range. The awk approach was not section-aware and,
// once the array was written in its empty single-line form, mistook `hosts = []`
// for an opening bracket and returned unrelated config as DNS records.
func ReadPiholeHostsArray(ctx context.Context, runner *ssh.Runner, target string) ([]string, error) {
	out, err := runner.Output(ctx, target,
		"docker exec "+CtDnsContainer+" cat "+CtDnsPiholeTomlPath)
	if err != nil {
		return nil, fmt.Errorf("read pihole.toml: %w", err)
	}
	return ExtractDNSHosts(out), nil
}

// writeRemote pipes content to a temp file then atomically renames it over
// path, so a network drop mid-stream cannot leave the destination half-written.
func writeRemote(ctx context.Context, runner *ssh.Runner, target, path string, content []byte) error {
	tmp := path + ".infra-dns.tmp"
	cmd := "tee " + tmp + " > /dev/null && mv " + tmp + " " + path
	return runner.Stream(ctx, target, cmd, bytes.NewReader(content), nil, nil)
}
