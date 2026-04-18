# `infra` CLI — v1 Design

**Status:** Spec — 2026-04-18
**Goal:** A Go-based CLI that wraps the most common SSH + Docker Compose operations across the homelab so the user can run `infra logs sonarr` from anywhere instead of `ssh ct-media && docker logs -f sonarr`. v1 targets terminal ergonomics; later phases will add upstream-update checking, manual backup/restore wrapping the restic system, and a TUI.

## Requirements

**In scope for v1:**

- `infra status` — fleet-wide Docker service overview
- `infra logs <service>` — tail + follow logs for a single service
- `infra restart <service>` — restart a service via compose
- `infra deploy <service|ct>` — pull + up -d (per-service or whole-CT)
- `infra ls` — list service → CT mapping (debug/reference)
- `infra ct status` — Proxmox CT overview (VMID, name, state, IP, CPU/RAM/disk)
- `infra update` — self-update the binary via `git pull` + rebuild + install
- Auto-discover services from `stacks/ct-*/docker-compose.yml`
- Fan-out concurrency for fleet-wide commands
- Table + `--json` output formats
- Reuse user's existing `~/.ssh/config` for transport (no agent, no extra keys)

**Out of v1 scope (architecture must accommodate):**

- `infra update check` / `infra update apply <svc>` — upstream registry checks (Docker Hub, GHCR). Phase 2.
- `infra backup <svc>` / `infra restore <svc>` — wraps the existing `ct-backup` / restic system. Phase 3.
- `infra tui` — Bubble Tea interactive dashboard. Phase 4.

## Architecture

### Repo layout

```
~/Documents/personal/ops/infra/   (this repo root)
├── cli/                           # new Go module, rooted here
│   ├── go.mod
│   ├── go.sum
│   ├── Makefile                   # build / install / deploy / test / fmt
│   ├── cmd/
│   │   └── infra/
│   │       └── main.go            # cobra root, command registration
│   ├── internal/
│   │   ├── discover/              # compose-file walker, service→CT map
│   │   ├── ssh/                   # os/exec wrapper around `ssh`
│   │   ├── repo/                  # locate infra repo root (git or $INFRA_REPO)
│   │   ├── ui/                    # table rendering, colors (lipgloss)
│   │   └── cmd/                   # one file per top-level command
│   │       ├── status.go
│   │       ├── logs.go
│   │       ├── restart.go
│   │       ├── deploy.go
│   │       ├── ls.go
│   │       ├── update.go
│   │       └── ct.go              # subcommand tree root for `ct …`
│   └── testdata/                  # fixture compose tree for tests
└── (stacks/, docs/, scripts/, etc. unchanged)
```

The Go module is rooted at `cli/` — not at the repo root — so the Go tooling doesn't try to own directories that contain YAML/markdown/shell scripts.

### Module selection

- **CLI framework**: `spf13/cobra` — the standard for Go CLIs with subcommand trees. Same framework as `docker`, `gh`, `k9s`.
- **YAML parsing**: `gopkg.in/yaml.v3` — stdlib-quality, handles Docker Compose schema.
- **Output styling**: `charmbracelet/lipgloss` — pairs with future `charmbracelet/bubbletea` for the Phase 4 TUI, minimizing rewrite surface.
- **Table rendering**: `jedib0t/go-pretty/v6/table` — clean ANSI tables, JSON output for free.
- **SSH transport**: **no Go SSH library**. Invoke system `ssh` via `os/exec`. Reuses `~/.ssh/config` for host aliases and keys; matches what the user already does in shell.

### Transport

Every command that reaches out to a CT follows the pattern:

```go
cmd := exec.Command("ssh", "-o", "BatchMode=yes", ct, remoteCmd)
```

The user's existing `~/.ssh/config` (or `/etc/hosts` + SSH keys) resolves hostnames. `BatchMode=yes` prevents passphrase prompts from blocking. For interactive commands (logs in follow mode), `stdin`/`stdout`/`stderr` pipe through transparently. For scripted commands (status, ls), stdout is captured and parsed.

No SSH multiplexing or ControlMaster in v1 — could be added later if latency becomes a concern. At homelab scale (~10 hosts), serial SSH overhead per command is negligible; parallel fan-out within a single `infra status` invocation is what matters and is achieved via goroutines.

## Service Discovery

### Algorithm

