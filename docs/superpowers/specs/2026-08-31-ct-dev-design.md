# ct-dev — always-on remote development container

**Status:** design
**Date:** 2026-08-31
**VMID:** 116 · **IP:** 192.168.3.19 · **Host:** proxmoxmain

> This repo is public. The work monorepo is referred to as `<WORK_REPO>` and the
> AWS SSO profile as `<AWS_SSO_PROFILE>`; real values live in `CLAUDE.local.md`.

## Problem

Work happens from several machines during the day — sometimes a different OS,
sometimes a borrowed PC, sometimes only a phone — and the primary workstation is
not always up. Today the canonical checkout lives on the workstation, so:

- long-running Claude Code sessions die when the workstation sleeps or the SSH
  connection drops;
- work-in-progress has to be shuttled between machines by hand;
- reaching anything at all requires cloning the repo and authenticating again on
  whatever machine is at hand.

## Goals

1. One canonical, always-on workspace holding `<WORK_REPO>`.
2. Agent sessions that survive disconnect — reattach later from any device.
3. Fire-and-forget batch tasks that run with **no terminal attached** and report
   completion out-of-band.
4. Primary access over SSH (Tailscale); a browser fallback for locked-down
   machines where no client can be installed.

## Non-goals

- **The knowledge base is out of scope.** Explicitly dropped; a separate concern.
- **No backup of ct-dev's contents** (owner's decision — see *Durability*).
- Not a replacement for the workstation. It is where long-lived work *lives*,
  not where all work must happen.

## Architecture

A single unprivileged LXC on proxmoxmain, following the existing fleet pattern
(`nesting=1,keyctl=1`, AppArmor unconfined, `/proc/sys` rw for Docker-in-LXC).

### Sizing

| Resource | Value | Rationale |
|---|---|---|
| vCPU | 6 | Go workspace builds are the spike; host is 6C/12T |
| RAM | 12 GB | JVM search service floors at ~2 GB; Go builds + Postgres + Bun on top |
| Swap | 4 GB | Absorbs build peaks rather than OOM-killing |
| Disk | 120 GB (local-lvm) | Checkout + Go build cache + `node_modules` + images; 278 GB free |

**This fits only because ct-nvr is stopped.** proxmoxmain has ~20 GB available
with recording off. Running full builds while ct-nvr is also running will
oversubscribe a host that already serves Jellyfin transcodes and Immich ML.
Treat "restart ct-nvr" and "run a full build" as competing for the same budget.

### User and path layout

Runs as **`psy`, UID 1000** — a deliberate departure from the root-only fleet —
and replicates the workstation path exactly:

```
/home/psy/Documents/work/travelware/<WORK_REPO>
```

The path is load-bearing, not cosmetic: `~/.gitconfig` selects the work identity
via `includeIf gitdir:~/Documents/work/travelware/`, and the repo's own tooling
assumes that layout. Identical paths mean identical behaviour wherever you sit.

### Toolchain

Pinned to match the monorepo's `preinstall` version checks: Go 1.26, Node 26,
Bun 1.3.13, Docker + compose, AWS CLI v2, tmux, Claude Code.

## Host prerequisites

Two changes on **proxmoxmain itself**, both outside the CT:

1. **`vm.max_map_count=262144`** via `/etc/sysctl.d/`. The compose stack's search
   service requires it and this sysctl is *not* namespaced — it cannot be set
   from inside the container. Hypervisor-level blast radius; small but real.
2. **`/dev/net/tun` passthrough** for Tailscale in an unprivileged LXC:
   `lxc.cgroup2.devices.allow: c 10:200 rwm` plus the matching mount entry. No
   existing CT does this, so it is new ground for the fleet.

## Session persistence

Two mechanisms for two distinct needs.

**Interactive — `tmux`.** SSH in, attach, work, close the lid; reattach from any
device including Termux. `tmux` over `zellij` for Termux behaviour. `claude
--resume` underneath so history survives even a killed pane.

**Batch — `systemd-run --user`.** A `dev-task` wrapper runs `claude -p "<task>"`
as a transient user unit, logs to a file, and notifies via the existing
`@blvckhomelab_bot` Telegram bot on completion or when input is needed. No
terminal is involved at any point. This is the "leave it working and walk away"
path, and it reuses alerting infrastructure that already exists for Gatus.

## Access

**Primary — Tailscale + SSH.** ct-dev joins the tailnet; `ssh psy@ct-dev` works
from the phone and from any machine where a client can be installed.

**Fallback (phase 2) — browser terminal.** `ttyd` bound to **loopback only**,
fronted by Cloudflare Access.

Two constraints shape this and neither is optional:

- **It needs its own Cloudflare Tunnel.** The existing ct-tunnel connector is
  remotely managed under a single token. A second connector for the *same*
  tunnel would round-robin, so an ingress rule pointing at `127.0.0.1:7681`
  would resolve on the ct-tunnel replica and fail. Separate tunnel, separate
  token, separate dashboard entry.
- **Loopback binding is the whole security model.** A LAN-reachable port behind
  Cloudflare Access is bypassable by any host on the LAN — the same trap
  documented for ct-chat's `WEBUI_AUTH_TRUSTED_EMAIL_HEADER`, except the thing
  behind the gate here is an interactive shell. Bind loopback; let cloudflared
  reach it locally.

Phase 2 is deliberately separate. Phase 1 (SSH) solves the stated problem; build
the shell-on-the-internet piece only once the need is demonstrated.

## Credentials

AWS access is via **IAM Identity Center SSO** (`aws sso login --profile
<AWS_SSO_PROFILE>`, wrapped by the repo's own login script). Short-lived
credentials only — no long-lived access keys on the box, which is the main
reason this design is acceptable at all.

Because ct-dev is headless, use **`--no-browser`**: the CLI prints a URL and code
to complete on whatever device is in front of you.

## Durability

**Nothing on ct-dev is backed up.** Owner's decision, recorded deliberately.

The consequence, stated plainly: if the CT is lost, **uncommitted work is gone.**
Everything committed and pushed is safe in the work GitHub org; nothing else is.

The cheap mitigation, if wanted later, is an hourly auto-commit-and-push to a
`wip/ct-dev` branch — WIP safety without putting employer source into personal
Backblaze storage. Not implemented; noted as the obvious next step if a loss
ever stings.

ct-backup must be configured to **skip VMID 116 entirely**, so the work tree
never flows into the personal B2 bucket.

## Phasing

1. **Phase 1 — CT + SSH.** Host prerequisites, CT creation, toolchain, checkout,
   Tailscale, tmux. Solves the stated problem end to end.
2. **Phase 2 — batch tasks.** `dev-task` wrapper + Telegram notification.
3. **Phase 3 — browser fallback.** Dedicated tunnel, loopback `ttyd`, Access.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Home internet / proxmoxmain becomes a hard dependency for paid work | High | Accepted trade-off of self-hosting. Committed work stays in GitHub and remains reachable independently. |
| Resource contention with ct-nvr, Jellyfin, Immich | Medium | ct-nvr stopped; do not run full builds alongside a restarted ct-nvr. |
| CT loss destroys uncommitted work | Medium | Accepted. `wip/ct-dev` push loop available if it becomes painful. |
| Browser terminal exposes a shell to the internet | High | Phase 3 only; loopback bind + dedicated tunnel + Cloudflare Access. |
| Employer source on personal hardware | — | Cleared by owner 2026-08-31. |
