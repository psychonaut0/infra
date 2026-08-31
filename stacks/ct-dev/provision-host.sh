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
# Floor-only: only write /etc/sysctl.d/99-ct-dev.conf if the CONFIGURED baseline
# (without our drop-in) is below the floor. Never lower the system default.
# The runtime value is untrustworthy (we might have set it ourselves on a prior run).

get_configured_baseline() {
  # Compute vm.max_map_count from configuration files only, excluding our own drop-in.
  # Mimics systemd-sysctl logic: gathers all *.conf from sysctl.d and /usr/lib/sysctl.d/,
  # sorts by BASENAME (later basename wins), and takes the last matching value.
  local key="vm.max_map_count"
  local dirs=("/etc/sysctl.d" "/run/sysctl.d" "/usr/lib/sysctl.d")
  local conf_file="/etc/sysctl.conf"

  # Collect all config files, excluding our own drop-in
  local all_files=()
  if [[ -f "$conf_file" ]]; then
    all_files+=("$conf_file")
  fi
  for dir in "${dirs[@]}"; do
    if [[ -d "$dir" ]]; then
      while IFS= read -r f; do
        if [[ "$f" != *"99-ct-dev.conf" ]]; then
          all_files+=("$f")
        fi
      done < <(find "$dir" -maxdepth 1 -name "*.conf" -type f 2>/dev/null | sort)
    fi
  done

  # Sort by basename (later basename wins). Extract value from last matching line.
  local last_value=""
  for f in "${all_files[@]}"; do
    local basename_f=$(basename "$f")
    for other_f in "${all_files[@]}"; do
      if [[ "$basename_f" == "$(basename "$other_f")" && "$other_f" != "$f" ]]; then
        # Found another file with the same basename; skip this one (earlier in sort order)
        f=""
        break
      fi
    done
    if [[ -n "$f" && -f "$f" ]]; then
      while IFS= read -r line; do
        if [[ "$line" =~ ^[[:space:]]*${key}[[:space:]]*= ]]; then
          last_value=$(echo "$line" | sed "s/.*=//;s/[[:space:]]*//g")
        fi
      done < "$f"
    fi
  done

  # If no value found in config, fall back to current runtime value (kernel default)
  if [[ -z "$last_value" ]]; then
    last_value=$(sysctl -n vm.max_map_count 2>/dev/null || echo "")
  fi

  # Validate numeric
  if [[ -n "$last_value" && "$last_value" =~ ^[0-9]+$ ]]; then
    echo "$last_value"
  else
    log "FAIL: could not determine vm.max_map_count baseline; got '$last_value'"
    exit 1
  fi
}

baseline=$(get_configured_baseline)
if ! [[ "$baseline" =~ ^[0-9]+$ ]]; then
  log "FAIL: baseline value '$baseline' is not numeric"
  exit 1
fi

if [[ $baseline -lt 262144 ]]; then
  log "vm.max_map_count baseline is $baseline, below floor 262144; setting to 262144"
  install -d -m 755 /etc/sysctl.d
  echo 'vm.max_map_count=262144' > /etc/sysctl.d/99-ct-dev.conf
  sysctl -p /etc/sysctl.d/99-ct-dev.conf >/dev/null
else
  log "vm.max_map_count baseline is $baseline, meets floor 262144; no drop-in needed"
  rm -f /etc/sysctl.d/99-ct-dev.conf
fi

# Assert the floor is met on the effective running value
final=$(sysctl -n vm.max_map_count)
if ! [[ "$final" =~ ^[0-9]+$ ]] || [[ $final -lt 262144 ]]; then
  log "FAIL: effective vm.max_map_count=$final is below floor 262144"
  exit 1
fi
