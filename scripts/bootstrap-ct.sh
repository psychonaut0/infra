#!/bin/bash
# Idempotent CT provisioner for DR recovery and consistent new-CT creation.
#
# Assumes:
#   - Run on a Proxmox hypervisor that hosts (or will host) the CT
#   - /etc/pve/lxc/<vmid>.conf exists — either live state or restored from backup
#   - This repo is checked out at $INFRA_REPO (default /root/infra) with
#     stacks/<ct-name>/ populated
#   - .env files already in place under stacks/<ct-name>/ (restored from
#     backup staging before running this script)
#   - Optional: a backup staging dir passed via --restore-volumes-from contains
#     Docker named-volume tarballs for the CT at <dir>/<ct-name>/<vol>.tar.gz
#
# Usage:
#   bootstrap-ct.sh <ct-name> [--restore-volumes-from <dir>]
#
# Examples:
#   bootstrap-ct.sh ct-dns
#   bootstrap-ct.sh ct-mgmt --restore-volumes-from /recovery/backup-staging/volumes

set -euo pipefail

CT_NAME="${1:?Usage: bootstrap-ct.sh <ct-name> [--restore-volumes-from <dir>]}"
RESTORE_DIR=""
if [[ "${2:-}" == "--restore-volumes-from" ]]; then
  RESTORE_DIR="${3:?--restore-volumes-from requires a directory}"
fi

INFRA_REPO="${INFRA_REPO:-/root/infra}"
STACK_DIR="$INFRA_REPO/stacks/$CT_NAME"
if [[ ! -d "$STACK_DIR" ]]; then
  echo "ERROR: stack dir $STACK_DIR does not exist" >&2
  exit 1
fi

# Locate VMID from the restored PVE config
VMID=$(grep -l "^hostname: $CT_NAME$" /etc/pve/lxc/*.conf 2>/dev/null | head -1 \
       | xargs -rI{} basename {} .conf)
if [[ -z "$VMID" ]]; then
  echo "ERROR: no /etc/pve/lxc/*.conf found with hostname $CT_NAME" >&2
  exit 1
fi

echo "==> Bootstrapping $CT_NAME (VMID $VMID)"

# Start CT if stopped
if ! pct status "$VMID" | grep -q running; then
  echo "  starting CT $VMID"
  pct start "$VMID"
  sleep 5
fi

# --- Install Docker + portainer-agent prereqs inside the CT ---
echo "  installing Docker engine"
pct exec "$VMID" -- bash -c '
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null; then
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $VERSION_CODENAME stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
'

echo "  applying /etc/docker/daemon.json (log caps + live-restore)"
DAEMON_JSON_SRC="$INFRA_REPO/scripts/docker-daemon.json"
[[ -r "$DAEMON_JSON_SRC" ]] || { echo "ERROR: $DAEMON_JSON_SRC missing" >&2; exit 1; }
pct push "$VMID" "$DAEMON_JSON_SRC" /etc/docker/daemon.json
pct exec "$VMID" -- systemctl enable --now docker
pct exec "$VMID" -- systemctl restart docker

# --- Copy the stack into /opt/stacks/<ct-name>/ inside the CT ---
echo "  copying stack files from $STACK_DIR"
pct exec "$VMID" -- mkdir -p /opt/stacks
tar -C "$INFRA_REPO/stacks" -cf - "$CT_NAME" | pct exec "$VMID" -- tar -C /opt/stacks -xf -

# --- Optional: restore Docker named volumes from backup staging ---
if [[ -n "$RESTORE_DIR" && -d "$RESTORE_DIR/$CT_NAME" ]]; then
  shopt -s nullglob
  for TARBALL in "$RESTORE_DIR/$CT_NAME"/*.tar.gz; do
    VOL=$(basename "$TARBALL" .tar.gz)
    echo "  restoring volume $VOL"
    pct exec "$VMID" -- docker volume create "$VOL" > /dev/null
    cat "$TARBALL" | pct exec "$VMID" -- \
      docker run --rm -i -v "$VOL:/dst" alpine tar xzf - -C /dst
  done
  shopt -u nullglob
fi

# --- Bring up the stack ---
echo "  docker compose up -d"
pct exec "$VMID" -- bash -c "cd /opt/stacks/$CT_NAME && docker compose up -d"

echo "==> $CT_NAME bootstrapped."
