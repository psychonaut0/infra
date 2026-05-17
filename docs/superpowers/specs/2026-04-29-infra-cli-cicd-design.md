# `infra` CLI — CI/CD & Distribution Design

**Status:** Spec — 2026-04-29
**Goal:** Replace the manual `git pull && go build` update flow with a tag-triggered CI pipeline that publishes signed binaries to a LAN-served mirror, so any host (workstation or CT) can self-update without a Go toolchain or repo checkout. Adds a passive "update available" notice so installations don't silently drift.

## Background

`infra` v1 ships via two mechanisms:

1. `infra update` — clones the repo (or relies on an existing checkout), runs `git pull --ff-only`, then `go build`, then atomic install. Requires Go installed and a working repo checkout on every host.
2. `cli/Makefile` `deploy` target — `scp` to a hardcoded list of three hosts (proxmoxmain, proxmoxnode, ct-backup). Manual, partial coverage, and out of date the moment the list grows.

In practice, neither path covers every machine that could benefit (CTs, Termux, future workstations), so installations drift. This design eliminates the per-host build dependency and adds an automated, auditable release pipeline.

## Requirements

**In scope:**

- GitHub Actions release workflow triggered by git tags matching `v*`.
- GitHub Actions CI workflow (lint + test + build smoke) triggered by `cli/**` changes only — monorepo-aware, doesn't fire on `stacks/`, `docs/`, etc.
- Cross-compiled artifacts: `linux/amd64` (all servers + blvckmain) and `linux/arm64` (Termux).
- Mirror service on ct-mgmt that polls GitHub Releases and re-publishes artifacts to a LAN URL with no auth required.
- `infra update` reworked to consume the mirror's `manifest.json` by default, with `--from-source` retained as a fallback for the historical git+build path.
- Passive daily update check: every `infra` invocation prints a non-blocking footer when a newer version is available.
- One-liner bootstrap (`curl … | sh`) for fresh hosts that don't yet have `infra` installed.

**Out of scope:**

- Off-LAN updates (Termux when away from home WiFi). Decision: accept this limitation; Termux updates only on home WiFi. Revisit later if friction appears.
- Multi-channel releases (stable vs edge). Single rolling channel keyed off tags is sufficient at this scale.
- Code signing / SLSA provenance. SHA256 verification against the manifest is the integrity boundary.
- Migrating the existing `make deploy` target's host list — the mirror replaces it; the target is retired.

## Architecture

```
   ┌─ developer ─┐
   │ git tag v…  │
   │ git push    │
   └──────┬──────┘
          ▼
   ┌─────────────────────────────┐
   │  GitHub Actions release.yml │
   │  matrix: amd64, arm64       │
   │  → GH Release (binaries +   │
   │    .sha256)                 │
   └──────────────┬──────────────┘
                  │  poll every 5 min (PAT)
                  ▼
   ┌─────────────────────────────┐
   │  ct-mgmt: infra-mirror      │
   │  systemd timer + sync.sh    │
   │  Caddy serves /var/www/     │
   │   infra-bin/                │
   └──────────────┬──────────────┘
                  │  https://infra-bin.lan/...
                  ▼
   ┌─────────────────────────────┐
   │  any host running `infra`   │
   │  - passive check (daily)    │
   │  - `infra update` applies   │
   └─────────────────────────────┘
```

## Components

### 1. GitHub Actions

#### `.github/workflows/ci.yml`

Path-filtered linting and testing. Does not publish.

- **Triggers:** `push` to `master` and `pull_request`, both gated by `paths: ['cli/**', '.github/workflows/ci.yml']`.
- **Jobs:**
  - `test`: `go vet ./...`, `go test ./...` from `cli/` working dir.
  - `build-smoke`: cross-compile `GOOS=linux GOARCH={amd64,arm64}` matrix, discard the binary. Catches breakage on the arch we don't dev on.
- **Caching:** `actions/setup-go@v5` with `cache-dependency-path: cli/go.sum`.

#### `.github/workflows/release.yml`

Tag-triggered build and publish.

- **Trigger:** `push` of tag matching `v*` (e.g., `v0.4.0`). Tags are plain `vX.Y.Z` — when the repo grows other release artifacts, migrate to a `cli/vX.Y.Z` prefix.
- **Job: `build`** with matrix `[{goos: linux, goarch: amd64}, {goos: linux, goarch: arm64}]`:
  1. `actions/checkout@v4`.
  2. `actions/setup-go@v5` (Go version pinned in `cli/go.mod`).
  3. Resolve version metadata: `TAG=${GITHUB_REF_NAME}`, `SHA=$(git rev-parse --short HEAD)`.
  4. Build:
     ```sh
     cd cli
     GOOS=$GOOS GOARCH=$GOARCH go build \
       -trimpath \
       -ldflags "-s -w -X main.Version=$TAG -X main.Commit=$SHA" \
       -o ../infra-$GOOS-$GOARCH \
       ./cmd/infra
     ```
  5. `sha256sum infra-$GOOS-$GOARCH > infra-$GOOS-$GOARCH.sha256`.
  6. `actions/upload-artifact@v4` so the second job can collect both arches.
