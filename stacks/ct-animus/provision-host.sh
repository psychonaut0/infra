#!/bin/bash
# ON the intended Proxmox node, as root. Default is read-only preflight.
# Requires explicit operator inputs; never edits an existing CT or starts one.
set -euo pipefail
mode=${1:---preflight}
[[ "$mode" == --preflight || "$mode" == --create-stopped ]] || { echo 'Use --preflight or --create-stopped' >&2; exit 2; }
for tool in pct pvesh pvesm python3 hostname id awk; do
  command -v "$tool" >/dev/null || { echo "Missing required host tool: $tool" >&2; exit 1; }
done
for key in TARGET_NODE VMID CT_IPV4 CT_GATEWAY PRIVATE_DNS_IPV4 PROXY_IPV4 BACKUP_IPV4 ADMIN_CIDR TEMPLATE SSH_PUBLIC_KEY_FILE; do
  [[ -n ${!key:-} ]] || { echo "Missing operator input: $key" >&2; exit 1; }
done
[[ $(id -u) == 0 && $(hostname -s) == "$TARGET_NODE" ]] || { echo 'Wrong user/node' >&2; exit 1; }
[[ "$VMID" =~ ^[1-9][0-9]{2,5}$ ]] || { echo 'Invalid VMID' >&2; exit 1; }
[[ "$TEMPLATE" =~ ^local:vztmpl/debian-13-standard_[a-zA-Z0-9._-]+_amd64.tar.zst$ ]] || { echo 'Use a cached Debian 13 amd64 template' >&2; exit 1; }
[[ -s "$SSH_PUBLIC_KEY_FILE" ]] || { echo 'Staged administration public key required' >&2; exit 1; }
python3 - <<'PY'
import ipaddress, os
for key in ('CT_IPV4','CT_GATEWAY','PRIVATE_DNS_IPV4','PROXY_IPV4','BACKUP_IPV4'):
    a=ipaddress.ip_address(os.environ[key])
    assert a.version == 4 and a.is_private and not a.is_loopback and not a.is_unspecified and not a.is_link_local, key
assert len({os.environ[k] for k in ('CT_IPV4','PROXY_IPV4','BACKUP_IPV4')}) == 3
n=ipaddress.ip_network(os.environ['ADMIN_CIDR'], strict=True)
assert n.version == 4 and n.is_private and n.prefixlen >= 24
assert ipaddress.ip_address(os.environ['CT_GATEWAY']) in ipaddress.ip_network(os.environ['CT_IPV4']+'/24', strict=False)
PY
resources=$(pvesh get /cluster/resources --type vm --output-format json)
existing_vmid=$(python3 -c '
import json, sys
rows = json.load(sys.stdin)
assert isinstance(rows, list) and all(isinstance(row, dict) and type(row.get("vmid")) is int for row in rows)
print(sum(row["vmid"] == int(sys.argv[1]) for row in rows))
' "$VMID" <<< "$resources" 2>/dev/null) || { echo 'Unable to parse cluster VM inventory' >&2; exit 1; }
[[ "$existing_vmid" == 0 ]] || { echo 'VMID already exists; refusing any existing-guest mutation' >&2; exit 1; }
template_path=$(pvesm path "$TEMPLATE")
[[ -s "$template_path" ]] || { echo 'Cached template not found' >&2; exit 1; }
# Live available memory/storage, never sum of configured CT limits.
available_kib=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
(( available_kib >= 10 * 1024 * 1024 )) || { echo 'Less than 10 GiB available; re-evaluate 6 GiB CT plus host headroom' >&2; exit 1; }
storage=$(pvesh get "/nodes/${TARGET_NODE}/storage/local-lvm/status" --output-format json)
available_disk=$(python3 -c '
import json, sys
status = json.load(sys.stdin)
assert isinstance(status, dict)
value = status.get("avail", 0)
assert type(value) is int and value >= 0
print(value)
' <<< "$storage" 2>/dev/null) || { echo 'Unable to parse storage availability' >&2; exit 1; }
(( available_disk >= 48 * 1024 * 1024 * 1024 )) || { echo 'Less than 48 GiB free local-lvm; re-evaluate 32 GiB rootfs plus upgrade margin' >&2; exit 1; }
echo 'PASS: fresh VMID, cached template, snapshot memory/storage floors'
echo 'Reservation remains an operator gate: verify DHCP/IPAM, existing DNS and routing conflicts; a silent ping/empty neighbor table does not prove address availability.'
if [[ "$mode" == --preflight ]]; then exit 0; fi
[[ ${RESOURCE_PEAK_REVIEWED:-} == yes && ${ISOLATED_BOOTSTRAP_REVIEWED:-} == yes ]] || {
  echo 'Offline creation requires RESOURCE_PEAK_REVIEWED=yes ISOLATED_BOOTSTRAP_REVIEWED=yes from the operator' >&2; exit 1;
}
# Creation is OFFLINE: address reservation gates the later network-enable step.
# No host/cluster firewall activation or rule writes are performed here.
pct create "$VMID" "$TEMPLATE" --hostname ct-animus \
  --cores 2 --memory 6144 --swap 1024 --rootfs local-lvm:32 \
  --net0 "name=eth0,bridge=vmbr0,link_down=1,gw=${CT_GATEWAY},ip=${CT_IPV4}/24,type=veth" \
  --nameserver "$PRIVATE_DNS_IPV4" --ostype debian --unprivileged 1 \
  --features nesting=1,keyctl=1 --onboot 0 --ssh-public-keys "$SSH_PUBLIC_KEY_FILE"
conf="/etc/pve/lxc/${VMID}.conf"
cat >> "$conf" <<'EOF'
#ct-animus - private API and identity; synthetic commissioning only
lxc.mount.auto: proc:rw sys:rw
lxc.apparmor.profile: unconfined
lxc.cgroup2.memory.high: 5368709120
EOF
echo 'Created STOPPED with onboot=0 and net0 link_down=1; memory.high=5 GiB below 6 GiB hard cap. Start console-only, run bootstrap --lock, reserve the address, then connect only for package egress and guest guard installation. No application service was started.'
