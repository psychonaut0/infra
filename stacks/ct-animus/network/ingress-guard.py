#!/usr/bin/env python3
"""Own only inet animus_ingress in the new guest; never flush the ruleset."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import tempfile

CONFIG = Path('/etc/animus-ingress-guard/config.json')
RECEIPT = Path('/run/animus-ingress-guard/applied.json')


def config(path):
    values = json.loads(path.read_text())
    if set(values) != {'interface', 'ct_ipv4', 'proxy_ipv4', 'backup_ipv4', 'admin_cidr'}:
        raise ValueError('unexpected guard configuration keys')
    if values['interface'] != 'eth0':
        raise ValueError('this new guest has exactly one external interface: eth0')
    for key in ('ct_ipv4', 'proxy_ipv4', 'backup_ipv4'):
        address = ipaddress.ip_address(values[key])
        if address.version != 4 or not address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified:
            raise ValueError('guard requires explicit private IPv4 addresses')
    if len({values[k] for k in ('ct_ipv4', 'proxy_ipv4', 'backup_ipv4')}) != 3:
        raise ValueError('guest/proxy/backup addresses must differ')
    admin = ipaddress.ip_network(values['admin_cidr'], strict=True)
    if admin.version != 4 or not admin.is_private or admin.prefixlen < 24:
        raise ValueError('administration needs an explicit private /24 or narrower CIDR')
    return values


def render(values):
    # -200 conntrack -> -150 guard -> -100 destination NAT. An accept here
    # still traverses later Docker chains; a drop is final before DNAT.
    return f'''table inet animus_ingress {{
  chain prerouting {{
    type filter hook prerouting priority -150; policy accept;
    iifname "eth0" jump uplink
  }}
  chain uplink {{
    meta nfproto ipv6 counter drop
    ct state invalid counter drop
    ct direction reply ct state {{ established, related }} counter accept
    ip daddr {values['ct_ipv4']} ip saddr {values['admin_cidr']} tcp dport 22 counter accept
    ip daddr {values['ct_ipv4']} ip saddr {values['backup_ipv4']} tcp dport 22 counter accept
    ip daddr {values['ct_ipv4']} ip saddr {values['proxy_ipv4']} tcp dport {{ 8095, 9000 }} counter accept
    counter drop
  }}
}}
'''


def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


def normalize(document):
    """Keep exact ordered rules/priority/verdicts; ignore handles and counters."""
    def clean(value):
        if isinstance(value, dict):
            return {key: ({} if key == 'counter' else clean(item))
                    for key, item in value.items() if key not in ('handle', 'packets', 'bytes')}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value
    return [clean(item) for item in document['nftables'] if 'metainfo' not in item]


def live():
    return normalize(json.loads(run(['nft', '-j', 'list', 'table', 'inet', 'animus_ingress']).stdout))


def validate_interface(values):
    addresses = json.loads(run(['ip', '-j', '-4', 'address', 'show', 'dev', 'eth0']).stdout)
    actual = {item['local'] for dev in addresses for item in dev.get('addr_info', []) if item.get('scope') == 'global'}
    if actual != {values['ct_ipv4']}:
        raise ValueError('eth0 must have only the configured guest IPv4 address')
    # Additional external interfaces are outside this deployment contract.
    links = json.loads(run(['ip', '-j', 'link', 'show']).stdout)
    for link in links:
        name = link['ifname'].split('@', 1)[0]
        if name not in ('lo', 'eth0', 'docker0') and not name.startswith(('br-', 'veth')):
            raise ValueError('unexpected guest interface; Docker startup blocked')


def digest(values):
    return hashlib.sha256(render(values).encode()).hexdigest()


def apply(values, receipt):
    validate_interface(values)
    exists = subprocess.run(['nft', 'list', 'table', 'inet', 'animus_ingress'], capture_output=True).returncode == 0
    batch = ('delete table inet animus_ingress\n' if exists else '') + render(values)
    # The kernel validates then commits one atomic batch; a rejected replacement
    # leaves the previous table intact. No global flush or Docker table mutation.
    run(['nft', '--check', '-f', '-'], input=batch)
    run(['nft', '-f', '-'], input=batch)
    document = {'config_sha256': digest(values), 'table': live()}
    receipt.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=receipt.parent, delete=False) as output:
        json.dump(document, output)
        temporary = Path(output.name)
    temporary.chmod(0o600)
    temporary.replace(receipt)
    verify(values, receipt)


def verify(values, receipt):
    validate_interface(values)
    stat = receipt.stat()
    if stat.st_uid != 0 or stat.st_mode & 0o077:
        raise ValueError('private root-owned guard receipt required')
    expected = json.loads(receipt.read_text())
    if expected['config_sha256'] != digest(values) or expected['table'] != live():
        raise ValueError('live guard differs from the applied configuration')


def verify_docker(values, receipt):
    verify(values, receipt)
    # Required so stopping the daemon also stops application containers.
    state = run(['docker', 'info', '--format', '{{json .LiveRestoreEnabled}}']).stdout.strip()
    if state != 'false':
        raise ValueError('Docker live restore must be disabled')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('render', 'apply', 'verify', 'verify-docker'))
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--receipt', type=Path, default=RECEIPT)
    args = parser.parse_args()
    try:
        values = config(args.config)
        if args.action == 'render':
            print(render(values), end='')
        else:
            stat = args.config.stat()
            if os.geteuid() != 0 or stat.st_uid != 0 or stat.st_mode & 0o077:
                raise ValueError('apply/verify requires root and a root-owned 0600 configuration')
            {'apply': apply, 'verify': verify, 'verify-docker': verify_docker}[args.action](values, args.receipt)
            print('PASS: exact guest ingress guard ' + args.action)
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError):
        print('FAIL: guest ingress guard unavailable or mismatched; keep Docker stopped')
        raise SystemExit(1)
