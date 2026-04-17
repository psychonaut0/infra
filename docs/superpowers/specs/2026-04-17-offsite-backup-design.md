# Off-site Backup Design

**Status:** Spec — 2026-04-17
**Goal:** Full disaster recovery of the infrastructure (excluding expendable media + NVR recordings + samba family data + HAOS) to an off-site target, restorable to a running environment in one weekend max.

## Requirements

Recoverable after total proxmoxmain loss, with no per-service reconfiguration:

- LXC CT structure (Proxmox definitions)
- All container compose configurations
- Portainer state (accounts, endpoints, settings)
- Pi-hole state (admin password, custom DNS entries, blocklists, query history)
- Caddy state (TLS certificates, auto-HTTPS state)
- Jellyfin state (accounts, watch history, library definitions — **not** movie/series files)
- Immich full (photo library, Postgres DB, cache, ML state)
- *arr state — Sonarr, Radarr, Prowlarr, Deluge, FlareSolverr (configs, SQLite DBs, indexer pairings, history)
- Cloudflare Tunnel config + credentials
- Samba config + `samba/data/psy` (personal files, 66 GB)
- Frigate state (camera definitions, zones, MQTT config — **not** recordings)
- Host-level config on both Proxmox nodes (`/etc/fstab`, `/etc/systemd/system/`, `/root/.ssh/`, network)
- Home Assistant — **deferred**: included after HAOS→Container migration (separate project). Not in initial scope.

Expendable (not backed up):
- Jellyfin movie/series media (re-acquirable via *arr)
- Frigate camera recordings
- `samba/data/family/*` (except `psy/`)
- HAOS (transitional — will be replaced)

## Architecture

Three-layer separation:

```
┌────────────────────────── git (this repo) ────────────────────┐
│  • stacks/ct-*/docker-compose.yml                              │
│  • stacks/ct-mgmt/Caddyfile, gatus/config.yaml, dashboard/     │
│  • CLAUDE.md, docs/hardware.md, docs/recovery.md (new)         │
│  • scripts/bootstrap-ct.sh (new)                               │
│                    ↓ pushed to GitHub (origin)                 │
└────────────────────────────────────────────────────────────────┘

┌─────────────────── restic → Backblaze B2 ─────────────────────┐
│  SECRETS:                                                       │
│  • stacks/ct-*/.env and nested .env files                       │
│                                                                 │
│  STATE:                                                         │
│  • Docker named volumes (portainer-data, pihole-data,           │
│    pihole-dns, caddy-data, caddy-config, filebrowser-db, etc.)  │
│  • Bind-mounted data (Immich full, samba/psy + config,          │
│    *arr configs, Jellyfin config, Frigate config)               │
│  • Immich Postgres dump (pg_dump)                               │
│  • /etc/pve/ from both nodes                                    │
│  • Host config: fstab, systemd/system, root/.ssh, network       │
└─────────────────────────────────────────────────────────────────┘
```

**Git = structure. Restic = secrets + state.** No third place to look.

### Components

**ct-backup** — a new unprivileged LXC on proxmoxmain

- IP: 192.168.3.13
- Resources: 1 vCPU, 512 MB RAM, 4 GB disk
- OS: Debian 13 minimal
- Installed: restic, rsync, ssh, cron, caddy (for the status endpoint)
- No Docker, no public exposure

**Bind mounts (read-only from host):**

```
/mnt/cloud/volumes/mediaserver/immich               → /backup-sources/immich
/mnt/cloud/volumes/samba/data/psy                   → /backup-sources/samba-psy
/mnt/cloud/volumes/samba/config                     → /backup-sources/samba-config
/mnt/cloud/volumes/mediaserver/jellyfin/config      → /backup-sources/jellyfin-config
/mnt/cloud/volumes/mediaserver/sonarr/config        → /backup-sources/arr-configs/sonarr
/mnt/cloud/volumes/mediaserver/radarr/config        → /backup-sources/arr-configs/radarr
/mnt/cloud/volumes/mediaserver/prowlarr/config      → /backup-sources/arr-configs/prowlarr
/mnt/cloud/volumes/mediaserver/deluge/config        → /backup-sources/arr-configs/deluge
/mnt/cloud/volumes/mediaserver/flaresolverr/config  → /backup-sources/arr-configs/flaresolverr
/mnt/nvr-data/config                                → /backup-sources/frigate-config
```

**CT-local scratch (populated each run, wiped beforehand):**

```
/var/backup-staging/
├── env/<ct-name>/.env                                # gitignored secrets
├── volumes/<ct-name>/<volname>.tar.gz                # Docker named volumes
├── immich-postgres.sql.gz                            # pg_dump of Immich DB
├── pve-main/                                         # /etc/pve from proxmoxmain
├── pve-node/                                         # /etc/pve from proxmoxnode
├── host-cfg-main/                                    # fstab, systemd, SSH, network
└── host-cfg-node/                                    # same for proxmoxnode
```

**Secrets and keys:**

```
/etc/restic/password          # repo password (600 root)
/etc/restic/b2.env            # AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (B2 S3 creds)
/root/.ssh/id_ed25519         # ct-backup's SSH key; public half in each target's authorized_keys
```