- **Job: `release`** (needs `build`):
  1. `actions/download-artifact@v4` to gather both arch outputs.
  2. `softprops/action-gh-release@v2` creates a release for the tag, attaches both binaries and both `.sha256` files.

GitHub Actions on private repos uses the included free CI minutes (~2,000/month for personal accounts). Each release run is well under a minute.

### 2. Mirror service (ct-mgmt)

Native systemd. No Docker — this is a tiny script and a timer; containerizing adds bookkeeping for no gain.

#### Files (deployed to ct-mgmt; sources committed in the repo)

```
stacks/ct-mgmt/infra-mirror/
├── sync.sh                 # poll → download → verify → publish
├── install.sh              # served via Caddy for fresh-host bootstrap
├── infra-mirror.service    # oneshot systemd unit
├── infra-mirror.timer      # OnBootSec=2min, OnUnitActiveSec=5min, Persistent=true
└── README.md               # first-time install steps
```

Runtime layout on ct-mgmt:

```
/opt/infra-mirror/sync.sh
/opt/infra-mirror/install.sh
/etc/infra-mirror/token              # 0600 root:root, fine-grained PAT
/etc/infra-mirror/state.json         # last seen tag (mirror's bookkeeping)
/var/www/infra-bin/                  # Caddy root
├── manifest.json
├── install.sh
├── linux/
│   ├── amd64/infra
│   └── arm64/infra
```

#### `sync.sh` algorithm

1. `curl -sf -H "Authorization: Bearer $(cat /etc/infra-mirror/token)" https://api.github.com/repos/psychonaut0/infra/releases/latest` → parse `tag_name` and asset URLs with `jq`.
2. Compare `tag_name` to `state.json.last_tag`. If equal, exit 0.
3. Create a tmpdir. For each `(arch, asset_url, sha_url)`:
   - Download binary and its `.sha256` file (with the same auth header — assets on private repos require it).
   - Verify `sha256sum -c`.
4. Atomically move binaries into `/var/www/infra-bin/linux/<arch>/infra` (write-rename pattern; `mv` on the same filesystem is atomic).
5. Write a fresh `manifest.json` (see schema below) using a write-rename pattern so consumers never read a half-written file.
6. Update `state.json`.
7. On any failure mid-pipeline, exit non-zero and leave the existing manifest + binaries untouched. journald captures the error for `journalctl -u infra-mirror`.

#### `manifest.json` schema

```json
{
  "version": "v0.4.0",
  "commit": "bd5e181",
  "published_at": "2026-04-29T12:34:56Z",
  "binaries": {
    "linux/amd64": {
      "url": "https://infra-bin.lan/linux/amd64/infra",
      "sha256": "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8"
    },
    "linux/arm64": {
      "url": "https://infra-bin.lan/linux/arm64/infra",
      "sha256": "ef797c8118f02dfb649607dd5d3f8c7623048c9c063d532cc95c5ed7a898a64f"
    }
  }
}
```

#### Systemd units

`infra-mirror.service`:
```ini
[Unit]
Description=infra CLI release mirror sync
After=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/infra-mirror/sync.sh
```

`infra-mirror.timer`:
```ini
[Unit]
Description=infra CLI release mirror sync (every 5 min)

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
```

#### Caddy

Add to ct-mgmt's existing Caddyfile (or `Caddyfile` block in `stacks/ct-mgmt/`):

```caddy
infra-bin.lan {
    root * /var/www/infra-bin
    file_server
    encode gzip
}
```

#### DNS

Add to ct-dns Pi-hole local records: `infra-bin.lan → 192.168.3.12` (ct-mgmt).

#### PAT scope

Fine-grained PAT, single repository (`psychonaut0/infra`), permissions:
- `Contents: Read` (required to download release assets on a private repo)
- `Metadata: Read` (mandatory companion)

No write permissions, no other repo access. Stored at `/etc/infra-mirror/token` (mode 0600, owned by root). Rotation: regenerate annually or on suspected compromise; the token file is the only thing to update.

### 3. `infra update` rework

Reworked to consume the mirror by default; old git+build path retained behind a flag.

#### Default flow

