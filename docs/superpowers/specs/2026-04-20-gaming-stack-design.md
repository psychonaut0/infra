# Gaming Stack Design

**Status:** Spec — 2026-04-20
**Goal:** Run two Minecraft servers (1 vanilla/Paper for up to 20 casual players, 1 medium-to-heavy modded for ~5 players) on the homelab, reachable by friends over the internet despite CGNAT, with room to add other game servers (Valheim, Satisfactory, Palworld, etc.) later without structural changes. All configuration lives in git; the homelab's existing compose/Portainer/Gatus/`infra` CLI patterns handle operations.

## Requirements

**In scope:**

- Two Minecraft servers:
  - **mc-vanilla** — Paper, 4 GB heap, up to 20 players, port 25565.
  - **mc-modded** — Forge or Fabric (modpack TBD), 8 GB heap, ~5 players, port 25566.
- Public reachability behind CGNAT without port forwarding, on a free tier — via **Playit.gg**. Friends connect with a vanilla Minecraft client; no install on their side.
- LAN-direct connectivity for low-latency local play from `192.168.3.14:25565` / `:25566`.
- Config-as-code: every server parameter (version, heap, MOTD, difficulty, whitelist, OPs, modpack identity) lives in `stacks/ct-games/docker-compose.yml` and env files in git; no UI that becomes the source of truth.
- Three-tier storage:
  - Live worlds on NVMe for TPS.
  - Daily on-box `.tgz` snapshots on mergerfs for fast local rollback.
  - Existing restic → B2 job for off-site insurance.
- LAN-exposed RCON so any `mcrcon` / `rcon-cli` client on the home network can administer in-game.
- Gatus monitoring with Telegram alerts on TCP port outage.
- `infra` CLI auto-picks up the new services (no CLI changes required).
- Future game servers: add as new compose services in the same CT; no new CT until the CT outgrows 16 GB RAM.

**Out of scope (deferred, non-blocking):**

- `infra rcon <service> <cmd>` subcommand — small Go PR after the stack is live; not required at launch.
- Pterodactyl or any other game-server panel. Considered during brainstorming; dropped because its container-ownership model conflicts with compose-as-source-of-truth. Portainer covers the "glance + console + restart" UX.
- Web-based RCON UI (RCON-Web-Admin). Option if a browser/mobile UI is wanted later.
- A dashboard tile on the ct-mgmt custom dashboard for MC status. ~10-line follow-up edit.
- Native Minecraft protocol-level Gatus check with player count. TCP is sufficient for liveness.
- Router / firewall changes. Playit tunnels are outbound-only; nothing to forward (CGNAT anyway).

**Non-goals:**

- Multi-tenancy / friends administering their own servers. Admin is solo.
- Exposing the stack to the public internet beyond the game ports themselves (no admin UI on the internet).
- Running Wings / Pterodactyl Panel. Explicitly rejected.

## Architecture

One new unprivileged Debian 13 LXC on **proxmoxmain**. No other CTs touched, no new services on other nodes. (Per standing preference, proxmoxnode is hands-off.)

### Topology

```
proxmoxmain (192.168.3.2)
└── ct-games (VMID 112, 192.168.3.14)
      docker-compose stack (all on shared `gamesnet` bridge network):
      ├── mc-vanilla       itzg/minecraft-server   NVMe: /opt/stacks/ct-games/data/vanilla/
      ├── mc-modded        itzg/minecraft-server   NVMe: /opt/stacks/ct-games/data/modded/
      ├── playit-vanilla   playit-cloud/playit-agent → mc-vanilla:25565 (via gamesnet)
      ├── playit-modded    playit-cloud/playit-agent → mc-modded:25566 (via gamesnet)
      ├── backup-vanilla   itzg/mc-backup           → /mnt/archives/vanilla/
      └── backup-modded    itzg/mc-backup           → /mnt/archives/modded/
```

### Data-plane flow (players)

```
Friend over internet
    → <hostname>.joinmc.link  (Playit relay)
    → playit-vanilla (agent container inside ct-games)
    → mc-vanilla:25565 over gamesnet
    → Paper / Vanilla server

LAN client (blvckmain, phone)
    → 192.168.3.14:25565 (LAN-published port on ct-games)
    → mc-vanilla:25565 (Docker port mapping)
```

