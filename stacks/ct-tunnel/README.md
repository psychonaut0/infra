# ct-tunnel

Cloudflare Tunnel endpoint. Runs `cloudflared` for selective public access to
internal services — no port-forwards, no UniFi rules.

## This tunnel is remotely managed

`cloudflared tunnel run` with a `TUNNEL_TOKEN`. That makes it a **remotely-managed**
tunnel: hostname→origin rules live in the Cloudflare Zero Trust dashboard, not on
disk. There is no `config.yml` and no `credentials.json` on this host.

The practical consequence: **adding or changing a public hostname is a dashboard
action that produces no diff in this repo.** Two endpoints were added that way on
2026-07-28 with nothing to show for it in git.

`ingress.yml` in this directory is a **mirror** of that dashboard state, written by
`infra tunnel export`. It is *not* the source of truth — editing it changes
nothing at Cloudflare.

| Command | Does | Exit |
|---|---|---|
| `infra tunnel ls` | Print the live ingress rules | 0, or 1 on error |
| `infra tunnel export` | Refresh `ingress.yml`, then commit it | 0 |
| `infra tunnel diff` | Report drift | **0** in sync, **2** drifted, **1** error |

`diff` distinguishes 2 from 1 deliberately: a scheduled check must be able to tell
"the config drifted" from "the check itself failed". Treating an API outage as
drift would cry wolf.

**After changing a public hostname in the dashboard, run `infra tunnel export` and
commit** — otherwise the mirror goes stale silently.

## Setup

All three commands are read-only against Cloudflare and need
`~/.config/infra/cloudflare.yml` (mode 0600):

```yaml
account_id: <account id>
tunnel_id: <tunnel id>
public_domain: <your domain>
api_token: <token>        # or set CF_API_TOKEN instead
```

Create the token at **My Profile → API Tokens → Create Custom Token** with
permission **Account → Cloudflare Tunnel → Read** and nothing else. No zone
permissions, no write.

That file lives outside the repo tree on purpose: this repo is **public**, so the
account ID, tunnel ID and real domain must not be committed. Real values are
recorded in `CLAUDE.local.md`.

To confirm a token really is read-only, compare two requests against a
*non-existent* tunnel ID — nothing real is touched either way:

```
GET   → 1002 Tunnel not found   (read scope reaches the lookup)
PATCH → 1001 Not authorized     (refused before it)
```

The difference between those two responses *is* the absent write scope. A `PATCH`
returning "not found" instead would mean the token can write.

## The export is sanitised, and fails closed

`export` substitutes the real domain for the literal `<PERSONAL_DOMAIN>`. That
alone is not the guarantee — hostname substitution was proven to leak three ways
during development. So after rendering, it checks whether the domain (or the
account/tunnel ID) still appears anywhere in the output bytes and **returns an
error instead of writing** if so.

Expect these refusals, and treat each as a real signal rather than a bug:

- The domain appears in a `service`, `path` or `originRequest` value. Those are
  never substituted, because guessing at their structure risks corrupting a
  config that cannot be inspected first. The error names the offending field and
  value.
- A hostname sits on a **second, unconfigured** domain. `export` refuses unless
  `--allow-unexpected-domains`, because the byte guard structurally cannot check
  a domain it was never told about. `ls` and `diff` only warn.
- A value could not be represented as text and yaml emitted `!!binary`. Base64 is
  valid ASCII, so a domain hidden inside it would pass an ordinary content check.

## Gotchas

- **`version` increments on every dashboard change, including a change and its
  revert.** So `diff` can report drift where the ingress rules are identical and
  only `version` differs. That is deliberate — it tells you someone edited the
  tunnel.
- Rendering is byte-deterministic, which is what lets `diff` compare bytes. It
  relies on `gopkg.in/yaml.v3` sorting map keys and never folding a scalar
  mid-token. **Re-test on any `yaml.v3` bump.**
- `ls` prints live hostnames **unsanitised** — it is for your terminal, not for a
  file. Only `export` sanitises.
- `export` writes via a temp file plus rename, so a failure mid-write cannot leave
  `ingress.yml` truncated.

## Why not a locally-managed tunnel

Converting was investigated and rejected on evidence.

A tunnel's management mode (`config_src`) is **fixed at creation**. `PATCH
/cfd_tunnel/{id}` accepts only `name` and `tunnel_secret`, and Cloudflare's own
Terraform provider marks `config_src` `RequiresReplaceIfConfigured()` — changing
it destroys and recreates the tunnel. The maintainer is explicit in
cloudflared#1029: *"we don't allow tunnels to be migrated to 'locally managed'."*

Converting would therefore mean a **new tunnel UUID and repointing every CNAME**,
including the portfolio. And it would not even prevent drift: the dashboard's
Configure action stays available for locally-managed tunnels.

Worth knowing: the `TUNNEL_TOKEN` base64-decodes to `{a, s, t}` = `AccountTag` /
`TunnelSecret` / `TunnelID`, so a valid `credentials.json` *can* be built from it.
It achieves nothing — the edge replies `TunnelIsRemotelyManaged` and pushes
configuration that overrides any local ingress.

Full reasoning: `docs/superpowers/specs/2026-07-28-infra-tunnel-export-design.md`

## Known gaps

- **No automatic drift alerting.** `diff` must be run. Its exit codes exist so a
  timer can be added later; the natural home is ct-mgmt beside
  `infra-mirror.timer`, but that host has no repo checkout, so it is real work.
- The dashboard stays authoritative. This records and detects; it does not prevent.
- One tunnel, one account, one domain.
- `infra tunnel` needs the local config, so it is an operator-host command. It is
  installed fleet-wide but will not work on a CT without that file — by design.
