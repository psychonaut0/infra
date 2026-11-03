#!/usr/bin/env python3
"""Read-only Animus observations, collected through the existing backup SSH path."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

sys.dont_write_bytecode = True

from bundle import external_control, need, private_file
import lifecycle

STATUS = Path('/var/lib/backup-status/animus-operations.json')
CONTROL = Path('/etc/animus-control/recovery.env')
READINESS = Path('/etc/animus-control/authenticated-read.json')
SERVICES = {'api', 'app-postgres', 'auth-postgres', 'auth-server', 'auth-worker'}
BITS = {'api_running', 'database_ready', 'provider_ready', 'control_ready',
        'source_storage_ok', 'authenticated_read_recent', 'recovery_closed'}
FIELDS = {'version', 'observed', 'timestamp_unix', 'authenticated_read_expiry_unix'} | BITS
MAX_AGE = 180
RESERVE = 1024 ** 3


def run(arguments):
    result = subprocess.run(arguments, capture_output=True, timeout=30)
    need(result.returncode == 0 and len(result.stdout) <= 16384, 'observation unavailable')
    return result.stdout


def storage_ok(path):
    size = shutil.disk_usage(path)
    return size.free >= RESERVE and size.used / size.total <= 0.85


def empty():
    return {'version': 1, 'observed': False, 'timestamp_unix': 0,
            'authenticated_read_expiry_unix': 0, **{key: False for key in BITS}}


def recent_read(path, api, generation, now):
    # This is a separately reviewed real-read receipt, never a token or an
    # automatic login. A restart, generation change or expiry invalidates it.
    try:
        private_file(path)
        need(path.stat().st_size <= 4096, 'oversized read receipt')
        value = json.loads(path.read_text())
        need(set(value) == {'version', 'success', 'checked_unix', 'expires_unix',
                           'api_container_id', 'api_started_at', 'generation', 'evidence_sha256'}, 'receipt schema')
        need(value['version'] == 1 and value['success'] is True, 'read did not succeed')
        checked, expiry = value['checked_unix'], value['expires_unix']
        need(type(checked) is int and type(expiry) is int and checked <= now < expiry <= checked + 86400,
             'read evidence expired')
        need(value['api_container_id'] == api['id'] and value['api_started_at'] == api['started']
             and value['generation'] == generation and re.fullmatch('[0-9a-f]{64}', value['evidence_sha256']),
             'read evidence no longer matches this execution')
        return True, expiry
    except (OSError, ValueError, KeyError, TypeError):
        return False, 0


def source_status(*, control=CONTROL, readiness=READINESS, spool=Path('/var/lib/animus-backup'), now=None):
    now = int(time.time()) if now is None else now
    items = lifecycle.inventory()
    need(SERVICES <= set(items), 'missing service')
    services = {name: {'id': item['Id'], 'started': item['State']['StartedAt'],
                       'running': item['State']['Running'],
                       'health': item['State'].get('Health', {}).get('Status')}
                for name, item in items.items() if name in SERVICES}
    result = empty()
    result.update(observed=True, timestamp_unix=now,
                  api_running=services['api']['running'] is True,
                  database_ready=all(services[key]['running'] is True and services[key]['health'] == 'healthy'
                                     for key in ('app-postgres', 'auth-postgres')),
                  provider_ready=all(services[key]['running'] is True and services[key]['health'] == 'healthy'
                                     for key in ('auth-server', 'auth-worker')),
                  source_storage_ok=storage_ok(Path('/')) and storage_ok(spool))
    try:
        generation = external_control(control)
        state = lifecycle.database_status(items, generation)
        lifecycle.gate(control, lambda generation: state, api=items['api'], expected_generation=generation)
        result['recovery_closed'] = state['state'] == 'closed'
        result['control_ready'] = not lifecycle.held(spool / 'capture-hold.json')
        result['authenticated_read_recent'], result['authenticated_read_expiry_unix'] = recent_read(
            readiness, services['api'], generation, now)
        if not result['api_running'] or result['recovery_closed'] or not result['control_ready']:
            result['authenticated_read_recent'], result['authenticated_read_expiry_unix'] = False, 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        pass
    return result


def validate(value, now):
    need(set(value) == FIELDS and value['version'] == 1 and value['observed'] is True, 'invalid observation')
    need(all(type(value[key]) is bool for key in BITS), 'invalid readiness bits')
    need(type(value['timestamp_unix']) is int and 0 <= now - value['timestamp_unix'] <= MAX_AGE, 'stale observation')
    expiry = value['authenticated_read_expiry_unix']
    need(type(expiry) is int and ((value['authenticated_read_recent'] and now < expiry <= now + 86400)
                                 or (not value['authenticated_read_recent'] and expiry == 0)), 'invalid read expiry')
    return value


def save(value):
    temporary = STATUS.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True) + '\n')
    temporary.chmod(0o644)
    temporary.replace(STATUS)


def collect(config):
    private_file(config)
    value = json.loads(config.read_text())
    need(re.fullmatch(r'root@[a-zA-Z0-9.-]+', value['ssh_host']), 'fixed SSH target required')
    need(all(Path(value[key]).is_absolute() for key in ('ssh_key', 'known_hosts')), 'absolute SSH inputs required')
    raw = run(['ssh', '-i', value['ssh_key'], '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UserKnownHostsFile=' + value['known_hosts'], '-o', 'ConnectTimeout=10',
               value['ssh_host'], 'animus-operational-status'])
    result = validate(json.loads(raw), int(time.time()))
    result['receiver_storage_ok'] = storage_ok(Path('/var/backup-staging'))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('source', 'collect', 'freshness', 'read-begin'))
    parser.add_argument('--config', type=Path, default=Path('/etc/animus-backup/collector.json'))
    args = parser.parse_args()
    if args.action == 'read-begin':
        # Mandatory first step of the bounded read campaign. Failed renewal
        # cannot leave a previous successful source receipt available.
        READINESS.unlink(missing_ok=True)
        lifecycle.sync_directory(READINESS.parent)
        return
    if args.action == 'source':
        print(json.dumps(source_status(), sort_keys=True))
        return
    STATUS.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    with (STATUS.parent / '.animus-operations.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if args.action == 'collect':
                save({**empty(), 'receiver_storage_ok': False})
                result = collect(args.config)
            else:
                result = json.loads(STATUS.read_text())
                receiver = result.pop('receiver_storage_ok')
                need(type(receiver) is bool, 'invalid receiver observation')
                validate(result, int(time.time()))
                result['receiver_storage_ok'] = receiver
            save(result)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            save({**empty(), 'receiver_storage_ok': False})
            raise


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('FAIL: Animus operational observation unavailable', file=sys.stderr)
        raise SystemExit(1)
