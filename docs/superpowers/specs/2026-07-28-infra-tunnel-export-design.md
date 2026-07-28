# `infra tunnel` — Design

**Status:** Spec — 2026-07-28
**Goal:** Bring the Cloudflare Tunnel's ingress configuration under version control, so the fleet's public routing is diffable, reviewable and recoverable — without migrating away from the remotely-managed tunnel model Cloudflare recommends.

## Background

ct-tunnel (`192.168.3.6`) runs `cloudflare/cloudflared` as a **remotely-managed** tunnel: `ENTRYPOINT ["cloudflared","--no-autoupdate"]`, `CMD ["tunnel","run"]`, with a single `TUNNEL_TOKEN` in `/opt/stacks/ct-tunnel/.env` and no config file anywhere on disk.

Three public hostnames route through it, all live:

| Hostname | Origin |
|---|---|
| `portfolio.<PERSONAL_DOMAIN>` | `http://192.168.3.16:3000` |
| `drive.<PERSONAL_DOMAIN>` | `http://192.168.3.11:3923` |
| `family.<PERSONAL_DOMAIN>` | `http://192.168.3.11:3924` |

Because the tunnel is remotely managed, those hostname→origin mappings exist **only in Cloudflare's Zero Trust dashboard**. They are absent from this repo, and therefore from any durable record — the nightly restic backup covers the CTs and Proxmox nodes, not a workstation checkout, so git and GitHub are the relevant durable copy for a text config like this. Adding a public endpoint produces no diff — which is how `drive` and `family` were added on 2026-07-28 with nothing to show for it in git. If that dashboard state were lost or altered, there is no local record to compare against or restore from.

### Why not convert to a locally-managed tunnel

This was the owner's first instinct and was investigated before being rejected on evidence.

**A tunnel's management mode cannot be changed after creation.** `config_src` (`local` | `cloudflare`) is accepted only by `POST /accounts/{account}/cfd_tunnel`; `PATCH /cfd_tunnel/{id}` accepts only `name` and `tunnel_secret`; `PUT /cfd_tunnel/{id}/configurations` returns `source` as read-only. Cloudflare's own Terraform provider marks `config_src` with `RequiresReplaceIfConfigured()` — changing it destroys and recreates the tunnel. The maintainer states it directly in cloudflared#1029: *"Unfortunately, we don't allow tunnels to be migrated to 'locally managed'. We do want to promote remotely managed tunnels going forward."*

Note in passing: the existing `TUNNEL_TOKEN` base64-decodes to exactly `{a, s, t}`, which maps to `AccountTag` / `TunnelSecret` / `TunnelID` — so a valid `credentials.json` *can* be built from it. That does not help. On registration the edge replies `TunnelIsRemotelyManaged`, then pushes configuration that replaces whatever ingress cloudflared parsed locally; the orchestrator deliberately starts at version `-1` to let remote config override local.

Converting would therefore require a **new tunnel UUID and repointing all three CNAMEs**, including the portfolio. Against that cost:

- Cloudflare documents locally-managed tunnels as *"intended for specific scenarios such as local development, testing, or legacy configurations"*.
- It would not even achieve drift-prevention: the dashboard's Configure action remains available for locally-managed tunnels, so out-of-band edits stay possible.

Export achieves the stated goal — a version-controlled, diffable, recoverable record — with no new tunnel, no DNS changes, no secret on ct-tunnel, and no downtime risk.

## Requirements

**In scope:**

- Read the live ingress configuration from the Cloudflare API and render it to a deterministic file in the repo.
- Show the live configuration in human-readable form without writing anything.
- Detect and report drift between the committed file and live state, with a non-zero exit code so it can be scripted or scheduled later.
- Read-only against Cloudflare. The command must be incapable of modifying the tunnel.
- **Nothing identifying enters the repo.** `github.com/psychonaut0/infra` is **public** (verified: `visibility: PUBLIC`). The exported file must not contain the real public domain, the account ID, or the tunnel ID. The repo's existing convention is placeholders in tracked files (`<PERSONAL_DOMAIN>`, `<PUBLIC_IP>`) with real values in gitignored local files, and this must follow it.
- Follow the existing `infra` CLI patterns (`internal/cmd/<name>.go` + `internal/<domain>/` with unit tests) so it is maintained the same way as `infra dns`.