1. Resolve mirror URL (default `https://infra-bin.lan/manifest.json`, override via `--mirror <url>` or `INFRA_MIRROR_URL` env).
2. `GET` the manifest with a 5s timeout.
3. Parse, look up `binaries["linux/<runtime.GOARCH>"]`. Error if the current arch isn't listed.
4. Compare manifest version to the embedded `Version`. Use semver compare; treat the embedded `dev` placeholder as "always update".
5. If equal, print "Already on latest (vX.Y.Z)." and exit 0.
6. If newer, prompt unless `-y`. On confirm:
   - Download binary to `<install_dir>/infra.new` with a 60s timeout.
   - Verify sha256 against the manifest entry. On mismatch, delete and abort.
   - `os.Chmod(0755)` then `os.Rename(infra.new, infra)`. Rename is atomic on Linux when source and destination are on the same filesystem.
7. Print `Done. infra is now vX.Y.Z.`

`<install_dir>` is determined by inspecting `os.Executable()`. The new binary is written next to the running one and replaces it. (Linux allows replacing a running executable's inode; the running process keeps its open file handle and exits cleanly.)

#### Flags

- `--check` (existing) — report mirror version vs current, no download.
- `-y / --yes` (existing) — skip prompt.
- `--from-source` (new) — force the historical `git pull && go build` flow. Useful for testing local CLI changes or when the mirror is intentionally out of date.
- `--mirror <url>` (new) — override the manifest URL.

#### Error paths

- Mirror unreachable → `Mirror at <url> unreachable. If you have a repo checkout, retry with --from-source.`
- Manifest parse error → `Mirror returned invalid manifest. Check 'journalctl -u infra-mirror' on ct-mgmt.`
- Arch not in manifest → `No binary for linux/<arch> in manifest. Mirror may be mid-publish; retry shortly.`
- SHA256 mismatch → `Downloaded binary failed checksum verification. Aborted; existing binary unchanged.`
- Atomic rename failure → propagate OS error; original binary unchanged.

### 4. Passive update check

A new package `cli/internal/updatecheck` hooks into the root cobra `PersistentPreRun` and the post-execution path.

#### Cache file

`$XDG_CACHE_HOME/infra/update-check.json` (default `~/.cache/infra/`):

```json
{
  "last_check_at": "2026-04-29T08:12:00Z",
  "latest_version": "v0.5.0"
}
```

#### Algorithm

`PersistentPreRun`:
1. Read the cache file. If `last_check_at` is within 24h, skip the network step but keep `latest_version` in process memory.
2. Otherwise spawn a goroutine:
   - `GET https://infra-bin.lan/manifest.json` with 2s timeout.
   - On success, write a new cache file (write-rename).
   - On any failure, do nothing — never log, never block.

After the command finishes (cobra `PersistentPostRun`):
1. If the cached `latest_version > current Version` (semver compare), print to stderr:
   ```
   [infra update available: v0.4.0 → v0.5.0 — run 'infra update']
   ```
   in dim style (lipgloss faint).

#### Suppression

The footer is **not** printed when any of:
- `--json` flag (or any output mode that needs to be machine-parseable)
- stderr is not a TTY
- `INFRA_NO_UPDATE_CHECK=1` env var set
- Cache miss / network error (no `latest_version` available)
- Current version is `dev` (developer build; nag-free)

The 24h network call is also fully skipped when `INFRA_NO_UPDATE_CHECK=1`.

### 5. Bootstrap (`install.sh`)

Served at `https://infra-bin.lan/install.sh` (copied into `/var/www/infra-bin/` by `sync.sh`, source committed at `stacks/ct-mgmt/infra-mirror/install.sh`).

Contents (sketch):

```sh
#!/bin/sh
set -eu
ARCH=$(uname -m)
case "$ARCH" in
  x86_64)  GOARCH=amd64 ;;
  aarch64) GOARCH=arm64 ;;
  *) echo "Unsupported arch: $ARCH" >&2; exit 1 ;;
esac
DEST=${INFRA_INSTALL_DIR:-/usr/local/bin}
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
curl -fsSL "https://infra-bin.lan/linux/$GOARCH/infra" -o "$TMP"
EXPECTED=$(curl -fsSL https://infra-bin.lan/manifest.json | jq -r ".binaries[\"linux/$GOARCH\"].sha256")
ACTUAL=$(sha256sum "$TMP" | awk '{print $1}')
[ "$EXPECTED" = "$ACTUAL" ] || { echo "checksum mismatch" >&2; exit 1; }
install -m 755 "$TMP" "$DEST/infra"
echo "Installed: $DEST/infra"
```

Usage on a fresh CT: `curl -fsSL https://infra-bin.lan/install.sh | sh`.

### 6. Repo layout additions

```
.github/workflows/
├── ci.yml
└── release.yml
stacks/ct-mgmt/infra-mirror/
├── sync.sh
├── install.sh
├── infra-mirror.service
├── infra-mirror.timer
└── README.md
cli/internal/
├── manifest/                    # shared types + http client for manifest.json
└── updatecheck/                 # passive check
```

`cli/Makefile`'s `deploy` target is removed. The `make install` local-build path stays for development.

## Data Flow

1. Developer commits CLI changes, runs CI on PRs (path-filtered).
2. Developer tags `v0.4.0`, pushes the tag. `release.yml` builds both arches, attaches binaries to a GH Release.
3. ct-mgmt's `infra-mirror.timer` fires within ≤5 min, `sync.sh` pulls the new release, verifies, atomically swaps `/var/www/infra-bin/`, writes new `manifest.json`.
4. Any host running `infra <cmd>` reads `manifest.json` (24h-cached), notices the new version, prints the footer.
5. User runs `infra update`, host downloads the binary, verifies sha256, atomic-renames over its own executable.

End-to-end latency: tag push → CT consumer notice ≤ ~5 min in the worst case.

## Error Handling Summary

| Layer | Failure mode | Behavior |
|---|---|---|
| `release.yml` | Build/test failure | Red CI; no GH Release created; downstream untouched. |
| `sync.sh` | GH API unreachable | `curl` fails → exit non-zero → systemd retries on next tick. Existing manifest untouched. |
| `sync.sh` | sha256 mismatch | Tmpdir discarded; existing manifest untouched; journald logs the failure. |
| `sync.sh` | Disk full | Same as above. |
| `infra update` | Mirror unreachable | Actionable error suggesting `--from-source`. |
| `infra update` | Manifest malformed | Actionable error suggesting the mirror diagnostic command. |
| `infra update` | sha256 mismatch | Abort, no replacement. |
| `infra update` | Disk/rename failure | Abort, original binary intact. |
| Passive check | Any error | Silent. |

## Testing

### Unit tests (`go test ./cli/...`)

- `internal/manifest`: parse manifest fixtures (happy path, missing arch, malformed JSON).
- `internal/updatecheck`: cache TTL logic (recent vs stale), version comparison, suppression rules (TTY/JSON/env).
- `internal/cmd/update`: end-to-end against a `httptest.Server` serving fixture manifests + binaries. Cases: happy path, sha mismatch, mirror 500, mirror timeout, arch missing.

### CI smoke

`build-smoke` job already cross-compiles both arches; protects against arm64-specific syntax regressions.

### Manual acceptance

1. Push a `v0.0.1-rc1` tag (pre-release) → confirm `release.yml` succeeds and uploads both binaries + sha256s.
2. SSH ct-mgmt → `journalctl -u infra-mirror -f` → wait for next tick or `systemctl start infra-mirror.service` → confirm `manifest.json` reflects the new tag.
3. From blvckmain: `curl -s https://infra-bin.lan/manifest.json | jq` → matches.
4. From a CT (e.g., ct-tunnel) without a Go toolchain or repo checkout: `curl -fsSL https://infra-bin.lan/install.sh | sh` → `infra --version` → matches the tag.
5. Push `v0.0.1` → wait → run `infra` on any host → footer appears → `infra update -y` → version bumps.

## Migration

1. Land the spec, CI workflow, release workflow.
2. Cut a first real release (`v0.4.0` matching the post-rework code).
3. Stand up the mirror on ct-mgmt; confirm it pulls `v0.4.0`.
4. On each currently-installed host, run `infra update` once with the new code in place; subsequent updates are passive.
5. Drop `make deploy` from `cli/Makefile`.
6. Bootstrap remaining hosts (Termux, any others) via `install.sh`.

## Follow-ups Not in v1

- Cloudflare Tunnel route for off-LAN updates (Termux when away). Adds a Cloudflare Access policy to gate the public hostname; the manifest URL becomes the public one. Defer until friction proves it.
- Stable vs edge channels (separate manifests). Defer until there's a real need to publish unstable builds.
- SLSA provenance / cosign signatures. Overkill for personal infra today; revisit if the threat model changes.
- Auto-apply mode (`infra update --watch` or systemd timer that runs `infra update -y` nightly on CTs). Tempting, but a passive notice + manual apply is safer for now.

## Success Criteria

The CI/CD work is "done" when:

1. `ci.yml` runs only on `cli/**` changes; random `stacks/` or `docs/` PRs do not consume CI minutes.
2. `release.yml` produces both arch binaries + sha256s on `v*` tag push, end-to-end in under 2 minutes.
3. ct-mgmt mirror picks up new releases within 5 minutes of publication, without human intervention.
4. `infra update` on any LAN host with no Go toolchain and no repo checkout successfully upgrades.
5. Passive check footer appears the first time a host runs `infra` after a release.
6. `curl … install.sh | sh` brings a fresh CT to a working `infra --version` on first try.
7. The `make deploy` target is removed and not missed.
