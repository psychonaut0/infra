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
# We only ASSERT the floor, never enforce it: Debian 13 + systemd 257 ships
# 1048576 (/usr/lib/sysctl.d/50-default.conf) and PVE ships 262144
# (/usr/lib/sysctl.d/10-pve-ct-inotify-limits.conf), so any plausible build of
# this host already clears the floor -- the write path has never executed for
# real, yet three rounds of trying to "self-heal" it produced three real
# defects (lowered a live limit, deleted the wrong file, mis-ordered the
# precedence check). A loud failure with remediation is safer than that.
current=$(sysctl -n vm.max_map_count)
if [[ -z "$current" || ! "$current" =~ ^[0-9]+$ ]]; then
  log "FAIL: could not read a numeric vm.max_map_count (got '$current')"
  exit 1
fi
if [[ $current -lt 262144 ]]; then
  log "FAIL: vm.max_map_count=$current, need >= 262144. Fix: create" \
      "/etc/sysctl.d/99-ct-dev.conf with 'vm.max_map_count=262144', then run" \
      "'sysctl -p /etc/sysctl.d/99-ct-dev.conf'"
  exit 1
fi
log "vm.max_map_count=$current meets floor 262144"