**Out of scope:**

- **Write-back.** No `import`, `apply`, or `sync --apply`. The dashboard remains the source of truth; this tool observes it. Making the repo authoritative is precisely the locally-managed migration that was rejected above.
- **Scheduled execution and alerting.** No systemd timer, no Telegram. `diff`'s exit code makes both trivial to add later; building them now is speculative.
- **Managing DNS records.** Tunnel hostnames' CNAMEs are created by Cloudflare and are not touched. `infra dns` covers `.lan` records and is unrelated.
- **Private-network / CIDR routes and WARP routing.** Not in use. If they are ever added, the exported `warp-routing` block will surface them, but managing them is not in scope.
- **Multiple tunnels or accounts.** There is one tunnel. A second would be a follow-up.

## Architecture

### Commands

| Command | Behaviour | Exit code |
|---|---|---|
| `infra tunnel ls` | Fetch live config, print a table of hostname → service, plus `source` and `version`. Writes nothing. | 0, or 1 on API error |
| `infra tunnel export` | Fetch live config, render deterministically, write `stacks/ct-tunnel/ingress.yml`. Prints the path and whether content changed. | 0 |
| `infra tunnel diff` | Fetch live config, render, compare byte-for-byte against the committed file. Print a unified diff if they differ. | **0 if identical, 2 if drifted, 1 on error** |

`diff` uses exit code **2** for drift specifically so a caller can distinguish "drifted" from "failed to check" — a scheduled check that treated an API outage as drift would cry wolf.

### Data source

```
GET https://api.cloudflare.com/client/v4/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations
```

Returns `{"result": {"tunnel_id": ..., "version": N, "config": {"ingress": [...], "warp-routing": {...}, "originRequest": {...}}, "source": "cloudflare"}, "success": true}`.

- **Required token scope: `Account → Cloudflare Tunnel → Read`.** Nothing else. No zone scope, no DNS scope, no write permission. The command cannot alter the tunnel even if it had a bug.
- Account ID and tunnel ID are not credentials, but they are identifying, so in a public repo they live in the gitignored local config alongside the token (see below) rather than in a tracked file.

### Local configuration and token

All tunnel-identifying values and the token live in **one gitignored file outside
the repo tree**, so none of it can be committed even by accident:

`~/.config/infra/cloudflare.yml` (mode 0600):

```yaml
account_id: <real account id>
tunnel_id: <real tunnel id>
public_domain: <real domain>   # used to sanitise the export; see below
api_token: <read-only token>   # or omit and use CF_API_TOKEN
```

Token resolution: `CF_API_TOKEN` env var first, then `api_token` from this file.
If neither is present, exit 1 naming both options and the exact dashboard scope.

If the file is missing, exit 1 with a message showing the expected shape. If its
mode is not 0600, warn.

This is the **first** `infra` subcommand to need an API token — every existing one
works over SSH. Consequences handled as part of the work:

- `.gitignore` gains `*.token` and `**/credentials.json` **before any code is
  written**. The repo currently has no rule that would catch either; it covers
  `**/.env`, `*.pem`, `id_ed25519`, `**/secrets/` and `*.local.md`.
- Nothing tunnel-identifying is committed at all — no `tunnel.yml` in the repo.
- `CLAUDE.local.md` gains a pointer recording where this file lives.

### Exported file format

`stacks/ct-tunnel/ingress.yml`, rendered in **cloudflared's own `ingress:` schema**. Two reasons: it is the most readable representation, and it means the file is directly reusable as a starting point if a locally-managed migration is ever revisited.

```yaml
# Generated by `infra tunnel export` — DO NOT EDIT BY HAND.
#
# This is a MIRROR of a remotely-managed Cloudflare tunnel. The Zero Trust
# dashboard is the source of truth; editing this file changes nothing.
# Regenerate with `infra tunnel export`; check for drift with `infra tunnel diff`.

source: cloudflare
version: 7

ingress:
  - hostname: portfolio.<PERSONAL_DOMAIN>
    service: http://192.168.3.16:3000
  - hostname: drive.<PERSONAL_DOMAIN>
    service: http://192.168.3.11:3923
  - hostname: family.<PERSONAL_DOMAIN>
    service: http://192.168.3.11:3924
  - service: http_status:404
```

