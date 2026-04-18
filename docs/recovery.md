# Disaster Recovery Runbook

Target: rebuild the homelab on replacement hardware in ≤1 weekend, restoring every "preserve" item from the backup design (photos, personal files, all CT configs, HA + Mosquitto, ESPHome devices, *arr configs, Pi-hole, Portainer, Caddy, Frigate config, Proxmox cluster config, host config). Acceptable losses: Jellyfin media library, Frigate recordings, samba/data/family/*.

Reference: `docs/superpowers/specs/2026-04-17-offsite-backup-design.md`.

## Prerequisites

Have on hand:

- Password manager access containing restic repo password + B2 credentials
- Paper copy of the same (in case password manager is unavailable)
- Network access to Backblaze B2 (`s3.eu-central-003.backblazeb2.com`)
- GitHub access to `psychonaut0/infra` (or a local clone)
- Tailscale auth credentials
- Physical replacement hardware

## Phase 1 — Hypervisor install

1. Install Proxmox VE 9.x on replacement hardware using the same hostname (`proxmoxmain` or `proxmoxnode`).
2. If the other node survived, join the cluster: `pvecm add <surviving-node-ip>`.
3. Install Tailscale and enroll. Advertise subnet routes if needed.
4. Install recovery tools:
   ```bash
   apt-get install -y restic rsync jq curl
   ```

## Phase 2 — Recover from B2 to a scratch location

Set restic environment from the vault entry:

```bash
export AWS_ACCESS_KEY_ID=<from vault>
export AWS_SECRET_ACCESS_KEY=<from vault>
export RESTIC_REPOSITORY=s3:s3.eu-central-003.backblazeb2.com/blvck-homelab-backup/backup
export RESTIC_PASSWORD_FILE=/tmp/resticpass
echo -n '<password from vault>' > $RESTIC_PASSWORD_FILE
chmod 600 $RESTIC_PASSWORD_FILE

# Verify repo reachable
restic snapshots

# Pull the latest snapshot to /recovery
mkdir -p /recovery
restic restore latest --target /recovery
```

Expected download: ~130 GB. Plan for 2–3 hours over a typical home uplink.

Inside `/recovery` you will find two trees mirroring what was backed up:
- `/recovery/backup-sources/` — bind-mounted bulk data (Immich, samba/psy, *arr configs, Jellyfin config, Frigate config)
- `/recovery/var/backup-staging/` — everything else:
  - `env/<ct-name>/` — gitignored `.env` files per stack
  - `stacks/ct-ha/`, `stacks/ct-tools/` — full stack trees (HA config, Mosquitto, ESPHome)
  - `volumes/<ct-name>/<vol>.tar.gz` — Docker named-volume exports
  - `immich-postgres.sql.gz` — Immich DB dump
  - `pve-main/`, `pve-node/` — `/etc/pve/` from each node
  - `host-cfg-main/`, `host-cfg-node/` — host-level config (fstab, systemd, SSH)

## Phase 3 — Recreate bulk storage on proxmoxmain

Per `docs/hardware.md`:

1. Partition the new HDDs:
   - `sda` (4 TB) → single ext4 partition → `/mnt/cloud-2`
   - `sdb` (1 TB) → single ext4 partition → `/mnt/cloud-1`
   - `sdc` (456 GB) → LVM physical volume → VG `nvr-data` → thin pool `nvr-data` → thin LV (400 GB, ext4) → `/mnt/nvr-data` via kpartx
2. Install mergerfs:
   ```bash
   apt-get install -y mergerfs
   ```
3. Restore `/etc/fstab` from backup:
   ```bash
   cp /recovery/var/backup-staging/host-cfg-main/fstab /etc/fstab
   mount -a
   ```
4. Restore the nvr-data systemd unit:
   ```bash
   cp /recovery/var/backup-staging/host-cfg-main/systemd-system/mnt-nvr-data.service \
     /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable --now mnt-nvr-data.service
   ```
5. Verify `/mnt/cloud` shows ~4.5 TB and `/mnt/nvr-data` is mounted.

## Phase 4 — Restore host-level config

```bash
# /etc/hosts + /etc/network/interfaces (if applicable)
cp /recovery/var/backup-staging/host-cfg-main/hosts /etc/hosts

# SSH config + keys
cp -r /recovery/var/backup-staging/host-cfg-main/root-ssh/. /root/.ssh/
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys /root/.ssh/id_*

# Root crontab
crontab /recovery/var/backup-staging/host-cfg-main/root-crontab

# Any custom systemd units
cp /recovery/var/backup-staging/host-cfg-main/systemd-system/*.service \
   /recovery/var/backup-staging/host-cfg-main/systemd-system/*.timer \
   /etc/systemd/system/
systemctl daemon-reload
```

Repeat Phase 4 on proxmoxnode using `host-cfg-node/` instead of `host-cfg-main/`.

## Phase 5 — Restore Proxmox cluster config

`/etc/pve` is a FUSE filesystem backed by pmxcfs. Restore the per-guest files:

```bash
# CT definitions
cp /recovery/var/backup-staging/pve-main/lxc/*.conf /etc/pve/lxc/

# Storage.cfg, datacenter.cfg — validate against `docs/hardware.md` and merge
# manually if the restored file has pool definitions that don't exist yet.
diff /recovery/var/backup-staging/pve-main/storage.cfg /etc/pve/storage.cfg
```

Do not blindly copy `/etc/pve/storage.cfg` — storage pools must physically exist. Recreate pools missing from the fresh install (typically you only need `local`, `local-lvm`, `nvr-data`, and the `cloud` dir pool pointing at `/mnt/cloud`).

## Phase 6 — Restore bulk bind-mount data

```bash
# Immich full
rsync -a /recovery/backup-sources/immich/ /mnt/cloud/volumes/mediaserver/immich/

# Samba personal
rsync -a /recovery/backup-sources/samba-psy/    /mnt/cloud/volumes/samba/data/psy/
rsync -a /recovery/backup-sources/samba-config/ /mnt/cloud/volumes/samba/config/

# *arr configs
for ARR in sonarr radarr prowlarr deluge; do
  rsync -a "/recovery/backup-sources/arr-configs/$ARR/" \
           "/mnt/cloud/volumes/mediaserver/$ARR/config/"
done

# Jellyfin config (NOT media files — those re-download via *arr)
rsync -a /recovery/backup-sources/jellyfin-config/ /mnt/cloud/volumes/mediaserver/jellyfin/config/

# Frigate config (NOT recordings)
rsync -a /recovery/backup-sources/frigate-config/ /mnt/nvr-data/config/
```

## Phase 7 — Clone the infra repo

```bash
cd /root
git clone git@github.com:psychonaut0/infra.git
cd infra
```

Restore `.env` files (gitignored, so not in the repo):

```bash
for CT in ct-dns ct-tunnel ct-nvr ct-media ct-photos ct-files ct-mgmt ct-ha ct-tools; do
  if [[ -d /recovery/var/backup-staging/env/$CT ]]; then
    cp -r /recovery/var/backup-staging/env/$CT/$CT/* stacks/$CT/ 2>/dev/null || true
  fi
done
```

For ct-ha and ct-tools, restore the full stack state (HA config, Mosquitto, ESPHome) over the repo's compose-only content:

```bash
rsync -a /recovery/var/backup-staging/stacks/ct-ha/   stacks/ct-ha/
rsync -a /recovery/var/backup-staging/stacks/ct-tools/ stacks/ct-tools/
```

## Phase 8 — Bootstrap every CT

Order matters: DNS first (so other CTs resolve names), then ct-mgmt (Portainer UI, Caddy), then the rest.

```bash
cd /root/infra
VOLDIR=/recovery/var/backup-staging/volumes

./scripts/bootstrap-ct.sh ct-dns     --restore-volumes-from $VOLDIR
./scripts/bootstrap-ct.sh ct-mgmt    --restore-volumes-from $VOLDIR
./scripts/bootstrap-ct.sh ct-tunnel  --restore-volumes-from $VOLDIR
./scripts/bootstrap-ct.sh ct-files   --restore-volumes-from $VOLDIR
./scripts/bootstrap-ct.sh ct-media
./scripts/bootstrap-ct.sh ct-photos
./scripts/bootstrap-ct.sh ct-nvr
./scripts/bootstrap-ct.sh ct-ha
./scripts/bootstrap-ct.sh ct-tools
```

## Phase 9 — Restore Immich Postgres dump

Only after ct-photos is up and `immich_postgres` is running:

```bash
gunzip -c /recovery/var/backup-staging/immich-postgres.sql.gz \
  | pct exec 106 -- docker exec -i immich_postgres psql -U postgres immich
```

Note: the pgvecto-rs extension is restored as part of the dump. If the dump fails on the `CREATE EXTENSION vectors` line, verify the `immich_postgres` image version matches what was running pre-disaster (`tensorchord/pgvecto-rs:pg14-v0.2.0` per the compose).

## Phase 10 — Recreate ct-backup itself

ct-backup's own state isn't included in the ct-backup snapshot (it would be circular). Rebuild manually:

1. Create the CT per `docs/hardware.md` / the spec: privileged, 192.168.3.13, 1 vCPU / 512 MB / 4 GB, with the same bind mounts as documented.
2. Install tools: `apt-get install -y restic rsync cron caddy curl jq postgresql-client`.
3. Restore `/etc/restic/` from the password vault (paste password + B2 creds + Telegram creds into `/etc/restic/{password,b2.env,telegram.env}`).
4. Regenerate the SSH key or reuse if you have it: `ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N ""`.
5. Deploy the dispatcher key to every target (see Task 6 in the implementation plan).
6. Deploy scripts + systemd units from the restored repo:
   ```bash
   scp /root/infra/stacks/ct-backup/scripts/*.sh root@ct-backup:/usr/local/bin/
   scp /root/infra/stacks/ct-backup/systemd/*    root@ct-backup:/etc/systemd/system/
   scp /root/infra/stacks/ct-backup/Caddyfile    root@ct-backup:/etc/caddy/Caddyfile
   scp /root/infra/stacks/ct-backup/systemd/caddy-lxc-override.conf \
       root@ct-backup:/etc/systemd/system/caddy.service.d/lxc-override.conf
   ```
7. Reload and enable:
   ```bash
   ssh root@ct-backup 'systemctl daemon-reload && \
     systemctl enable --now caddy backup.timer backup-prune.timer backup-check.timer'
   ```

## Phase 11 — Verification

1. Open `https://status.lan` — every Gatus endpoint should recover to green over the first 5 minutes.
2. Open `https://immich.lan` — your photos are visible.
3. Open `https://homeassistant.lan` — accounts, automations, integrations present. (Z-Wave/Zigbee sticks need re-pairing only if the physical stick changed.)
4. Open `https://jellyfin.lan` — accounts and library definitions present; media folders empty (*arr will re-acquire).
5. Open `https://sonarr.lan`, `https://radarr.lan`, `https://prowlarr.lan` — indexers paired, quality profiles intact.
6. Open `https://pihole.lan` — admin password works, custom DNS list present, blocklists subscribed.
7. Open `http://backup.lan/status.json` — Caddy responding with a `timestamp`.
8. Trigger a manual backup to verify the new ct-backup can reach B2 end-to-end:
   ```bash
   ssh root@ct-backup /usr/local/bin/backup.sh
   ```

## Phase 12 — Restart background media re-acquisition

Once *arr is up and online, it will automatically start downloading movies and series to replenish the empty Jellyfin library. This happens in the background over days to weeks depending on indexer response.

## Estimated timing

| Phase | Action | Time |
|---|---|---|
| 1 | Proxmox install + cluster join + Tailscale | 30m |
| 2 | restic restore from B2 | 2–3h (network-bound) |
| 3 | Recreate bulk storage | 1h |
| 4 | Host config restore | 15m × 2 nodes = 30m |
| 5 | PVE cluster config | 15m |
| 6 | Bulk data restore | 2–3h |
| 7 | Clone repo + env restore | 10m |
| 8 | Bootstrap 9 CTs | ~10m × 9 = 1h 30m |
| 9 | Immich Postgres restore | 10m |
| 10 | Recreate ct-backup | 30m |
| 11 | Verification | 30m |
| **Total hands-on** | | **~7–9 hours** |

Background: Jellyfin media re-acquisition runs for days with no user action.
