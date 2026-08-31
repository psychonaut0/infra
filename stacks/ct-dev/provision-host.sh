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

# --- 2. Container ---------------------------------------------------------
TEMPLATE="${TEMPLATE:-local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst}"

if ! pct status "$VMID" >/dev/null 2>&1; then
  log "creating CT $VMID"
  pct create "$VMID" "$TEMPLATE" \
    --hostname "$HOSTNAME" \
    --cores 6 --memory 12288 --swap 4096 \
    --rootfs local-lvm:120 \
    --net0 "name=eth0,bridge=vmbr0,firewall=1,gw=${GW},ip=${IP}/24,type=veth" \
    --nameserver "$DNS" \
    --ostype debian \
    --unprivileged 1 \
    --features nesting=1,keyctl=1 \
    --onboot 1
else
  log "CT $VMID already exists, skipping create"
fi

# --- 3. CT config: Docker-in-LXC + Tailscale TUN --------------------------
# AppArmor unconfined + rw proc/sys match the rest of the fleet's *unprivileged*
# Docker-in-LXC CTs (ct-portfolio/113, ct-workout/114, ct-chat/115): those use
# `lxc.mount.auto: proc:rw sys:rw`, NOT a bind-mount of /proc/sys -- a bind
# mount of the host's /proc/sys into an unprivileged container's user
# namespace fails at start ("Failed to mount /proc/sys ... Invalid argument"),
# which is only used by the fleet's *privileged* CTs (e.g. ct-nvr/104).
# The /dev/net/tun passthrough is NEW for this fleet -- Tailscale cannot
# create its interface in an unprivileged LXC without it.
CONF="/etc/pve/lxc/${VMID}.conf"
add_conf() {
  grep -qxF "$1" "$CONF" || { log "conf += $1"; echo "$1" >> "$CONF"; }
}
add_conf 'lxc.mount.auto: proc:rw sys:rw'
add_conf 'lxc.apparmor.profile: unconfined'
add_conf 'lxc.cgroup2.devices.allow: c 10:200 rwm'
add_conf 'lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file'

# Same pipefail + `grep -q` SIGPIPE class fixed in provision-ct.sh: `grep -q`
# can exit before `pct status` finishes writing, and pipefail would then
# surface the killed producer's SIGPIPE exit rather than grep's. Not an
# observed failure here (pct status's single short line has never lost this
# race in testing) -- fixed defensively so it can't start an already-running
# CT (which would error and abort under set -e) and so it doesn't stand next
# to the fixed sshd form as an example to copy.
ct_status=$(pct status "$VMID" 2>/dev/null || true)
if ! grep -q running <<< "$ct_status"; then
  log "starting CT"
  pct start "$VMID"
fi