### Why this shape

- **Single CT, compose-only orchestration** matches every other `ct-*` in the homelab. No mixed-orchestration (no Wings), no Pterodactyl Panel — rejected during brainstorming because its "Wings owns the containers" model conflicts with config-as-code.
- **Shared Docker network `gamesnet`** lets Playit agents reach MC servers by service name without host networking — cleaner, and avoids the `network_mode: host` port-conflict risk.
- **itzg/mc-backup sidecars** handle save-quiesce correctly via RCON (`save-off` / `save-all` / `save-on`) — purpose-built for Minecraft, no custom scripts.
- **NVMe for live worlds, mergerfs for archives**: modded MC is chunk-IO-heavy and punishes HDD latency; archives tolerate HDD speed. Three-tier storage (NVMe live → mergerfs cold → B2 off-site) gives graduated restore times.
- **No new Caddy routes, no new reverse proxy**: nothing in this stack exposes a web UI. Existing Portainer on ct-mgmt covers container ops.

## CT Specification

| Param | Value |
|-------|-------|
| VMID | 112 |
| Hostname | ct-games |
| IP / Gateway | 192.168.3.14 / 192.168.3.1 |
| Template | debian-13-standard |
| vCPU | 6 |
| RAM | 16 GB |
| Swap | 4 GB (larger than the usual 512 MB — safety net for JVM spikes) |
| Boot disk | 40 GB on `local-lvm` (NVMe) |
| Unprivileged | yes |
| Nesting | yes (required for Docker) |
| AppArmor | unconfined (standard Docker-in-LXC pattern) |
| proc / sys | rw bind mount (standard Docker-in-LXC pattern) |

**Bind mount (Proxmox host → CT):**

| Host path | CT mount | Mode | Purpose |
|-----------|----------|------|---------|
| `/mnt/cloud/volumes/games/archives` | `/mnt/archives` | rw | `itzg/mc-backup` sidecars write `.tgz` snapshots here |

Set via `pct set 112 -mp0 /mnt/cloud/volumes/games/archives,mp=/mnt/archives`.

## Stack Layout

Repo path: `stacks/ct-games/`, mirroring every other CT. Deployed to `/opt/stacks/ct-games/` on the CT.

```
stacks/ct-games/
├── docker-compose.yml
├── .env.example            # committed — template with placeholder values
└── .env                    # gitignored — real secrets (created manually)
```

### Compose services

**`gamesnet`** — single user-defined bridge network. All six services attached.

**`mc-vanilla`** (`itzg/minecraft-server:latest`)

Env highlights:
- `EULA=TRUE`
- `TYPE=PAPER`
- `VERSION=LATEST`
- `MEMORY=4G` (hard heap cap — not advisory)
- `MAX_PLAYERS=20`
- `DIFFICULTY=normal` (final value TBD by operator)
- `MOTD=...` (TBD)
- `OPS=...` / `WHITELIST=...` (operator-set)
- `ENABLE_RCON=true`, `RCON_PASSWORD=${RCON_PASSWORD_VANILLA}`, `RCON_PORT=25575`

Ports: `25565:25565/tcp` (LAN + Docker), `25575:25575/tcp` (RCON, LAN).

Volume: `./data/vanilla:/data`.

**`mc-modded`** (`itzg/minecraft-server:latest`)

Env highlights:
- `EULA=TRUE`
- `TYPE=FORGE` or `TYPE=FABRIC` — **decision owed before first deploy**; drives modpack sourcing.
- Modpack identity (one of):
  - `MODPACK_PLATFORM=MODRINTH` + `MODPACK=<url|id>` (preferred if on Modrinth).
  - `CF_API_KEY=${CF_API_KEY}` + `CF_SLUG=...` for CurseForge.
  - `FTB_MODPACK_ID=...` for FTB packs.
  - Manual: `MODS_FILE=/mods.txt` or a pre-populated `/data/mods/`.
