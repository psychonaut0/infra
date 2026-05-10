# `infra dns` — Design

**Status:** Spec — 2026-04-30
**Goal:** A new `infra dns` subcommand tree that manages LAN DNS records (Pi-hole on ct-dns) and reverse-proxy entries (Caddy on ct-mgmt) as a synchronized pair, so adding or removing a `<name>.lan` service is a single command rather than a multi-step manual edit of `pihole.toml` plus `Caddyfile`.

## Background

The fleet has two pieces of LAN-service plumbing that always move together:

1. A reverse-proxy block in `stacks/ct-mgmt/Caddyfile` (e.g. `http://jellyfin.lan { reverse_proxy 192.168.3.8:8096 }`).
2. A local DNS record in Pi-hole pointing the hostname at ct-mgmt (`192.168.3.12 jellyfin.lan`).

Today both are hand-edited. The Pi-hole side is especially friction-y: records live inside the pihole container's `/etc/pihole/pihole.toml` `dns.hosts` array, which is also written by Pi-hole's web UI, making programmatic edits brittle. The two-step "add Caddy block, then add Pi-hole record, then reload both" sequence has been done manually multiple times during fleet build-out and is error-prone.

There is also a small set of *direct* DNS records (currently `mc-vanilla.lan`, `mc-modded.lan`) that resolve straight to a host, bypassing Caddy. Those are non-HTTP services (Minecraft TCP) and need a different handling path.

## Requirements

**In scope:**

- `infra dns ls` — show every Caddy hostname, its Pi-hole record (or absence), and drift between the two.
- `infra dns add <hostname> <upstream-url> [--http|--https|--both]` — append a Caddy block for the chosen scheme(s), add the matching Pi-hole record (`→ 192.168.3.12`), and reload both.
- `infra dns add <hostname> --no-caddy --ip <ip>` — direct (non-Caddy) DNS record only.
- `infra dns rm <hostname>` — remove matching Caddy block(s) + Pi-hole record. Confirm unless `-y`.
- `infra dns sync [--apply]` — reconcile live state to repo state. Default is dry-run.
- `infra dns reload` — push current repo state to ct-mgmt + ct-dns and reload services, no edits.
- One-time `infra dns sync --bootstrap --apply` migration that moves existing Pi-hole records from `pihole.toml` into a managed dnsmasq config file.

**Out of scope:**

- Editing existing Caddy blocks. `infra dns` only appends new blocks and removes ones it can identify by hostname; everything else (special blocks for `infra-bin.lan`'s file_server, the dashboard container alias, Frigate's HTTPS-with-skip-verify pair) stays hand-edited.
- Adding non-`.lan` hostnames (public DNS, alternate suffixes). Hostname must end in `.lan`. Revisit if the constraint becomes a real limit.
- Pi-hole groups, blocklists, regex rules, conditional forwarding. Out of scope; use Pi-hole UI.
- Caddy admin API integration. Reload is via `docker compose up -d caddy` (recreate) which is what the rest of the fleet uses.

## Architecture

### Source of truth

| Domain | Source of truth (repo) | Live target |
|---|---|---|
| HTTP services | `stacks/ct-mgmt/Caddyfile` | `ct-mgmt:/opt/stacks/ct-mgmt/Caddyfile` + caddy container |
| Direct DNS | `stacks/dns-extra.yaml` (new) | dnsmasq config in pihole container |
| All DNS records | derived from the two above | `/etc/dnsmasq.d/02-infra-dns.conf` inside pihole container |

`infra dns` writes the dnsmasq file as a single atomic blob whose content is fully determined by the repo's Caddyfile + `dns-extra.yaml`. The pihole container's `dns.hosts` array in `pihole.toml` is no longer the live source after the bootstrap migration.

### Pi-hole storage choice

Pi-hole supports custom dnsmasq configs in `/etc/dnsmasq.d/*.conf` (the pihole container header for `pihole.toml` documents this). Records there use dnsmasq syntax:

```
address=/jellyfin.lan/192.168.3.12
address=/mc-vanilla.lan/192.168.3.14
```

`/etc/dnsmasq.d` is already bind-mounted to a docker volume (`pihole-dns`) in `stacks/ct-dns/docker-compose.yml`, so a managed file persists across container recreations.

