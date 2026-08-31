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
install -d -m 755 /etc/sysctl.d
if ! grep -qs 'vm.max_map_count' /etc/sysctl.d/99-ct-dev.conf 2>/dev/null; then
  log "setting vm.max_map_count"
  echo 'vm.max_map_count=262144' > /etc/sysctl.d/99-ct-dev.conf
fi
sysctl -p /etc/sysctl.d/99-ct-dev.conf >/dev/null
