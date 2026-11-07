#!/bin/bash
# New ct-animus ONLY, via pct exec. Never run on a host or an existing workload.
# --lock with net0 link_down=1; --install-guard only after parent connects it.
set -euo pipefail
mode=${1:-}
[[ "$mode" == --lock || "$mode" == --install-guard ]] || { echo 'Use --lock or --install-guard' >&2; exit 2; }
[[ $(id -u) == 0 && $(hostname -s) == ct-animus && $(systemd-detect-virt --container) == lxc ]] || { echo 'Wrong scope; requires new ct-animus LXC' >&2; exit 1; }
awk 'NR == 1 && $1 == 0 && $2 > 0 {ok=1} END {exit !ok}' /proc/self/uid_map || { echo 'Requires an unprivileged LXC user namespace' >&2; exit 1; }
command -v ss >/dev/null
! command -v docker >/dev/null || { echo 'Docker already installed; not an empty bootstrap guest' >&2; exit 1; }
! command -v containerd >/dev/null || { echo 'Container runtime already installed; bootstrap refused' >&2; exit 1; }
state=/var/lib/animus-bootstrap/locked
no_listeners() {
  local sockets
  sockets=$(ss -H -lntu | awk '$5 !~ /^127\./ && $5 !~ /^\[?::1\]?:/ {print}')
  [[ -z "$sockets" ]] || { echo 'Unexpected non-loopback listening socket; keep guest network disconnected' >&2; exit 1; }
}
if [[ "$mode" == --lock ]]; then
  [[ $(cat /sys/class/net/eth0/carrier 2>/dev/null || true) != 1 ]] || { echo 'Disconnect net0 before bootstrap lock' >&2; exit 1; }
  systemctl mask --now docker.service docker.socket containerd.service ssh.service ssh.socket nftables.service
  no_listeners
  install -d -m 700 /var/lib/animus-bootstrap
  touch "$state"
  echo 'LOCKED: SSH/runtime/stock nftables masked; no non-loopback listeners. Network stays disconnected until explicit reservation/collision review by parent.'
  exit 0
fi
[[ -f "$state" && ${ADDRESS_RESERVATION_VERIFIED:-} == yes ]] || { echo 'Console lock and explicit address-reservation evidence required' >&2; exit 1; }
for unit in docker.service docker.socket containerd.service ssh.service ssh.socket nftables.service; do
  [[ $(systemctl is-enabled "$unit" 2>/dev/null || true) == masked ]] || { echo 'Bootstrap service mask missing' >&2; exit 1; }
done
no_listeners
for key in CT_IPV4 PROXY_IPV4 BACKUP_IPV4 ADMIN_CIDR; do [[ -n ${!key:-} ]] || { echo "Missing $key" >&2; exit 1; }; done
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nftables python3 iproute2 ca-certificates curl jq rsync openssh-server
no_listeners
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ ! -e /etc/animus-ingress-guard/config.json ]] || { echo 'Guard already installed; inspect and use its apply/verify operations' >&2; exit 1; }
install -d -m 700 /etc/animus-ingress-guard
python3 - <<'PY'
import json, os
from pathlib import Path
values={'interface':'eth0', 'ct_ipv4':os.environ['CT_IPV4'], 'proxy_ipv4':os.environ['PROXY_IPV4'],
        'backup_ipv4':os.environ['BACKUP_IPV4'], 'admin_cidr':os.environ['ADMIN_CIDR']}
p=Path('/etc/animus-ingress-guard/config.json')
p.write_text(json.dumps(values)+'\n'); p.chmod(0o600)
PY
install -d -m 755 /usr/local/libexec /etc/systemd/system/docker.service.d /etc/systemd/system/ssh.service.d
install -m 755 "$source_dir/network/ingress-guard.py" /usr/local/libexec/animus-ingress-guard
install -m 644 "$source_dir/network/animus-ingress-guard.service" /etc/systemd/system/animus-ingress-guard.service
install -m 644 "$source_dir/network/docker-guard.conf" /etc/systemd/system/docker.service.d/10-animus-ingress.conf
install -m 644 "$source_dir/network/ssh-guard.conf" /etc/systemd/system/ssh.service.d/10-animus-ingress.conf
install -d -m 755 /etc/docker
[[ ! -e /etc/docker/daemon.json ]] || { echo 'Unexpected existing Docker configuration; inspect before continuing' >&2; exit 1; }
printf '%s\n' '{"live-restore":false}' > /etc/docker/daemon.json
systemctl daemon-reload
systemctl enable --now animus-ingress-guard.service
/usr/local/libexec/animus-ingress-guard verify
[[ -s /root/.ssh/authorized_keys ]] || { echo 'Administration key missing; keep SSH masked' >&2; exit 1; }
cat > /etc/ssh/sshd_config.d/00-animus-key-only.conf <<'EOF'
AuthenticationMethods publickey
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
install -d -m 0755 -o root -g root /run/sshd
sshd -t
effective=$(sshd -T)
[[ "$effective" == *'authenticationmethods publickey'* && "$effective" == *'passwordauthentication no'* ]] || { echo 'Effective SSH policy mismatch' >&2; exit 1; }
systemctl unmask ssh.service
systemctl enable --now ssh.service
echo 'GUARDED: key-only SSH enabled behind verified rules. Docker/containerd/socket and stock nftables remain masked. Install Docker while masked, reverify live source denials, then explicitly unmask runtime services.'
