#!/usr/bin/env python3
"""Run ONLY in a disposable user+network namespace; creates synthetic interfaces."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent


def run(*args, check=True, **kwargs):
    return subprocess.run(args, check=check, capture_output=True, text=True, **kwargs)


def check(ok, message):
    if not ok:
        raise RuntimeError(message)


def main():
    mapping = [line.split() for line in Path('/proc/self/uid_map').read_text().splitlines()]
    check(os.geteuid() == 0 and len(mapping) == 1 and mapping[0][0] == '0'
          and int(mapping[0][1]) > 0 and mapping[0][2] == '1',
          'requires disposable unprivileged user/network namespace, never host root')
    check([i['ifname'] for i in json.loads(run('ip', '-j', 'link').stdout)] == ['lo'],
          'namespace is not empty; refusing interface/rule mutations')
    child = subprocess.Popen(['unshare', '--net', 'sleep', '300'])
    processes = [child]
    try:
        for _ in range(100):
            if os.readlink(f'/proc/{child.pid}/ns/net') != os.readlink('/proc/self/ns/net'):
                break
            time.sleep(0.01)
        else:
            raise RuntimeError('peer network namespace was not created')
        peer = ['nsenter', '-t', str(child.pid), '-n']
        run('ip', 'link', 'set', 'lo', 'up')
        run('ip', 'link', 'add', 'eth0', 'type', 'veth', 'peer', 'name', 'testpeer')
        run('ip', 'link', 'set', 'testpeer', 'netns', str(child.pid))
        run('ip', 'address', 'add', '192.168.100.20/24', 'dev', 'eth0')
        run('ip', '-6', 'address', 'add', 'fd00::20/64', 'dev', 'eth0', 'nodad')
        run('ip', 'link', 'set', 'eth0', 'up')
        run(*peer, 'ip', 'link', 'set', 'lo', 'up')
        run(*peer, 'ip', 'link', 'set', 'testpeer', 'up')
        for suffix in (10, 12, 13, 99):
            run(*peer, 'ip', 'address', 'add', f'192.168.100.{suffix}/24', 'dev', 'testpeer')
        run(*peer, 'ip', '-6', 'address', 'add', 'fd00::12/64', 'dev', 'testpeer', 'nodad')
        host_mac = json.loads(run('ip', '-j', 'link', 'show', 'eth0').stdout)[0]['address']
        peer_mac = json.loads(run(*peer, 'ip', '-j', 'link', 'show', 'testpeer').stdout)[0]['address']
        run('ip', '-6', 'neigh', 'replace', 'fd00::12', 'lladdr', peer_mac, 'nud', 'permanent', 'dev', 'eth0')
        run(*peer, 'ip', '-6', 'neigh', 'replace', 'fd00::20', 'lladdr', host_mac, 'nud', 'permanent', 'dev', 'testpeer')
        run('ip', 'link', 'add', 'docker0', 'type', 'dummy')
        run('ip', 'address', 'add', '172.30.0.1/24', 'dev', 'docker0')
        run('ip', 'link', 'set', 'docker0', 'up')
        for port in (22, 5432, 18095, 18000):
            processes.append(subprocess.Popen([sys.executable, '-m', 'http.server', str(port), '--bind', '0.0.0.0'],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        processes.append(subprocess.Popen(peer + [sys.executable, '-m', 'http.server', '8081', '--bind', '192.168.100.99'],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        processes.append(subprocess.Popen([sys.executable, '-m', 'http.server', '8095', '--bind', '::'],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        run('nft', '-f', '-', input='''table ip simulated_docker {
 chain prerouting { type nat hook prerouting priority -100; policy accept;
  ip daddr 192.168.100.20 tcp dport 8095 dnat to 172.30.0.1:18095
  ip daddr 192.168.100.20 tcp dport 9000 dnat to 172.30.0.1:18000
 }
}''')
        def request(source, port, destination='192.168.100.20'):
            target = f'[{destination}]' if ':' in destination else destination
            return run(*peer, 'curl', '--noproxy', '*', '--silent', '--output', '/dev/null', '--write-out', '%{http_code}',
                       '--connect-timeout', '0.4', '--max-time', '0.6', '--interface', source,
                       f'http://{target}:{port}/', check=False).stdout == '200'
        for _ in range(100):
            if request('192.168.100.99', 8095):
                break
            time.sleep(0.02)
        else:
            raise RuntimeError('baseline simulated Docker DNAT listener did not become ready')
        check(request('fd00::12', 8095, 'fd00::20'), 'IPv6 baseline must be reachable before installing guard')
        with tempfile.TemporaryDirectory(prefix='animus-guard-kernel-') as directory:
            config = Path(directory) / 'config.json'
            receipt = Path(directory) / 'receipt.json'
            values = dict(interface='eth0', ct_ipv4='192.168.100.20', proxy_ipv4='192.168.100.12',
                          backup_ipv4='192.168.100.13', admin_cidr='192.168.100.10/32')
            config.write_text(json.dumps(values))
            config.chmod(0o600)
            guard = [sys.executable, str(ROOT / 'ingress-guard.py')]
            options = ['--config', str(config), '--receipt', str(receipt)]
            run(*guard, 'apply', *options)
            for source, port in (('192.168.100.12', 8095), ('192.168.100.12', 9000),
                                 ('192.168.100.10', 22), ('192.168.100.13', 22)):
                check(request(source, port), 'intended proxy/admin/backup path denied')
            for source, port, destination in (
                ('192.168.100.99', 8095, '192.168.100.20'),
                ('192.168.100.99', 9000, '192.168.100.20'),
                ('192.168.100.10', 8095, '192.168.100.20'),
                ('192.168.100.12', 22, '192.168.100.20'),
                ('192.168.100.12', 5432, '192.168.100.20'),
                ('192.168.100.99', 22, '192.168.100.20'),
                ('192.168.100.12', 18095, '172.30.0.1'),
                ('fd00::12', 8095, 'fd00::20')):
                check(not request(source, port, destination), 'unapproved ingress was reachable')
            check(run('curl', '--noproxy', '*', '--silent', '--output', '/dev/null', '--write-out', '%{http_code}',
                      '--max-time', '2', 'http://192.168.100.99:8081/').stdout == '200',
                  'reply-direction package/DNS-style egress response was denied')
            run(*guard, 'verify', *options)
            # Rejected atomic replacement must preserve the existing live table.
            invalid = run('nft', '-f', '-', input='delete table inet animus_ingress\nthis is invalid\n', check=False)
            check(invalid.returncode != 0, 'invalid transaction unexpectedly succeeded')
            run(*guard, 'verify', *options)
            # Receipt/config drift and direct kernel-rule drift block verification.
            original = config.read_text()
            values['proxy_ipv4'] = '192.168.100.15'
            config.write_text(json.dumps(values))
            check(run(*guard, 'verify', *options, check=False).returncode != 0, 'changed config was accepted')
            config.write_text(original)
            run('nft', 'insert', 'rule', 'inet', 'animus_ingress', 'uplink', 'ip', 'saddr', '192.168.100.99', 'accept')
            check(run(*guard, 'verify', *options, check=False).returncode != 0, 'changed kernel table was accepted')
            run(*guard, 'apply', *options)
            check(not request('192.168.100.99', 8095), 'atomic reapply failed to restore denial')
            # Simulate Docker NAT rule recreation; own table and receipts survive.
            run('nft', 'flush', 'chain', 'ip', 'simulated_docker', 'prerouting')
            run(*guard, 'verify', *options)
        print('PASS: actual nft pre-DNAT source policy, IPv6/direct-route/database denial, reply egress, atomic failure preservation, drift rejection and independent NAT lifecycle')
        print('LIMIT: simulated Docker DNAT only; actual LXC capability, systemd startup and Docker restart/reboot/source probes remain required')
    finally:
        for process in reversed(processes):
            process.terminate()
        for process in processes:
            process.wait(timeout=5)


if __name__ == '__main__':
    main()