Why a dedicated file rather than editing `pihole.toml`:
- `pihole.toml` is rewritten by Pi-hole's UI; round-tripping it would require a comment-preserving TOML library and risks merge conflicts with UI edits.
- A dedicated file is wholly owned by `infra dns` — `cat /etc/dnsmasq.d/02-infra-dns.conf` is a sufficient diagnostic.
- dnsmasq syntax is one-line-per-record, trivially diffable.
- Pi-hole's own header text recommends this exact pattern for "any other changes".

### Component layout

```
cli/internal/cmd/dns.go              # cobra command tree + flag wiring
cli/internal/dns/
├── caddyfile.go                     # parser + manipulator
├── caddyfile_test.go
├── dnsmasq.go                       # render/parse 02-infra-dns.conf
├── dnsmasq_test.go
├── extra.go                         # read/write stacks/dns-extra.yaml
├── extra_test.go
├── reconcile.go                     # ls + sync logic
└── reconcile_test.go
```

Each file has a single responsibility. The `dns.go` cobra layer is thin (flag parsing + delegation); all logic is testable without invoking SSH.

### Caddyfile parser

Recognizes two block shapes that `infra dns` can mutate:

```
http://<host> {
    ...
}
```

```
<host> {
    tls internal
    ...
}
```

Block boundaries: opening line matches `^(http://)?(?P<host>[a-z0-9.-]+\.lan)\s*\{`; block ends at the first `^}\s*$` line at top level. Anything not matching this shape (the file_server block, container-alias proxies, multi-line transport stanzas inside another block) is preserved verbatim.

Operations:
- **append(name, scheme, upstream)** — write one or two new blocks at the end of the file.
- **find(name) → []block** — return zero, one, or two blocks matching the hostname.
- **remove(name) → bool** — delete every matching block; return true if any were found.

The parser does *not* attempt to read or modify existing block bodies. It identifies blocks by the opening line, copies through anything in between, and only edits at block boundaries.

### dnsmasq file format

A single managed file at `/etc/dnsmasq.d/02-infra-dns.conf` inside the pihole container. The numeric prefix `02-` is below 99 so it loads after Pi-hole's own configs without overriding them. The file is regenerated from scratch on every write — no in-place edits.

```
# Managed by `infra dns`. Do not edit.
# Generated 2026-04-30T11:42:13Z from stacks/ct-mgmt/Caddyfile + stacks/dns-extra.yaml

address=/backup.lan/192.168.3.12
address=/dns.lan/192.168.3.12
…
address=/mc-vanilla.lan/192.168.3.14
```

Sorted alphabetically for stable diffs.

### `dns-extra.yaml` schema

```yaml
# Direct (non-Caddy) DNS records.
- name: mc-vanilla.lan
  ip: 192.168.3.14
- name: mc-modded.lan
  ip: 192.168.3.14
```

Two fields per entry. Parsed with `gopkg.in/yaml.v3`, which the binary already depends on.

## Subcommand semantics

### `infra dns ls`

Reads:
- `stacks/ct-mgmt/Caddyfile` (parsed for hostnames + their schemes + upstreams).
- `stacks/dns-extra.yaml`.
- Live `02-infra-dns.conf` over SSH from the pihole container.

Output:

```
HOSTNAME            MODE          UPSTREAM                       DNS                  STATUS
backup.lan          http          192.168.3.13:80                192.168.3.12         ok
homeassistant.lan   http+https    http://192.168.3.10:8123       192.168.3.12         ok
infra-bin.lan       http (raw)    <static file_server>           192.168.3.12         ok
jellyfin.lan        http          192.168.3.8:8096               192.168.3.12         ok
mc-vanilla.lan      direct        n/a                            192.168.3.14         ok
nvr.lan             http+https    https://192.168.3.7:8971       192.168.3.12         ok
```

Status column:
- `ok` — both sources agree.
- `(raw)` — Caddy block is in a shape `infra dns` doesn't manage (file_server, container alias, etc.). DNS is still verified.
- `⚠ no DNS` — Caddy block exists, no DNS record. Suggest `infra dns sync --apply`.
- `⚠ no source` — DNS record exists, no Caddy block and not in `dns-extra.yaml`. Suggest `infra dns rm` or add to source.
- `⚠ wrong IP` — DNS resolves to an unexpected IP. Sync would correct it.

Exit code: 0 if no drift, 1 if any `⚠` row.

`--json` flag emits an array of `{hostname, mode, upstream, dns_ip, status}`.

### `infra dns add <hostname> <upstream> [flags]`

Caddy mode (default; `--no-caddy` disables):