## Tool and Target

- **Tool:** restic
- **Target:** Backblaze B2 via **S3-compatible endpoint** (portable — migration to R2, iDrive e2, or self-hosted MinIO requires only env-var changes)
- **Encryption:** client-side AES-256 (restic). Provider sees only ciphertext.
- **Repository URL:**
  ```
  s3:s3.<region>.backblazeb2.com/<bucket>/backup
  ```

## Nightly Flow

Single cron job on ct-backup, `03:00` daily:

```
┌───────────────────── PRE-BACKUP ─────────────────────┐
│ 1. Wipe /var/backup-staging                           │
│                                                       │
│ 2. Pull .env files from each CT:                      │
│    for each CT: rsync .env files → staging/env/       │
│                                                       │
│ 3. Export Docker named volumes:                       │
│    for each CT, for each volume:                      │
│      ssh CT docker run --rm -v <vol>:/src:ro alpine   │
│        tar czf - -C /src . > staging/volumes/...      │
│                                                       │
│ 4. Dump Immich Postgres (via ct-photos):              │
│    ssh ct-photos docker exec immich-postgres          │
│      pg_dump -U postgres immich | gzip                │
│      > staging/immich-postgres.sql.gz                 │
│                                                       │
│ 5. Rsync /etc/pve/ from both nodes                    │
│                                                       │
│ 6. Rsync host config from both nodes                  │
│    (/etc/fstab, /etc/systemd/system/, /root/.ssh/,    │
│     /etc/network/)                                    │
└───────────────────────────────────────────────────────┘

┌───────────────────── RESTIC BACKUP ──────────────────┐
│ restic backup                                         │
│   /backup-sources/                                    │
│   /var/backup-staging/                                │
│   --tag nightly                                       │
│                                                       │
│ → AES-256 client-side encryption                      │
│ → Content-addressed chunking + dedup                  │
│ → Stream ciphertext to B2 bucket                      │
└───────────────────────────────────────────────────────┘

┌─────────────────────── NOTIFY ───────────────────────┐
│ On success:                                           │
│   echo {"timestamp":"...","duration_sec":...}         │
│     > /var/lib/backup-status/status.json              │
│                                                       │
│ On failure (via trap ERR in cron wrapper):            │
│   curl -s Telegram API with exit code + stderr tail   │
└───────────────────────────────────────────────────────┘
```

### Weekly prune (Sunday 04:00)

```
restic forget \
  --keep-daily 7 \
  --keep-weekly 4 \
  --keep-monthly 12 \
  --keep-yearly 2 \
  --prune
```

### Monthly integrity check (1st of the month, 05:00)

```
restic check --read-data-subset 5%
```

Full `restic check --read-data` annually.

## SSH Trust Model

ct-backup has one ed25519 keypair. Public half deployed to:

- Both Proxmox hosts: for rsync of `/etc/pve/` and host config
- Each of the 7 CTs: for rsync of `.env` files + Docker volume export + Immich DB dump

Where possible, `authorized_keys` entries use forced-command restrictions:

```
# Example: ct-photos authorized_keys entry for Immich DB dump
command="docker exec immich-postgres pg_dump -U postgres immich | gzip",no-pty,no-port-forwarding,no-X11-forwarding,no-agent-forwarding ssh-ed25519 AAAAC3... ct-backup
```

For rsync pulls, use `rrsync --ro <path>` to enforce read-only access to a specific directory.

## Monitoring

Two-channel alerting (belt-and-suspenders):

**Immediate failure notification** — cron wrapper includes:

```bash
trap 'curl -s -X POST \
  -d chat_id=$TELEGRAM_CHAT_ID \
  -d "text=🚨 backup failed (exit $?): $(tail -20 /var/log/backup.log)" \
  https://api.telegram.org/bot$TELEGRAM_TOKEN/sendMessage' ERR
```

Reuses the existing `@blvckhomelab_bot` from Gatus.

**Dead-man / staleness detection** — Gatus endpoint polls ct-backup's tiny HTTP server:

```
# ct-backup runs: caddy file-server --root /var/lib/backup-status --listen :80
# Gatus config adds endpoint:

- name: Backup freshness
  url: http://192.168.3.13/status.json
  interval: 15m
  conditions:
    - '[STATUS] == 200'
    - '[BODY].timestamp > now - 36h'
  alerts:
    - type: telegram
      failure-threshold: 2
```

Catches the case where the cron didn't even start.

## Secret Handling & Disaster Recovery Keys

Three pieces must survive total proxmoxmain loss:

- Restic repository password
- B2 Access Key ID
- B2 Secret Access Key

Stored in **two offline locations**:

1. **1Password / Bitwarden vault** — primary
2. **Printed paper in a fireproof location** — fallback if password manager is also compromised

**Not stored in git, not stored anywhere on proxmoxmain.**

## Recovery Flow (total proxmoxmain loss)

