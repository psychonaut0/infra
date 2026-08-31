# ct-dev — always-on remote development container

**Status:** design
**Date:** 2026-08-31 · **Revised:** 2026-09-01
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
- **No backup of ct-dev's contents.** The *base setup* is reproducible instead
  (see *Durability*); working data is deliberately expendable.
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

Installed by the provisioning script, pinned to satisfy the monorepo's
`preinstall` version checks (`check-node.sh` / `check-go.sh`):

| Tool | Version | Notes |
|---|---|---|
| Go | 1.26 | matches `go.work` |
| Node | 26 | frontend builds |
| Bun | 1.3.13 | package manager; `bunx only-allow bun` is enforced |
| Docker + compose plugin | current | for the repo's local service stack |
| AWS CLI | v2 | required for SSO login |
| SST | via repo deps | not installed globally |
| Claude Code | current | one-time interactive login on first use |
| tmux, git, git-lfs, ripgrep, jq | current | baseline |

**AWS tooling is four separate binaries, not one.** Correcting an earlier
assumption in this design: `aws` (CLI v2), `session-manager-plugin`, `pgcli`,
and the project's own **`travelogue` CLI** — a Go binary distributed via
*Bitbucket downloads* (self-updating via `travelogue update`), not part of the
monorepo and not installed by `bun install`. It refuses to run without `aws` on
PATH. `travelogue setup` writes the SSO profiles; `travelogue login`, `tunnel`
and `database` wrap the day-to-day flows.

Two consequences for provisioning:

- Installing the monorepo's dependencies is **not sufficient** to get a working
  environment. The `travelogue` binary must be fetched separately.
- Unlike BLVCKFlow — where `sudo` needs a password and this tooling therefore
  lives userland under `~/.local` — ct-dev has root, so all four install
  system-wide. The `~/.local` workaround does not carry over.

Profile-name scoping is the project scoping: **do not pin a repo-local
`AWS_CONFIG_FILE`**, it breaks the `travelogue` CLI.

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
as a transient user unit and logs to `~/.local/state/dev-task/<name>.log`. No
terminal is involved at any point.

Results are **pulled, not pushed** — no notification service, deliberately. A
`dev-task list` reads unit state (running / exited / failed) and `dev-task log
<name>` tails the output; the shell's MOTD prints anything that finished since
last login. Telegram alerting was considered and rejected: it is a moving part
with credentials to maintain, for a signal that is only useful once you have
already decided to sit down and look.

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

## State migration

The workstation's existing context moves to ct-dev so nothing restarts from zero.

### Claude Code session state

Because ct-dev replicates the workstation path exactly, Claude Code's project
directory encodes to the **identical** name on both machines:

```
~/.claude/projects/-home-psy-Documents-work-travelware-travelogue-social/
```

So the state transplants byte-for-byte and `claude --resume` finds it with no
rewriting. This is the concrete payoff of the UID-1000-and-same-path decision.

What moves (343 MB total):

| Item | Size | Notes |
|---|---|---|
| 3 session transcripts (`*.jsonl`) | 60 MB | the conversations themselves |
| 2 session working directories | ~283 MB | per-session artifacts |
| `memory/` | 40 files | project memories for the work repo |

The `memory/` directory is where employer-specific knowledge (SSO start URL,
profile names, CI quirks, prod-data procedures) lives. It migrates to ct-dev but
**must never enter this public repo** — which is why the committed ct-dev
`CLAUDE.md` carries machine facts only.

Also present and handled separately:

- `…-travelogue-social-frontends-…-src/` (32 KB) — a subdirectory-scoped project;
  moves alongside.
- `…-Documents-travelware-new-travelogue-main/` (24 KB) — a **stale path** from
  before the `work/` reorganisation. Not migrated; confirm before discarding.

### CLAUDE.md files

The workstation hierarchy (`~/CLAUDE.md`, `~/Documents/CLAUDE.md`,
`~/Documents/work/CLAUDE.md`, plus the repo's own committed one) moves so the
same instructions apply.

The ct-dev variant is committed at **`stacks/ct-dev/home/CLAUDE.md`** and
deployed to `~/CLAUDE.md`; `<WORK_REPO>` is substituted at provision time.

**The global `~/CLAUDE.md` cannot be copied verbatim.** It describes BLVCKFlow's
hardware — the Z13, `z13ctl`, Hyprland, the local `llama-server`, Secure Boot,
the dotfiles layout — none of which exists on ct-dev. Copied as-is it would be
actively misleading. It gets a ct-dev variant: shared conventions kept, hardware
and desktop sections replaced with what this box actually is.

### Not migrated

Claude Code credentials. Authentication is a **one-time interactive login** on
ct-dev; sessions and settings carry over, the login does not.

## Durability

**Nothing on ct-dev is backed up, and ct-backup skips VMID 116 entirely** — the
work tree must never reach the personal B2 bucket.

Instead the *base setup* is reproducible. Provisioning lives in `stacks/ct-dev/`
in this repo as an **idempotent script** — following the ct-backup precedent of
a native scripts-and-units stack rather than a compose file — covering:

- `pct create` parameters and the CT config (devices, AppArmor, mounts)
- the host prerequisites above
- the full toolchain install
- user creation, shell, tmux config, `dev-task` wrapper
- SSH keys, Tailscale enrolment, checkout locations

Recovery is *recreate and re-run*, not *restore*. The trade-off, stated plainly:
**uncommitted work is not covered.** Committed and pushed work is safe in the
work GitHub org; anything else dies with the CT. The cheap mitigation if that
ever stings is an hourly auto-commit to a `wip/ct-dev` branch — not implemented,
noted as the obvious next step.

Note the precedent from `ct-backup`: **committed is not deployed.** The script
must actually be run against the CT, not merely live in the repo.

## Phasing

1. **Phase 1 — CT + SSH.** Host prerequisites, CT creation, toolchain, checkout,
   Tailscale, tmux, state migration. Solves the stated problem end to end.
2. **Phase 2 — batch tasks.** `dev-task` wrapper, log files, MOTD summary.
3. **Phase 3 — browser fallback.** Dedicated tunnel, loopback `ttyd`, Access.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Home internet / proxmoxmain becomes a hard dependency for paid work | High | Accepted trade-off of self-hosting. Committed work stays in GitHub and remains reachable independently. |
| Resource contention with ct-nvr, Jellyfin, Immich | Medium | ct-nvr stopped; do not run full builds alongside a restarted ct-nvr. |
| CT loss destroys uncommitted work | Medium | Accepted. `wip/ct-dev` push loop available if it becomes painful. |
| Browser terminal exposes a shell to the internet | High | Phase 3 only; loopback bind + dedicated tunnel + Cloudflare Access. |
| Employer source on personal hardware | — | Cleared by owner 2026-08-31. |
