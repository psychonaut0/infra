#!/bin/bash
# Runs on ct-backup. Populates /var/backup-staging/ with all non-bind-mount
# data before restic ships everything off-site:
#   - .env files from each CT's /opt/stacks/
#   - Docker named volume exports from each CT
#   - Immich Postgres dump from ct-photos
#   - /etc/pve from both Proxmox nodes
#   - Host-level config from both Proxmox nodes (via their host-export.sh)
set -euo pipefail

STAGING=/var/backup-staging
LOGDIR=/var/log/backup
install -d -m 755 "$LOGDIR"
LOG="$LOGDIR/pre-backup.log"
SSHKEY=/root/.ssh/id_ed25519

# Target IPs
declare -A CT_IPS=(
  [ct-dns]=192.168.3.5
  [ct-tunnel]=192.168.3.6
  [ct-nvr]=192.168.3.7
  [ct-media]=192.168.3.8
  [ct-photos]=192.168.3.9
  [ct-files]=192.168.3.11
  [ct-mgmt]=192.168.3.12
  [ct-games]=192.168.3.14
  [ct-ha]=192.168.3.10
  [ct-tools]=192.168.3.15
)
# CTs whose full /opt/stacks state must be captured (not just .env). Both CTs
# keep their service data (HA config, Mosquitto passwd/data, ESPHome per-device
# keys) inside their /opt/stacks subdir — bind-mounted into the Docker
# containers — so the full tree is what matters.
FULL_STACK_CTS=(ct-ha ct-tools ct-games)
PROXMOXMAIN_IP=192.168.3.2
PROXMOXNODE_IP=192.168.3.3

SSH_OPTS=(-i "$SSHKEY" -o StrictHostKeyChecking=accept-new -o BatchMode=yes)
RSYNC_E="ssh $(printf '%q ' "${SSH_OPTS[@]}")"

log() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG" >&2; }

# Fresh staging
rm -rf "$STAGING"
install -d -m 700 "$STAGING"/{env,volumes,stacks,pve-main,pve-node,host-cfg-main,host-cfg-node}

# --- 1. Rsync .env files from each CT's /opt/stacks ---
# The rrsync restriction limits us to /opt/stacks. We pull only .env files
# (at any depth) so secrets land in staging but compose files / other content
# are skipped (they live in the git repo).
for CT in "${!CT_IPS[@]}"; do
  IP="${CT_IPS[$CT]}"
  log "Pulling .env files from $CT ($IP)"
  install -d "$STAGING/env/$CT"
  rsync -a -e "$RSYNC_E" \
    --include='*/' --include='.env' --include='**/.env' --exclude='*' \
    "root@$IP:/opt/stacks/" "$STAGING/env/$CT/" \
    2>>"$LOG" || log "WARN: .env sync failed for $CT"
done

# --- 1b. Full /opt/stacks tree for CTs that hold state outside Docker volumes ---
# ct-ha and ct-tools bind-mount their service state (HA config, Mosquitto,
# ESPHome per-device keys) directly from /opt/stacks subdirs into containers,
# so a full rsync is the only way to capture it.
for CT in "${FULL_STACK_CTS[@]}"; do
  IP="${CT_IPS[$CT]}"
  log "Pulling full /opt/stacks/$CT from $CT ($IP)"
  install -d "$STAGING/stacks/$CT"
  rsync -a -e "$RSYNC_E" \
    "root@$IP:/opt/stacks/$CT/" "$STAGING/stacks/$CT/" \
    2>>"$LOG" || log "WARN: full stacks sync failed for $CT"
done

# --- 2. Export Docker named volumes from CTs where the dispatcher allows it ---
# CTs with ALLOW_EXPORT_VOLUMES=1: ct-dns, ct-tunnel, ct-mgmt, ct-files.
# Skip Docker-anonymous volumes (64-char hex names) — those are transient
# container-internal state whose durable contents are captured elsewhere
# via bind mounts (e.g. Samba config is in samba/config bind mount).
for CT in ct-dns ct-tunnel ct-mgmt ct-files; do
  IP="${CT_IPS[$CT]}"
  log "Listing volumes on $CT"
  install -d "$STAGING/volumes/$CT"
  VOLS=$(ssh "${SSH_OPTS[@]}" "root@$IP" list-volumes 2>>"$LOG")
  for VOL in $VOLS; do
    if [[ "$VOL" =~ ^[0-9a-f]{64}$ ]]; then
      log "  Skipping anonymous volume $VOL on $CT"
      continue
    fi
    log "  Exporting volume $VOL from $CT"
    ssh "${SSH_OPTS[@]}" "root@$IP" "export-volume $VOL" \
      > "$STAGING/volumes/$CT/$VOL.tar.gz" 2>>"$LOG"
  done
done

# --- 3. Immich Postgres dump ---
log "Dumping Immich Postgres from ct-photos"
ssh "${SSH_OPTS[@]}" "root@${CT_IPS[ct-photos]}" pg-dump-immich \
  | gzip > "$STAGING/immich-postgres.sql.gz" 2>>"$LOG"

# --- 4. /etc/pve from both Proxmox hosts ---
log "Pulling /etc/pve from proxmoxmain"
rsync -a -e "$RSYNC_E" "root@$PROXMOXMAIN_IP:/etc/pve/" "$STAGING/pve-main/" 2>>"$LOG"

log "Pulling /etc/pve from proxmoxnode"
rsync -a -e "$RSYNC_E" "root@$PROXMOXNODE_IP:/etc/pve/" "$STAGING/pve-node/" 2>>"$LOG"

# --- 5. Host-level config (/var/backup-export/ on each host) ---
# Depends on host-export.sh having run via the host-side systemd timer
# scheduled ~15min before this job.
log "Pulling host config from proxmoxmain"
rsync -a -e "$RSYNC_E" "root@$PROXMOXMAIN_IP:/var/backup-export/" "$STAGING/host-cfg-main/" 2>>"$LOG"

log "Pulling host config from proxmoxnode"
rsync -a -e "$RSYNC_E" "root@$PROXMOXNODE_IP:/var/backup-export/" "$STAGING/host-cfg-node/" 2>>"$LOG"

log "Pre-backup complete. Staging size: $(du -sh "$STAGING" | cut -f1)"