```
1. Locate infra repo root:
   - If $INFRA_REPO is set, use it.
   - Else `git rev-parse --show-toplevel` from cwd.
   - Else error: "Cannot find infra repo. Run from inside the repo or set $INFRA_REPO."

2. Glob stacks/ct-*/docker-compose.yml

3. For each compose file:
   - ct_name = basename(dirname(compose_file))   # e.g. "ct-media"
   - Parse YAML, extract top-level `services:` keys
   - For each service name, append to the index:
       services[name] = append(services[name], {ct: ct_name, compose: compose_path})

4. Return the index.
```

### Disambiguation

Most services have unique names across the fleet (`sonarr`, `jellyfin`, `immich_server`, `pihole`). A few — notably `portainer-agent` — exist in every CT that joins Portainer.

Rule: if `services[name]` has a single entry, resolve to it. If multiple, commands must be invoked with the explicit form `infra logs ct-media:portainer-agent`. Unqualified invocation errors out:

```
$ infra logs portainer-agent
Error: 'portainer-agent' runs on multiple CTs: ct-dns, ct-tunnel, ct-nvr, …
Use 'ct-name:portainer-agent' to disambiguate.
```

### Caching

None for v1. Discovery runs on every invocation (~10 ms to walk ~10 compose files). A `--refresh`/`--no-cache` pattern can be added later if caching is introduced.

## Command Semantics

### `infra status`

Fleet-wide Docker service overview. Fans out across all CTs in parallel (one goroutine per CT, waits on all).

```
$ infra status

CT           SERVICE                 STATE      UPTIME
ct-dns       pihole                  running    3d 4h
ct-dns       portainer-agent         running    3d 4h
ct-media     jellyfin                running    2h 12m
ct-media     sonarr                  running    2h 12m
ct-media     radarr                  running    2h 12m
ct-media     prowlarr                running    2h 12m
ct-media     deluge                  running    2h 12m
ct-media     flaresolverr            restarting 10s
…
```

Implementation:
- Per CT: `ssh ct-X docker ps --format '{{.Names}}|{{.State}}|{{.Status}}'`, parse lines, map container names back to compose service names via the compose file.
- Aggregate results, sort by (ct, service), print table.
- Flags: `--json` (emits JSON array of `{ct, service, state, uptime}`), `--ct <name>` (filter to one CT).
- Exit code: 0 if everything ran (even if some services are down); non-zero only on SSH/discovery errors.

### `infra logs <service>`

Streams container logs. Defaults to `tail -n 100 --follow`.

```
$ infra logs sonarr
[tail of recent logs here]
[cursor sits, following…]  # Ctrl-C stops
```

Implementation:
- Resolve `sonarr` → `ct-media`.
- Exec `ssh ct-media docker logs --tail 100 --follow sonarr` with stdout/stderr wired to the terminal.
- On SIGINT, kill the SSH child cleanly (closes ssh → closes remote docker-logs).

Flags:
- `-n <N>` / `--lines <N>` — tail size (default 100).
- `--no-follow` — one-shot dump and exit.
- `--since <duration>` — pass through to `docker logs --since`.

### `infra restart <service>`

```
$ infra restart sonarr
Restart sonarr on ct-media? [y/N] y
Stopping sonarr… done (1.2s)
Starting sonarr… done (0.8s)
```

Implementation:
- Resolve service → CT.
- Prompt confirmation unless `-y` / `--yes`.
- Exec `ssh ct-X docker compose -f /opt/stacks/ct-X/docker-compose.yml restart <service>`.
- Stream progress output through.

### `infra deploy <service|ct>`

Target resolution:
- If argument matches a service name → per-service deploy (compose pull + up -d for that service only).
- If argument matches a ct name (starts with `ct-` and exists in the map) → whole-CT deploy (pull + up -d with no service argument, redeploys the full stack).

```
$ infra deploy sonarr
Pull sonarr on ct-media? [y/N] y
Pulling linuxserver/sonarr:latest … up to date
Recreating sonarr… done
```

```
$ infra deploy ct-media
Pull all services on ct-media? [y/N] y
Pulling jellyfin … up to date
Pulling sonarr … new image (2 MB)
Pulling radarr … up to date
…
Recreating sonarr … done
(Other services unchanged)
```

Flags: `-y`, `--no-pull` (skip pull, just up -d — useful after editing compose locally).