**The real domain is substituted out.** Every occurrence of `public_domain` (from
the local config) in a hostname is replaced with the literal `<PERSONAL_DOMAIN>`
before writing, matching the convention already used in `CLAUDE.md`. So the
committed file reads `hostname: drive.<PERSONAL_DOMAIN>`, and the real value stays
in the gitignored local config plus `CLAUDE.local.md`.

`diff` applies the *same* substitution to the freshly fetched config before
comparing, so the two sides are always in the same representation. Substitution is
a pure string replacement on a value that must be present in the local config —
if `public_domain` is unset, `export` and `diff` exit 1 rather than risk writing
the real domain.

Consequence to accept: `ingress.yml` is no longer directly usable as a cloudflared
config without substituting the domain back in. That trade is deliberate — the
repo is public.

Rendering rules, all load-bearing for `diff` to be meaningful:

- **Ingress order is preserved exactly.** Cloudflare evaluates rules first-match-wins, so order is semantic, not cosmetic. Never sort it.
- **Map keys within a rule are emitted in a fixed order** (`hostname`, `path`, `service`, then `originRequest` sub-keys alphabetically), so an API response that reorders keys does not produce a spurious diff.
- **Empty and absent optional blocks are omitted**, not rendered as `{}`, so cosmetic API changes stay invisible.
- `source` is recorded and asserted. If it ever reads `local`, something fundamental changed and `diff` reports it prominently rather than as an ordinary field change.
- `version` is recorded. A version-only change means the dashboard was edited and reverted — worth surfacing, not worth hiding.

Because rendering is deterministic, `diff` can compare bytes. Any difference is a real change; there is no need for structural comparison.

### File layout

**Create:**
- `cli/internal/cmd/tunnel.go` — cobra command tree (`ls`, `export`, `diff`).
- `cli/internal/tunnel/client.go` — Cloudflare API client. One method: fetch configuration. Read-only by construction — no write methods exist on the type.
- `cli/internal/tunnel/client_test.go` — against `httptest.Server`, no live calls.
- `cli/internal/tunnel/render.go` — config struct → deterministic YAML.
- `cli/internal/tunnel/render_test.go` — golden-file tests, including key-reorder and optional-block cases.
- `cli/internal/tunnel/config.go` — read `~/.config/infra/cloudflare.yml`, resolve the token, expose `public_domain`.
- `cli/internal/tunnel/config_test.go`
- `cli/internal/tunnel/testdata/` — recorded API response fixtures + golden YAML.
- `stacks/ct-tunnel/ingress.yml` (generated with the domain substituted, then committed)

**Modify:**
- `cli/internal/cmd/root.go` — register `newTunnelCmd()`.
- `.gitignore` — `*.token`, `**/credentials.json`.
- `CLAUDE.md` — `infra` task table row; note that tunnel ingress is now mirrored in the repo.
- `CLAUDE.local.md` — where the API token lives.
- `stacks/ct-tunnel/README.md` — new: what this is, how to regenerate, why the tunnel stays remotely managed.

Each file has one responsibility: API access, rendering, and configuration resolution are separable and independently testable, with the cobra layer holding no logic beyond flag wiring and output formatting.

## Testing

Unit tests only; no live Cloudflare calls in the suite.

- **Client:** `httptest.Server` returning recorded fixtures. Cases: success; `success: false` with API errors; HTTP 403 (wrong token scope) producing an actionable message; malformed JSON; empty ingress.
- **Render:** golden-file comparison. Cases: the real three-hostname config; keys arriving in a different order than emitted; optional `originRequest`/`warp-routing` present and absent; a rule with `path`; `source: local` handling; ingress order preserved under a deliberately shuffled input.
- **Config:** local-config parsing, missing file, missing fields; token from env, token from file, token from neither; wrong file permissions warn.
- **Sanitisation:** the real domain never appears in rendered output; a hostname on an unexpected domain is left intact and reported rather than silently passed through; `public_domain` unset causes a non-zero exit rather than a leak. This is the test that protects a public repo, so it asserts on the absence of the literal domain in the output bytes.
- **Determinism:** rendering the same input twice is byte-identical, and rendering a key-shuffled equivalent input yields identical bytes. This is the property `diff` depends on.

