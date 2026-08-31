#!/bin/bash
# Provisions ct-dev (VMID 116) on proxmoxmain. Idempotent: safe to re-run.
# Usage: run ON proxmoxmain as root.
set -euo pipefail

VMID=116
HOSTNAME=ct-dev
IP=192.168.3.19
GW=192.168.3.1
DNS=192.168.3.5

log() { echo "[ct-dev] $*" >&2; }

# --- 1. Host sysctl -------------------------------------------------------
# The work monorepo's compose stack runs OpenSearch, which requires
# vm.max_map_count >= 262144. This sysctl is NOT namespaced, so it cannot be
# set from inside the container -- it must live on the hypervisor.
# Floor-only: only write /etc/sysctl.d/99-ct-dev.conf if the effective value
# is below the floor. Never lower the system default.
current=$(sysctl -n vm.max_map_count)
if [[ $current -lt 262144 ]]; then
  log "vm.max_map_count is $current, below floor 262144; setting to 262144"
  install -d -m 755 /etc/sysctl.d
  echo 'vm.max_map_count=262144' > /etc/sysctl.d/99-ct-dev.conf
  sysctl -p /etc/sysctl.d/99-ct-dev.conf >/dev/null
else
  log "vm.max_map_count is $current, meets floor 262144 (system or distro default)"
  rm -f /etc/sysctl.d/99-ct-dev.conf
fi
# Assert the floor is met
final=$(sysctl -n vm.max_map_count)
if [[ $final -lt 262144 ]]; then
  log "FAIL: effective vm.max_map_count=$final is below floor 262144"
  exit 1
fi