### `infra ls`

```
$ infra ls
CT           SERVICES
ct-dns       pihole, portainer-agent
ct-media     jellyfin, sonarr, radarr, prowlarr, deluge, flaresolverr, portainer-agent
ct-mgmt      caddy, dashboard, gatus, portainer, portainer-agent
…
```

Implementation: print the discovery map. `--json` emits `{ct: [services]}`.

### `infra ct status`

Proxmox-level view. Two parallel SSH calls (proxmoxmain, proxmoxnode), parse `pct list` + `pct status <id>`.

```
$ infra ct status

NODE         VMID  NAME        STATE    IP             CPU   MEM          DISK
proxmoxmain  103   ct-tunnel   running  192.168.3.6    0.1%  38M / 256M   1.2G / 2G
proxmoxmain  104   ct-nvr      running  192.168.3.7    4.2%  2.3G / 4G    8.8G / 24G
proxmoxmain  105   ct-media    running  192.168.3.8    2.8%  4.1G / 8G    5.9G / 16G
…
proxmoxnode  102   ct-dns      running  192.168.3.5    0.3%  98M / 512M   2G / 4G
proxmoxnode  111   ct-ha       running  192.168.3.10   1.5%  1.8G / 4G    4.3G / 16G
```

Implementation:
- `ssh proxmoxmain pct list` → parse table for VMID, status, name.
- For each running CT: `pct exec <id> -- cat /proc/stat /proc/meminfo && df -h /` (single SSH batch) to collect resource usage. Or use `pct config <id>` + `/proc` snapshots from the host — exact mechanism selected during implementation.
- Aggregate, render table. `--json` option.

### `infra update`

```
$ infra update
Current: v0.3.1 (c9224ff)
Origin:  4 commits ahead (e.g. bd5e181)
Build and install the new binary? [y/N] y
  git pull --ff-only                ✓
  go build -o ~/.local/bin/infra.new  ✓
  ~/.local/bin/infra.new --version  ✓ v0.4.0 (bd5e181)
  mv infra.new → infra              ✓
Done. `infra` is now v0.4.0.
```

Implementation:
1. Locate repo (`$INFRA_REPO` or `git rev-parse --show-toplevel`).
2. `git -C $REPO fetch` then compare `HEAD` to `origin/master`.
3. If up-to-date, print and exit 0.
4. If behind, show commit summary, prompt unless `-y`.
5. `git -C $REPO pull --ff-only`.
6. `go build -o ~/.local/bin/infra.new ./cli/cmd/infra` (from repo root).
7. Run `~/.local/bin/infra.new --version` to verify it executes.
8. `mv` the new binary over the old; original is overwritten atomically on Linux.
9. On any failure step 5-7, leave `infra.new` or delete it; original untouched.