| # | Action | Time |
|---|---|---|
| 1 | Install Proxmox VE on replacement hardware | 30m |
| 2 | Install Tailscale, enroll node; install restic | 15m |
| 3 | `restic -r s3:... restore latest --target /recovery` (needs repo password + B2 creds from offline store) | 2–3h (download-bound) |
| 4 | Reformat HDDs, recreate mergerfs pool + nvr-data LVM thin per `docs/recovery.md` | 30m |
| 5 | Restore host config: `/etc/fstab`, `/etc/systemd/system/*.service`, `/root/.ssh/`, `/etc/network/interfaces` | 15m |
| 6 | Restore `/etc/pve/` on both nodes (CT/VM definitions as files) | 5m |
| 7 | Restore bind-mounted data to `/mnt/cloud/` and `/mnt/nvr-data/config/` | 2–3h |
| 8 | `git clone git@github.com:psychonaut0/infra.git /root/infra` | 1m |
| 9 | Restore `.env` files from recovery staging into `/root/infra/stacks/*/` | 5m |
| 10 | For each CT: `./scripts/bootstrap-ct.sh <ct-name>` | ~5m × 7 ≈ 35m |
|   | (Script creates CT, installs Docker + portainer-agent, copies stack + .env, restores named volumes, `docker compose up -d`) |
| 11 | Restore Immich Postgres dump: `psql -U postgres immich < immich-postgres.sql.gz` | 10m |
| 12 | Verify every service boots, logs in, shows expected state | 30m |
| **Total hands-on** | | **~5–7 hours** |

Background: *arr re-acquires media library over days. No user action required.

## What this design does NOT protect

Documented here for clarity on residual risk:

- **Jellyfin media library** (movie/series files) — ~2 TB, re-downloadable via *arr
- **Frigate camera recordings** — 200 days of footage, expendable
- **`samba/data/family/`** (except `psy/`) — excluded by user preference
- **HAOS VM state** — excluded until Container migration completes
- **Anything custom inside a CT's OS outside Docker** — e.g., manually-installed packages, custom systemd units, ad-hoc `/etc/` tweaks. Expected to be minimal given the uniform CT pattern (Debian 13 + Docker + portainer-agent); audit before first backup run to confirm. **Audit performed 2026-04-17 — no non-Docker state of concern found on any CT. Only cosmetic differences: `rsync` manually installed on ct-mgmt; standard Debian-created dirs in /root/.**
- **Docker images themselves** — re-pulled from registries on restore. No offline image cache.

## Components to Build

Tracked separately in the implementation plan, but summarized:

1. **ct-backup LXC** — new container on proxmoxmain
2. **Backup orchestration script** — lives in `stacks/ct-backup/` or similar, containing:
   - Pre-backup staging orchestrator (rsync, volume exports, DB dumps)
   - restic invocation with correct scope
   - Retention and integrity job definitions (systemd timers or cron)
   - Telegram failure trap + Gatus status.json writer
3. **`scripts/bootstrap-ct.sh`** — idempotent CT provisioner used both for initial setup consistency and for recovery
4. **`docs/recovery.md`** — hypervisor-level DR runbook (disk format, mergerfs, systemd units, Tailscale, `pct restore` order)
5. **Gatus endpoint addition** — new entry in `stacks/ct-mgmt/gatus/config.yaml` for backup freshness
6. **Audit task** — per-CT check for non-Docker state before first backup run, documented in the implementation plan
7. **Verification before rollout** — at least one full `restic restore` drill to a scratch location to confirm every layer round-trips correctly

## Cost Estimate

| Item | Value |
|---|---|
| First-run restic upload size | ~125 GB |
| Steady-state storage at B2 (after dedup, 7d/4w/12m/2y retention) | ~150–200 GB |
| Backblaze B2 pricing | $6/TB-month |
| **Monthly cost** | **~$1.00** |
| Free egress allowance (3× storage/month) | ~450–600 GB read/month |

Egress is effectively free for normal operation (integrity checks + occasional restore tests).

## Provider Portability

Because the repository format is provider-agnostic and we use S3-compatible transport, switching providers later requires changing only two environment variables:

```bash
# From Backblaze B2:
RESTIC_REPOSITORY=s3:s3.eu-central-003.backblazeb2.com/my-bucket/backup

# To Cloudflare R2:
RESTIC_REPOSITORY=s3:https://<account>.r2.cloudflarestorage.com/my-bucket/backup

# To self-hosted MinIO:
RESTIC_REPOSITORY=s3:https://minio.local:9000/my-bucket/backup
```

To preserve snapshot history during migration: `restic copy --from-repo <old> <new>`.

## Follow-up Work (out of scope for this spec)

- **HA migration** (HAOS → container) — separate project; HA data joins backup scope afterwards
- **Unified infra CLI** — `infra backup [service|all]` and `infra restore <service>` commands that wrap this restic setup for manual operations. Tracked in memory `project_infra_cli_idea.md`. Built after this spec is implemented.
- **Mergerfs RAID/replication** — out of scope; different concern (uptime vs off-site DR)
- **Proxmox Backup Server** — deliberately excluded after evaluation; full-restic aligns better with the "structure-in-git, state-in-restic" model and the fact that recovery onto new hardware will resize CT resources anyway