- `MEMORY=8G`
- `MAX_PLAYERS=10`
- Same RCON block, password `RCON_PASSWORD_MODDED`, port `25575` inside the container.

Ports: `25566:25565/tcp`, `25576:25575/tcp` (RCON).

Volume: `./data/modded:/data`.

**`playit-vanilla`, `playit-modded`** (`ghcr.io/playit-cloud/playit-agent:latest`)

Env: `PLAYIT_SECRET=${PLAYIT_SECRET_VANILLA}` (or `_MODDED`).

Networks: `gamesnet` (not `network_mode: host`). Agent reaches its MC server by service name (`mc-vanilla:25565`).

Volumes: named volumes `playit-vanilla-data`, `playit-modded-data` for agent state.

**`backup-vanilla`, `backup-modded`** (`itzg/mc-backup:latest`)

Env:
- `RCON_HOST=mc-vanilla` (or `mc-modded`)
- `RCON_PORT=25575`
- `RCON_PASSWORD=${RCON_PASSWORD_VANILLA}` (or `_MODDED`)
- `BACKUP_INTERVAL=24h`
- `INITIAL_DELAY=30m`
- `PRUNE_BACKUPS_DAYS=14`
- `PAUSE_IF_NO_PLAYERS=true`
- `BACKUP_NAME=world-%Y%m%d-%H%M%S.tgz`
- `DEST_DIR=/backups`

Volumes:
- `./data/vanilla:/data:ro` (source)
- `/mnt/archives/vanilla:/backups` (destination on mergerfs)

### Environment variables (`.env`)

Gitignored; `.env.example` is committed with placeholder values.

| Var | Purpose |
|-----|---------|
| `RCON_PASSWORD_VANILLA` | Random 32-char. Used by `mc-vanilla` + `backup-vanilla` + LAN RCON clients. |
| `RCON_PASSWORD_MODDED` | Same, for modded. |
| `PLAYIT_SECRET_VANILLA` | Claim token from Playit.gg dashboard. |
| `PLAYIT_SECRET_MODDED` | Same, second tunnel. |
| `CF_API_KEY` | Optional, only if modded pack is CurseForge-sourced. |

## Networking & Exposure

### Port-exposure matrix

| Service | Container port | Host (ct-games) port | LAN reachable | Internet reachable | Notes |
|---------|----------------|----------------------|---------------|--------------------|-------|
| mc-vanilla game | 25565/tcp | 25565 | yes | via Playit relay only | LAN-direct play |
| mc-modded game | 25565/tcp | 25566 | yes | via Playit relay only | LAN-direct play |
| mc-vanilla RCON | 25575/tcp | 25575 | yes | **no** | LAN-only administration |
| mc-modded RCON | 25575/tcp | 25576 | yes | **no** | LAN-only administration |
| playit-* agents | — | — | — | outbound only | No inbound; CGNAT-friendly |
| backup-* sidecars | — | — | — | — | Internal container only |

### Playit.gg setup (one-time, out-of-band)

Done via the Playit dashboard, not via compose:

1. Sign in / register at playit.gg.
2. Create tunnel: protocol "Minecraft Java", free tier hostname.
3. Note the claim secret → paste into `.env` as `PLAYIT_SECRET_VANILLA`.
4. In the Playit tunnel config: route to `mc-vanilla:25565` (agent resolves it via gamesnet).
5. Repeat for modded → `PLAYIT_SECRET_MODDED`, routing `mc-modded:25566`.

The resulting hostnames (something like `yourserver.joinmc.link`) are shared with friends. Anyone can join by pasting the hostname into vanilla Minecraft.

### LAN RCON

LAN exposure chosen deliberately (home network is trusted; strong random passwords mitigate plaintext-auth concerns). Client tools that just work:

- `mcrcon -H 192.168.3.14 -P 25575 -p "$RCON_PASSWORD_VANILLA" list`
- `rcon-cli --host 192.168.3.14 --port 25576 --password "$RCON_PASSWORD_MODDED" "say hi"`
- Phone apps: MC-Ctrl, rcontrol.

Deferred follow-up: `infra rcon <service> <cmd>` subcommand so day-to-day CLI use doesn't need the port exposed — small Go PR after launch.

