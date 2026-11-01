#!/usr/bin/env python3
"""Collect/protect only Animus. Status never derives from fleet backup success."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from bundle import HEX, REQUIRED, need, private_file, sha, unpack, verify


def run(args):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    need(result.returncode == 0, 'copy command failed')
    return result.stdout


def status(path, state, **details):
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    document = {'state': state, 'protected': state == 'protected', 'timestamp': '', **details}
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(document, sort_keys=True) + '\n')
    temporary.chmod(0o644)
    temporary.replace(path)


def config(path):
    private_file(path)
    value = json.loads(path.read_text())
    need(set(value) == {'ssh_host', 'ssh_key', 'known_hosts', 'staging', 'status', 'reserve_bytes', 'max_age_seconds'},
         'unexpected collector configuration')
    need(re.fullmatch(r'root@[a-zA-Z0-9.-]+', value['ssh_host']), 'explicit root SSH target required')
    need(value['reserve_bytes'] >= 256 * 1024 * 1024 and 0 < value['max_age_seconds'] <= 86400,
         'bounded reserve/freshness required')
    for key in ('ssh_key', 'known_hosts', 'staging', 'status'):
        need(Path(value[key]).is_absolute(), 'absolute collector paths required')
    return value


def collect(value):
    destination = Path(value['staging'])
    # Old staging must never be mistaken for this run after failed SSH/capture.
    if destination.exists():
        shutil.rmtree(destination)
    need(shutil.disk_usage(destination.parent).free >= value['reserve_bytes'], 'insufficient receiver headroom')
    partial = destination.with_name(destination.name + '.partial-' + str(uuid.uuid4()))
    args = ['ssh', '-i', value['ssh_key'], '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile=' + value['known_hosts'], '-o', 'ConnectTimeout=15',
            value['ssh_host'], 'animus-coherent-capture']
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        try:
            # Stream extraction uses a disk guard; no unbounded network archive in RAM.
            class Guarded:
                def read(self, count=-1):
                    need(shutil.disk_usage(destination.parent).free >= value['reserve_bytes'] + max(0, count),
                         'insufficient receiver headroom')
                    return process.stdout.read(count)
            unpack(Guarded(), partial)
            need(process.wait() == 0, 'remote capture failed')
            manifest = verify(partial, max_age=value['max_age_seconds'])
            partial.rename(destination)
            return manifest
        except BaseException:
            process.kill()
            process.wait()
            if partial.exists():
                shutil.rmtree(partial)
            raise


def digest_dump(snapshot, path):
    result = hashlib.sha256()
    with subprocess.Popen(['restic', 'dump', snapshot, path], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        for chunk in iter(lambda: process.stdout.read(1024 * 1024), b''):
            result.update(chunk)
        need(process.wait() == 0, 'snapshot payload unavailable')
    return result.hexdigest()


def protect(value):
    # This command is for the installed encrypted B2 destination only. A local
    # rehearsal has separate evidence and must not update off-host protection.
    need(os.environ.get('RESTIC_REPOSITORY', '').startswith('b2:'), 'off-host B2 repository required')
    directory = Path(value['staging'])
    manifest = verify(directory, max_age=value['max_age_seconds'])
    manifest_hash = sha(directory / 'manifest.json')
    tag = 'animus-capture-' + manifest['capture_id']
    run(['restic', 'backup', str(directory), '--tag', 'animus-coherent', '--tag', tag,
         '--host', 'ct-backup', '--quiet'])
    snapshots = json.loads(run(['restic', 'snapshots', '--json', '--tag', tag, '--host', 'ct-backup']))
    need(len(snapshots) == 1 and HEX.fullmatch(snapshots[0]['id']), 'exact unique full snapshot ID required')
    snapshot = snapshots[0]['id']
    for name in sorted(REQUIRED | {'manifest.json'}):
        expected = manifest_hash if name == 'manifest.json' else manifest['files'][name]['sha256']
        need(digest_dump(snapshot, str(directory / name)) == expected, 'off-host snapshot checksum mismatch')
    # Time is source capture START, never upload/fleet success time. Recheck after upload.
    verify(directory, expected_hash=manifest_hash, max_age=value['max_age_seconds'])
    status(Path(value['status']), 'protected', timestamp_unix=manifest['started_unix'],
           timestamp=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(manifest['started_unix'])),
           snapshot_id=snapshot, manifest_sha256=manifest_hash, capture_id=manifest['capture_id'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('collect', 'protect', 'freshness'))
    parser.add_argument('--config', type=Path, default=Path('/etc/animus-backup/collector.json'))
    args = parser.parse_args()
    value = None
    try:
        value = config(args.config)
        if args.action == 'freshness':
            path = Path(value['status'])
            prior = json.loads(path.read_text()) if path.exists() else {}
            need(prior.get('state') == 'protected' and prior.get('protected') is True
                 and 0 <= int(time.time()) - prior.get('timestamp_unix', 0) <= value['max_age_seconds'],
                 'Animus protection stale or missing')
        else:
            status(Path(value['status']), 'capturing' if args.action == 'collect' else 'uploading')
            if args.action == 'collect':
                manifest = collect(value)
                status(Path(value['status']), 'local-only', capture_id=manifest['capture_id'])
            else:
                protect(value)
        print('PASS: Animus ' + args.action)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        if value:
            status(Path(value['status']), 'stale' if args.action == 'freshness' else 'failed')
        print('FAIL: Animus ' + args.action + '; no fresh off-host protection', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