```
infra dns add jellyfin.lan http://192.168.3.8:8096
infra dns add proxmox.lan https://192.168.3.2:8006 --https
infra dns add nvr.lan https://192.168.3.7:8971 --both
```

- `<hostname>`: must end in `.lan`. Must not already match a Caddy block or DNS record.
- `<upstream>`: full URL. Scheme determines whether `tls_insecure_skip_verify` is added in the Caddy block.
- Listener-side scheme flags:
  - `--http` (default if none specified) — emit only the `http://name.lan { ... }` block.
  - `--https` — emit only the `name.lan { tls internal; ... }` block.
  - `--both` — emit both.

Direct mode:

```
infra dns add mc-vanilla.lan --no-caddy --ip 192.168.3.14
```

- `--no-caddy` and `--ip <ip>` are required together. Adds an entry to `stacks/dns-extra.yaml`.

Steps (Caddy mode):

1. Validate hostname/upstream/flags; reject duplicates by checking parsed Caddyfile + parsed `dns-extra.yaml`.
2. Build Caddy block(s) from a small set of templates (one for HTTP listener, one for HTTPS listener; each branches on whether the upstream is HTTPS for the `transport` stanza).
3. Append blocks to the in-repo Caddyfile. Save.
4. `scp` Caddyfile → ct-mgmt.
5. `ssh ct-mgmt 'cd /opt/stacks/ct-mgmt && docker compose up -d caddy'`.
6. Regenerate `02-infra-dns.conf` from the new repo state. SCP to ct-dns.
7. `ssh ct-dns 'docker exec pihole pihole reloaddns'`.
8. Print summary: `Added jellyfin.lan (http) → 192.168.3.8:8096; DNS → 192.168.3.12`.

Direct mode skips steps 2–5 (no Caddy edits) and goes straight to 6–7.

### `infra dns rm <hostname> [-y]`

1. Locate hostname in repo: Caddyfile blocks (zero, one, or two), `dns-extra.yaml` entries, `02-infra-dns.conf` records.
2. If found in none, error with a "did you mean…" list of similar hostnames.
3. Confirm unless `-y`. Show what will be removed.
4. Remove from each source.
5. Push + reload as in `add`.

### `infra dns sync [--apply]`

Computes the diff between **desired** (Caddyfile + dns-extra.yaml in repo) and **live** (`02-infra-dns.conf` on ct-dns + Caddyfile on ct-mgmt). Prints:

```
+ would add:    new.lan        192.168.3.12
- would remove: stale.lan      192.168.3.99
~ would change: jellyfin.lan   192.168.3.12 (was 192.168.3.99)
```

`--apply` performs the changes (push + reload).

`--bootstrap` (one-time; combines with `--apply`) reads the existing Pi-hole `dns.hosts` array from `pihole.toml`, cross-references with Caddyfile + `dns-extra.yaml`, and writes the consolidated set into `02-infra-dns.conf`. Any Pi-hole record whose hostname has no Caddy/extra source is reported and the user is prompted to add it to `dns-extra.yaml`. The bootstrap does not edit `pihole.toml` — it lets the dnsmasq.d file take over while leaving the legacy array in place.

### `infra dns reload`

Re-pushes the current repo state to both targets and reloads. No edits. Useful after `git pull` or when ct-mgmt/ct-dns has been recreated.

## SSH targets

`infra dns` uses the same `ssh.Runner` + `discover.Index.SSHTarget` mechanism as the rest of the CLI. The two destinations are:

- ct-mgmt (`192.168.3.12`) — Caddy.
- ct-dns (`192.168.3.5`) — Pi-hole.

Both are in the embedded `hosts.yaml` snapshot, so `infra dns` works from any host without ssh-config setup.

## Repo locator requirement

Mutating commands (`add`, `rm`, `sync --apply` outside `--bootstrap`) require running inside the repo because they edit committed files. They use `repo.Locate()` and error with the existing "infra repo not found" message if absent.

Read-only commands (`ls`, `sync` dry-run, `reload`) work from any host using the embedded snapshot. They derive desired-state from the embedded Caddyfile snapshot — wait, this is a wrinkle; see "Open question" below.

## Open question — embedded Caddyfile?

`infra dns ls` from a non-repo host needs to know what the desired state *is* in order to flag drift. Two options:

- **A.** Make `infra dns ls` repo-only. Acceptable: drift checks are mostly done by the operator, who works from the repo anyway.
- **B.** Add Caddyfile contents (or the parsed hostname list + scheme + upstream) to the embedded fleet snapshot, alongside hosts and services. Free read-only fleet inspection from any node.