### Pi-hole entries (optional, quality-of-life)

Add to ct-dns local DNS so you can type hostnames instead of IPs on LAN:

- `mc-vanilla.lan → 192.168.3.14`
- `mc-modded.lan → 192.168.3.14`

Remote players abroad are unaffected — they use the Playit hostname.

### Router / firewall

No changes. CGNAT makes port forwards moot, and Playit tunnels are outbound-only.

## Data Placement & Storage

Three tiers, each with a specific purpose and restore-time profile.

| Tier | What | Where | Medium | Retention | Restore time |
|------|------|-------|--------|-----------|--------------|
| **Live** | Active world data, mods, configs, player data | `/opt/stacks/ct-games/data/<server>/` on ct-games | NVMe (local-lvm) | until deleted | n/a (it's live) |
| **On-box cold** | Daily `.tgz` snapshots per server | `/mnt/cloud/volumes/games/archives/<server>/world-<ts>.tgz` (mergerfs, HDD) | HDD | 14 days (sliding window via `PRUNE_BACKUPS_DAYS`) | seconds — local `tar -xf` |
| **Off-site** | Captured by existing ct-backup → restic → B2 | Backblaze B2 bucket (existing) | cloud | per ct-backup retention policy | hours (bandwidth-bound) |

### Capacity

| Tier | Expected size | Free budget |
|------|---------------|-------------|
| Live | ~30 GB combined at steady state | 40 GB CT boot disk (NVMe) |
| On-box cold | <20 GB for 14 days of both servers | 1.6 TB free on mergerfs |
| Off-site | Same as live | existing B2 budget |

### Interaction with existing ct-backup

- **/opt/stacks/ct-games/** (live NVMe world data): existing ct-backup's per-CT source list already pulls `/opt/stacks` from every CT nightly. **Verify during implementation** that the include list covers `/opt/stacks/ct-games/` recursively; if not, add it.
- **/mnt/cloud/volumes/games/archives/** (on-box cold): **not** auto-included. ct-backup's bulk-data list per CLAUDE.md only covers Immich / samba/psy / \*arr configs / Jellyfin config / Frigate config. Leaving it un-backed-up to B2 is intentional — it would duplicate the NVMe data we're already shipping off-site. Archive path stays local-only. No exclusion rule needed; no-op.

## Monitoring

### Gatus

Add to `stacks/ct-mgmt/gatus/config.yaml` under the `important` tier (same as Jellyfin / Immich):

```yaml
- name: mc-vanilla
  group: games
  url: "tcp://192.168.3.14:25565"
  interval: 60s
  conditions: ["[CONNECTED] == true"]
  alerts: [{type: telegram}]

- name: mc-modded
  group: games
  url: "tcp://192.168.3.14:25566"
  interval: 60s
  conditions: ["[CONNECTED] == true"]
  alerts: [{type: telegram}]
```

TCP reachability on the game port is a sufficient liveness signal — the server will refuse TCP if the JVM crashed or the container exited. Telegram alerts flow through the existing `@blvckhomelab_bot` path, same as every other check.

### Dashboard

The ct-mgmt custom dashboard (Preact + Bun SSR) currently queries Proxmox for node stats and has hand-configured service tiles. Adding MC tiles is an incremental edit in `stacks/ct-mgmt/dashboard-src/` — out of initial scope. Gatus status page + Portainer cover the gap.

### Portainer

ct-mgmt's Portainer picks up the new containers once ct-games registers with the existing Portainer via `portainer-agent` on port 9001 — identical to how every other CT is onboarded in this homelab. Standard pattern; no spec-level detail needed.

## CLI Integration

The `infra` CLI auto-discovers services by walking `stacks/ct-*/docker-compose.yml`. As soon as `stacks/ct-games/docker-compose.yml` lands in the repo, these commands work with no code change:

```
infra ls                        # mc-vanilla, mc-modded, playit-*, backup-* appear
infra status                    # ct-games row in the fleet overview
infra logs mc-modded            # tail+follow modded server console
infra restart mc-vanilla        # compose restart single service
infra deploy ct-games           # rsync stack + compose up -d
infra deploy mc-vanilla         # per-service targeted deploy
infra ct status                 # ct-games appears in PVE overview
```

**Deferred:** `infra rcon <service> <command>` — separate small PR. Until then, `mcrcon` or `rcon-cli` against the exposed LAN port; or `ssh ct-games docker exec mc-vanilla rcon-cli list`.

## Future Extensibility

Adding non-MC game servers = adding compose services. No new CT unless RAM runs out.

### Pattern per new game

```yaml
services:
  <game>:
    image: <game-image>
    volumes: [./data/<game>:/game]
    ports: [<game-port>:<game-internal>]
    networks: [gamesnet]

  playit-<game>:
    image: ghcr.io/playit-cloud/playit-agent:latest
    env: { PLAYIT_SECRET: ${PLAYIT_SECRET_<GAME>} }
    networks: [gamesnet]

  backup-<game>:
    # image has built-in backup? Point its output dir at /mnt/archives/<game>/
    # otherwise: offen/docker-volume-backup sidecar, cron to /mnt/archives/<game>/
```

### Known-good image references

| Game | Image | Typical RAM | Notes |
|------|-------|-------------|-------|
| Valheim | `lloesche/valheim-server` | 2-4 GB | Built-in scheduled backups |
| Satisfactory | `wolveix/satisfactory-server` | 6-12 GB | UDP protocol |
| Terraria | `beardedio/terraria` | 512 MB – 1 GB | tshock |
| Palworld | `thijsvanloef/palworld-server-docker` | 8-16 GB | Heavy; likely forces CT RAM bump |
| Factorio | `factoriotools/factorio` | 1-2 GB | Autosave built-in |

### When 16 GB RAM is insufficient

Scenario: vanilla 4G + modded 8G + Palworld 16G = doesn't fit. Resolution:

```
pct set 112 -memory 24576 -swap 4096
```

No CT recreate, no recompose. If proxmoxmain itself can't spare 24+ GB at that point, revisit the proxmoxnode placement rule with explicit user approval.

## Pre-flight Checks

These run as the first step of the implementation plan. All must pass before CT creation.

| # | Check | Command | Pass criteria |
|---|-------|---------|---------------|
| 1 | RAM headroom on proxmoxmain | `ssh proxmoxmain 'free -h'` | ≥ 18 GB available |
| 2 | local-lvm free | `ssh proxmoxmain 'pvesm status'` | ≥ 40 GB free (current state shows ~308 GB) |
| 3 | mergerfs free | `ssh proxmoxmain 'df -h /mnt/cloud'` | ≥ 50 GB free (current state shows ~1.6 TB) |
| 4 | VMID 112 unused | `ssh proxmoxmain 'pct list \| awk "{print \$1}" \| grep -w 112 \|\| echo free'` | outputs `free` |
| 5 | IP 192.168.3.14 unused | `ping -c1 -W1 192.168.3.14` + cross-check Pi-hole DHCP leases | no response |
| 6 | ct-backup source list covers `/opt/stacks/ct-games` | `ssh ct-backup cat /etc/backup-dispatch/sources.d/ct-games.list` (or the equivalent config file) | file exists and lists the path — otherwise add as a plan step |
| 7 | Playit.gg secrets captured | manual | 2 tunnels created, secrets pasted into `.env` |
| 8 | Bulk storage dir exists | `ssh proxmoxmain 'ls -ld /mnt/cloud/volumes/games/archives'` | directory exists (create if not: `mkdir -p` with correct ownership matching the other `/mnt/cloud/volumes/*` dirs) |

## Open Decisions Before First Deploy

These don't block spec approval but must be resolved before the stack is brought up the first time:

1. **Modded loader & modpack source** — Forge vs. Fabric, and which modpack (Modrinth / CurseForge / FTB / manual). Drives `TYPE` and modpack env in `mc-modded`.
2. **Vanilla flavor** — `TYPE=PAPER` (recommended; plugin-friendly, better perf) vs. `TYPE=VANILLA` (strict parity). Default: Paper.
3. **Server identity** — world seed(s), MOTD, difficulty, OPs list, whitelist. All env-driven, changeable anytime.
4. **Playit.gg public hostnames** — the free-tier subdomains to share with friends.

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| proxmoxmain RAM pressure: current allocations sum to ~24 GB out of 32 GB physical; ct-games adds 16 GB allocation on top. Peak actual usage estimated 24–29 GB, 3–8 GB buffer. | Pre-flight check #1 gates creation. Strict JVM `-Xmx` caps prevent runaway growth. 4 GB swap on ct-games as safety net. If pressure shows post-launch: reduce modded heap, drop PAUSE_IF_NO_PLAYERS idling, or reopen proxmoxnode placement discussion. |
| Jellyfin transcode + MC tick contending on the same core (single-thread-sensitive). | If observed: pin MC containers to a CPU core subset via `cpuset` in compose. Not applied pre-emptively. |
| Modded TPS lag despite NVMe live storage (happens with very heavy modpacks / high player counts). | Monitor via Gatus + in-game `/tps`. If chronic: lower view-distance, reduce simulation-distance, or look at async chunk-writer plugins. Architecturally, the three-tier storage already prioritizes this; further optimizations are at the MC config layer. |
| Playit.gg free tier changes or goes dark. | Architecture permits drop-in swap — any outbound-only tunneling agent (ngrok TCP, reverse WireGuard to a free-tier VPS, Tailscale with Funnel replacement if/when they add raw TCP) can replace the Playit agent containers with no other changes. |
| Backup sidecar writes to mergerfs during gameplay causing IO stall. | `PAUSE_IF_NO_PLAYERS=true` skips the backup when the server is empty. `INITIAL_DELAY=30m` avoids startup thrash. 24h interval at off-hours minimizes collision. |

## Acceptance Criteria

The stack is done when all of the following are true:

1. `pct list` on proxmoxmain shows VMID 112 `ct-games` running.
2. `docker compose ps` on ct-games shows six services healthy: `mc-vanilla`, `mc-modded`, `playit-vanilla`, `playit-modded`, `backup-vanilla`, `backup-modded`.
3. Pasting the Playit hostname into a vanilla Minecraft client from outside the LAN successfully joins the vanilla server. Same for modded.
4. From blvckmain, `nc 192.168.3.14 25565` connects; `mcrcon -H 192.168.3.14 -P 25575 -p "$PWD" list` returns a player list.
5. After 24h + `INITIAL_DELAY`, `ls /mnt/cloud/volumes/games/archives/vanilla/` on proxmoxmain shows at least one `world-*.tgz`. Same for modded.
6. Gatus status page shows `mc-vanilla` and `mc-modded` green under the `important` tier.
7. `docker stop mc-vanilla` on ct-games (container kept stopped for ~3 min) triggers a Telegram alert via Gatus; subsequent `docker start mc-vanilla` clears the alert with a recovery notification.
8. `infra ls` and `infra status` show the new services without any code change to the CLI.
9. The next ct-backup run (nightly) captures `/opt/stacks/ct-games/data/` into the B2 repository.

## Implementation Order (for the subsequent plan)

1. Pre-flight checks (all eight).
2. Create directory structure on proxmoxmain (`/mnt/cloud/volumes/games/archives/{vanilla,modded}`).
3. Create ct-games LXC (VMID 112) with the spec above; configure bind mount.
4. Bootstrap Docker inside ct-games (standard Docker-in-LXC setup; identical to ct-media / ct-photos).
5. Scaffold `stacks/ct-games/docker-compose.yml` + `.env.example` in the repo.
6. Populate `.env` on ct-games with generated RCON passwords + captured Playit secrets + optional CF API key.
7. `infra deploy ct-games`. Verify all six services reach healthy state.
8. Verify LAN connectivity, external (Playit) connectivity, RCON from blvckmain.
9. Add Gatus checks to ct-mgmt config; redeploy ct-mgmt.
10. Add Pi-hole hostname entries (optional).
11. Verify ct-backup source list covers `/opt/stacks/ct-games`; add if missing.
12. Wait 24h + `INITIAL_DELAY` (or force-run a backup for early verification); confirm archives land in mergerfs.
13. Document any deviations from this spec inline in the implementation plan; commit final stack and spec.
