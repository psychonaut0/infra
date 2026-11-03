#!/usr/bin/env python3
"""Shared response-owner gate. It never adopts, repairs or opens DB control."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import time

from bundle import external_control, need

WRITERS = ('api', 'auth-server', 'auth-worker')
STARTUP_HOLD = Path('/etc/animus-control/startup-hold')
STOP_INTENT = Path('/etc/animus-control/stop-intent')
STATUS_ADAPTER = Path('/usr/local/libexec/animus-recovery-check')


def held(path):
    return path.exists() or path.is_symlink()


def run(arguments, *, input=None, timeout=120):
    result = subprocess.run(arguments, input=input, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout)
    need(result.returncode == 0, 'lifecycle command failed')
    return result.stdout


def environment(item):
    entries = item['Config']['Env']
    need(len({entry.partition('=')[0] for entry in entries}) == len(entries), 'ambiguous container environment')
    return dict(entry.split('=', 1) for entry in entries)


def snapshot(items):
    result = {}
    for name in WRITERS:
        item = items[name]
        need(item['HostConfig']['RestartPolicy']['Name'] == 'no', 'unguarded response-owner restart policy')
        # Retain a digest, not passwords embedded in the provider/API environment.
        environment(item)
        digest = hashlib.sha256(json.dumps(sorted(item['Config']['Env'])).encode()).hexdigest()
        result[name] = {'id': item['Id'], 'image': item['Image'], 'environment_sha256': digest,
                        'restart': item['HostConfig']['RestartPolicy']}
    return result


def gate(control, database_status, *, api=None, expected_generation=None, startup_hold=None,
         stop_intent=None, allow_stop_intent=False):
    startup_hold = STARTUP_HOLD if startup_hold is None else startup_hold
    stop_intent = STOP_INTENT if stop_intent is None else stop_intent
    need(not held(startup_hold), 'independent startup hold')
    need(allow_stop_intent or not held(stop_intent), 'durable stop requested')
    generation = external_control(control)
    need(expected_generation is None or generation == expected_generation, 'external generation changed')
    state = database_status(generation)
    need(isinstance(state, dict) and set(state) == {'matched', 'state'}
         and state['matched'] is True and state['state'] in ('closed', 'open'),
         'database authority unavailable or mismatched')
    if api is not None:
        values = environment(api)
        need(values.get('HUB_RECOVERY_MODE') == 'enforced'
             and values.get('HUB_RECOVERY_PROFILE') == 'native-workout-v1'
             and values.get('HUB_RECOVERY_GENERATION') == generation, 'snapshotted API authority mismatch')
    return generation


def persist_stop(path=None):
    path = STOP_INTENT if path is None else path
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, (json.dumps({'version': 1, 'requested_unix': int(time.time())}) + '\n').encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    temporary.replace(path)
    sync_directory(path.parent)


def sync_directory(path):
    descriptor = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def inspect(identifier):
    return json.loads(run(['docker', 'inspect', identifier], timeout=10))[0]


def inventory():
    identifiers = run(['docker', 'ps', '-aq', '--no-trunc', '--filter',
                       'label=com.docker.compose.project=ct-animus']).decode().split()
    need(len(identifiers) <= 6, 'unexpected project inventory')
    result = {}
    for identifier in identifiers:
        item = inspect(identifier)
        labels = item['Config']['Labels']
        name = labels.get('com.docker.compose.service')
        need(labels.get('com.docker.compose.project') == 'ct-animus' and name not in result
             and name in (*WRITERS, 'app-postgres', 'auth-postgres', 'migrate'), 'unexpected project owner')
        need(name != 'migrate' or not item['State']['Running'], 'operator migration active')
        result[name] = item
    return result


def contain_ids(identifiers, *, stop_seconds=60, stop_intent=None):
    """Try every update, every stop and every verification before failing."""
    identifiers = list(dict.fromkeys(identifiers))
    need(len(identifiers) <= 32, 'unexpected response-owner count')
    if not identifiers:
        return
    def attempt(action):
        try:
            return action() is not False
        except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError):
            return False
    def phase(action):
        with ThreadPoolExecutor(max_workers=len(identifiers)) as pool:
            return all(list(pool.map(lambda identifier: attempt(lambda: action(identifier)), identifiers)))
    updated = phase(lambda identifier: run(['docker', 'update', '--restart=no', identifier], timeout=10))
    stopped = phase(lambda identifier: run(['docker', 'stop', '--time', str(stop_seconds), identifier],
                                           timeout=stop_seconds + 15))
    def verify(identifier):
        item = inspect(identifier)
        return (item['State']['Running'] is False
                and item['HostConfig']['RestartPolicy']['Name'] == 'no')
    verified = phase(verify)
    if not (updated and stopped and verified):
        persist_stop(stop_intent)
        raise ValueError('response-owner containment uncertain')


def stop_owners(items=None):
    # Stop all known response owners, including duplicated/drifted owners; no
    # generation file is needed to remove an existing response owner.
    identifiers = run(['docker', 'ps', '-aq', '--no-trunc', '--filter',
                       'label=com.docker.compose.project=ct-animus'], timeout=10).decode().split() if items is None else [
                           item['Id'] for name, item in items.items() if name in WRITERS]
    need(len(identifiers) <= 32, 'unexpected response-owner count')
    owners = []
    uncertain = False
    for identifier in identifiers:
        try:
            item = inspect(identifier)
            labels = item['Config']['Labels']
            if labels.get('com.docker.compose.project') == 'ct-animus' and labels.get('com.docker.compose.service') in WRITERS:
                owners.append(identifier)
        except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError):
            uncertain = True
    contain_ids(owners)
    if uncertain:
        persist_stop()
        raise ValueError('response-owner inventory uncertain')


def database_status(items, generation):
    # Backend storage knowledge stays in the separately reviewed private adapter.
    # The public deployment depends only on this bounded status contract.
    stat = STATUS_ADAPTER.lstat()
    need(STATUS_ADAPTER.is_file() and not STATUS_ADAPTER.is_symlink() and stat.st_uid == os.geteuid()
         and stat.st_mode & 0o077 == 0 and stat.st_mode & 0o100, 'private root adapter unavailable')
    database = items['app-postgres']
    raw = run([str(STATUS_ADAPTER), '--container', database['Id'], '--project',
               database['Config']['Labels']['com.docker.compose.project']], input=(generation + '\n').encode())
    need(len(raw) <= 4096, 'unbounded database status')
    return json.loads(raw)