Flags:
- `--check` — report state, don't pull or build.
- `-y/--yes` — skip prompt.
- `--ref <branch>` — update to a specific branch or tag (default: repo's current branch).

### Version reporting

The binary embeds version info at build time via `-ldflags`:

```go
var (
    Version = "dev"       // set via -ldflags "-X main.Version=$(git describe)"
    Commit  = "unknown"   // set via -ldflags "-X main.Commit=$(git rev-parse --short HEAD)"
)
```

`infra --version` prints `infra v0.4.0 (bd5e181)`.

## Build & Install

### `cli/Makefile`

```makefile
VERSION := $(shell git -C .. describe --tags --always --dirty 2>/dev/null || echo dev)
COMMIT  := $(shell git -C .. rev-parse --short HEAD 2>/dev/null || echo unknown)
LDFLAGS := -X main.Version=$(VERSION) -X main.Commit=$(COMMIT)
PREFIX  ?= $(HOME)/.local/bin
TARGETS := proxmoxmain proxmoxnode ct-backup

.DEFAULT_GOAL := install

build:         ## compile ./bin/infra
	mkdir -p bin
	go build -ldflags "$(LDFLAGS)" -o bin/infra ./cmd/infra

install: build ## copy ./bin/infra to $PREFIX/infra
	install -m 755 bin/infra $(PREFIX)/infra
	@echo "Installed: $(PREFIX)/infra ($(VERSION))"

deploy: build ## scp the binary to every target host
	@for host in $(TARGETS); do \
		echo "→ $$host"; scp -q bin/infra root@$$host:/usr/local/bin/infra; \
	done

test:
	go test ./...

fmt:
	gofmt -w .
	goimports -w .

clean:
	rm -rf bin
```

`make install` is the default — the local build+install loop the user hits most.

### First-time install

```
cd ~/Documents/personal/ops/infra/cli
make install          # builds + copies to ~/.local/bin/infra
```

Assumes `~/.local/bin` is in `$PATH` (standard on Arch + most distros).

## Error Handling

User-facing errors are plain English with a suggested next step. No Go stack traces.

| Situation | Message |
|---|---|
| Not in repo, `$INFRA_REPO` unset | `Cannot find the infra repo. Run from inside it or set $INFRA_REPO.` |
| Service not found | `Service 'foo' not found. Try 'infra ls' to see available services.` |
| Ambiguous service | `'portainer-agent' runs on multiple CTs: ct-dns, …. Use 'ct-name:service' to disambiguate.` |
| SSH exit code non-zero | Propagate the exit code, print stderr verbatim below a `(remote error)` prefix. |
| `git pull --ff-only` fails (diverged) | `Cannot fast-forward. Resolve the branch state manually.` |
| Build fails in `infra update` | `Build failed (see output above). Existing infra binary unchanged.` |

Exit codes: 0 success, 1 user error (bad args, not found, ambiguous), 2 remote failure (SSH/docker returned non-zero), 3 repo/build problem.

## Testing

- **Discovery**: unit tests against `cli/testdata/` — a fixture compose tree with a few CTs, ambiguous services, parse errors. Covers ~80% of the pre-SSH logic.
- **Resolution**: unit tests for service-name → CT lookup, including the ambiguity branch.
- **SSH wrapper**: thin enough that mocking adds more complexity than it removes. Tested manually against the live infra during development.
- **Commands**: integration tests that shell out to `./bin/infra <cmd>` against a mock SSH server (a fixture script in `cli/testdata/mockssh`) might be considered later. For v1, manual verification.

## Phase 2+ — Planned but Not in v1

Mentioned so the folder structure accommodates them cleanly:

### Phase 2 — `infra update <service>` (upstream-version checker)

Separate from `infra update` (self-update). A subcommand tree:

- `infra update check` — poll registries, report outdated services.
- `infra update apply <service>` — pull + recreate for one service.
- Lives in `cli/internal/cmd/update/` (separate from the self-update at `internal/cmd/update.go` — may need a rename to `self-update.go` when Phase 2 lands).

### Phase 3 — `infra backup <service>` / `infra restore <service>`

Wraps ct-backup:

- `infra backup now` — triggers the nightly backup job out-of-schedule (`ssh ct-backup /usr/local/bin/backup.sh`).
- `infra backup snapshots` — lists restic snapshots.
- `infra restore <service> --from <snapshot-id>` — extracts a service's bind-mounted data or named volumes from a past snapshot into a scratch dir, prompts for promotion.

### Phase 4 — `infra tui`

Bubble Tea dashboard. Reuses discovery, ssh, status internals. Adds `cli/internal/tui/`.

## Follow-up Questions & Decisions Deferred

- **Auto-completion** (bash/zsh/fish): cobra supports generating completion scripts. Worth adding a `infra completion <shell>` command once the command tree is stable. Not blocking for v1.
- **Config file**: none in v1. `$INFRA_REPO` env var is the only configuration. If we ever need per-user preferences (default flags, shell completion, color theme), `~/.config/infra/config.toml` with Viper is the default path.
- **Distribution beyond blvckmain**: `make deploy` pushes to Proxmox hosts + ct-backup. Whether the CLI should *run* on CTs (so server-side crons can call `infra backup now`) is TBD — simple enough to add.

## Success Criteria

v1 is "done" when:

1. `infra status`, `infra logs`, `infra restart`, `infra deploy`, `infra ls`, `infra ct status`, `infra update` all work end-to-end against the live infra.
2. `make install` produces a binary at `~/.local/bin/infra` that the user can run from any directory that's inside (or below) the infra repo.
3. Discovery handles all 9 CTs (including ct-ha, ct-tools) correctly, with ambiguous services reported clearly.
4. Self-update round-trips: `infra update --check` reports correctly, `infra update -y` upgrades cleanly.
5. Errors are actionable English, not Go panics.
6. Unit tests for discovery + resolution pass.