## Rollout

1. Build and test locally (`cd cli && make install`), verify against the live tunnel read-only.
2. Create the API token in the Cloudflare dashboard with scope `Account → Cloudflare Tunnel → Read`, store at `~/.config/infra/cloudflare.token` (0600).
3. `infra tunnel ls` — confirm it reports the three known hostnames and `source: cloudflare`.
4. `infra tunnel export` — inspect the rendered file, confirm it shows `<PERSONAL_DOMAIN>` and no real values, then commit `ingress.yml`. **This is the moment the goal is met:** public routing is now in git history, and on GitHub once pushed.
5. `infra tunnel diff` — expect exit 0.
6. Tag and release, then `infra update -y` across the fleet per the existing CI/CD flow.

**Rollback** is trivial and total: the command is read-only, so nothing about the tunnel can be left in a bad state. Reverting the commit removes the files; the previous `infra` release is a pinned tag away.

## Verification

| Check | Method | Pass criterion |
|---|---|---|
| Read-only guarantee | Inspect `client.go` | No method issues anything but GET; no write methods exist |
| Token scope sufficiency | Run `ls` with a Tunnel:Read-only token | Succeeds |
| Wrong-scope diagnosis | Run with a token lacking the scope | Exits 1 with a message naming the required scope, not a raw 403 |
| Live fidelity | Compare `infra tunnel ls` to the dashboard's Public Hostnames | All three hostnames and origins match exactly |
| Determinism | `infra tunnel export` twice | Second run reports no change; file byte-identical |
| Drift detection | Edit `ingress.yml` by hand, run `diff` | Exit 2, unified diff shown |
| Drift detection, real | Add a throwaway hostname in the dashboard, run `diff`, then remove it | Exit 2 both times, diff matches the change |
| Error vs drift distinction | Run `diff` with an invalid token | Exit **1**, not 2 |
| No secret committed | `git check-ignore` the local-config path | Outside the repo tree entirely |
| **No domain leak** | `git grep` the real domain across tracked files after `export` | **Zero occurrences**; `ingress.yml` shows `<PERSONAL_DOMAIN>` |
| No ID leak | `git grep` the account and tunnel IDs | Zero occurrences |
| Durability | Confirm `ingress.yml` is committed and pushed | In git history and on GitHub. ct-tunnel needs no new ct-backup entry, because no secret or config file is added on that host. |

## Known limitations

1. **The dashboard remains the source of truth.** This tool records and detects; it does not prevent. Someone can still change routing in the dashboard, and the repo will be stale until the next `export`. That is inherent to remotely-managed tunnels and was the accepted trade against migrating.
2. **Drift is only detected when `diff` is run.** No automatic alerting in v1 — see *Follow-up work*.
3. **One tunnel, one account, one domain.** A single `public_domain` is substituted. If the tunnel ever serves hostnames on a second domain, those would be written verbatim — which is why the sanitiser reports unexpected domains instead of ignoring them.
4. **Requires an API token** — the first `infra` subcommand to need one, and it must be provisioned per operator host that wants to run it.
5. **`version` may change without an ingress change** (dashboard edit then revert). Reported as a diff, which is deliberate.

## Follow-up work

- **Scheduled drift check with Telegram alerting**, once there is evidence drift actually happens. `diff` already exits 2 for drift and 1 for error, which is the whole contract a timer needs. The natural home is a systemd timer on ct-mgmt alongside `infra-mirror.timer`, but it needs the repo available there — which it currently is not, so it is a real piece of work rather than a one-liner.
- **A `--check` mode for CI**, if this repo ever gains CI that could assert no-drift on push.
- **Second tunnel support**, if one is ever added.
- Revisit the locally-managed migration only if Cloudflare changes its stance on converting `config_src`, or if drift proves to be a recurring practical problem that detection alone does not solve.