**Decision:** Option **A** for v1. Bake-in is doable but the snapshot already grows with every release, and `infra dns ls` is primarily an operator tool. Revisit if the friction shows up.

## Error handling

| Where | Failure | Behavior |
|---|---|---|
| `add` | duplicate hostname | "already exists in Caddyfile / dns-extra.yaml; use rm first" |
| `add` | hostname doesn't end `.lan` | reject with usage hint |
| `add` | malformed upstream URL | reject with parser error |
| `add` | `scp` to ct-mgmt fails | abort before DNS edits; repo Caddyfile already saved → user can retry |
| `add` | `docker compose up -d caddy` fails | report stderr; DNS step is skipped; suggest `infra dns sync --apply` after fixing |
| `add` | DNS reload fails | report; record was written to file, will activate on next reload |
| `rm` | hostname not found anywhere | error with "did you mean X, Y, Z" |
| `sync` | drift detected without `--apply` | non-zero exit, drift list to stdout |
| `sync --apply` | partial failure | report which side succeeded/failed; safe to retry |
| `ls` | SSH to ct-dns fails | flag DNS column as `?`, exit 0 with warning |

## Testing

- **Unit (caddyfile.go):** parse fixture Caddyfiles (current + edge cases: empty, only special blocks, mixed shapes); append; remove; preserve unrelated blocks verbatim.
- **Unit (dnsmasq.go):** render N-entry sets to bytes; parse back; round-trip stable; alphabetical ordering deterministic.
- **Unit (extra.go):** add/remove/duplicate-detect.
- **Unit (reconcile.go):** given fixture (caddy parsed, dns-extra parsed, dnsmasq parsed), produce drift report. Cover all four `⚠` cases.
- **Integration:** `httptest`-style mock for SSH not feasible, but the SSH layer is already exercised by other commands. Key dispatch logic is the reconcile + push sequence; covered by unit tests on the bytes that get pushed.
- **Manual acceptance:** end-to-end `add → ls → rm → ls → sync` against the live fleet on a throwaway hostname like `test.lan`.

## Migration plan

1. Land the spec, plan, and code in a feature branch.
2. Cut a release (next version: v0.5.0 — minor bump for new command tree).
3. Roll the new binary fleet-wide via `infra update -y`.
4. Run `infra dns sync --bootstrap --apply` once. Review the diff (should match current Pi-hole + Caddy state). Confirm.
5. After bootstrap, the dnsmasq.d file is canonical for DNS. Future edits via `infra dns add/rm/sync`. Pi-hole's `dns.hosts` array is dormant and can be cleared manually if desired.

## Repo layout additions

```
cli/internal/cmd/dns.go
cli/internal/dns/
├── caddyfile.go
├── caddyfile_test.go
├── dnsmasq.go
├── dnsmasq_test.go
├── extra.go
├── extra_test.go
├── reconcile.go
└── reconcile_test.go
stacks/dns-extra.yaml                          # initial entries: mc-vanilla, mc-modded
docs/superpowers/specs/2026-04-30-infra-dns-design.md
```

`stacks/ct-mgmt/Caddyfile` is touched by the bootstrap (no schema change; entries are already in the right shape). `stacks/ct-dns/docker-compose.yml` requires no changes — `/etc/dnsmasq.d` is already volume-mounted.

## Success criteria

`infra dns` is "done" when:

1. `infra dns ls` from inside the repo prints the full table with `ok` for every existing entry after bootstrap.
2. `infra dns add foo.lan http://192.168.3.99:1234` appends one Caddy block, scp's the Caddyfile, recreates Caddy, writes the dnsmasq record, reloads Pi-hole, and `dig foo.lan @192.168.3.5` returns `192.168.3.12`.
3. `infra dns rm foo.lan -y` reverses (2) cleanly.
4. Manual edits to the Caddyfile (e.g. adding a new block by hand) are picked up by `infra dns sync` and reconciled into Pi-hole.
5. Existing manually-added entries (jellyfin, proxmox, nvr, mc-vanilla, infra-bin, etc.) survive the bootstrap untouched and continue resolving.
6. The `make deploy` workflow on ct-mgmt — `cd /opt/stacks/ct-mgmt && docker compose up -d caddy` — still works after `infra dns add`. (No changes to compose; this is just confirming we don't break it.)
