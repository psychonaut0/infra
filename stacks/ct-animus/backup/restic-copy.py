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
import tarfile
import time
import uuid
from urllib.parse import urlsplit

sys.dont_write_bytecode = True

from bundle import HEX, REQUIRED, need, private_file, sha, unpack, verify

DEFAULT_STATUS = Path('/var/lib/backup-status/animus.json')


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
    need(value['status'] == str(DEFAULT_STATUS), 'fixed Animus status path required')
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


def b2_repository(repository):
    """Native B2 or TLS path-style S3 at Backblaze's regional endpoint only."""
    if not isinstance(repository, str) or re.search(r'[\s\\?#%]', repository):
        return False
    bucket_pattern = r'[A-Za-z0-9][A-Za-z0-9.-]{1,61}[A-Za-z0-9]'
    if repository.startswith('b2:'):
        match = re.fullmatch(r'b2:(' + bucket_pattern + r'):([A-Za-z0-9._/-]*)', repository)
        return bool(match and '..' not in match[1] and not re.fullmatch(r'[0-9.]+', match[1]))
    if not repository.startswith('s3:'):
        return False
    location = repository[3:]
    try:
        value = urlsplit(location if '://' in location else 'https://' + location)
        if (value.scheme != 'https' or value.username is not None or value.password is not None
                or value.query or value.fragment or value.port not in (None, 443)
                or not re.fullmatch(r's3\.[a-z]+(?:-[a-z]+)*-[0-9]{3}\.backblazeb2\.com', value.hostname or '')):
            return False
        parts = value.path.split('/')
        bucket = parts[1] if len(parts) > 1 else ''
        return bool(re.fullmatch(bucket_pattern, bucket) and bucket == bucket.lower()
                    and '..' not in bucket and not re.fullmatch(r'[0-9.]+', bucket)
                    and all(re.fullmatch(r'[A-Za-z0-9._-]*', part) and part not in ('.', '..')
                            for part in parts[2:]))
    except ValueError:
        return False


def protect(value):
    # This command is for the installed encrypted B2 destination only. A local
    # rehearsal has separate evidence and must not update off-host protection.
    need(b2_repository(os.environ.get('RESTIC_REPOSITORY', '')), 'off-host B2 repository required')
    directory = Path(value['staging'])
    manifest = verify(directory, max_age=value['max_age_seconds'])
    prior = json.loads(Path(value['status']).read_text())
    need(prior.get('state') == 'local-only' and prior.get('capture_id') == manifest['capture_id'],
         'this run has no successful coherent capture')
    status(Path(value['status']), 'uploading', capture_id=manifest['capture_id'])
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
            if args.action == 'collect':
                status(Path(value['status']), 'capturing')
            if args.action == 'collect':
                manifest = collect(value)
                status(Path(value['status']), 'local-only', capture_id=manifest['capture_id'])
            else:
                protect(value)
        print('PASS: Animus ' + args.action)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, tarfile.TarError):
        try:
            status(Path(value['status']) if value else DEFAULT_STATUS,
                   'stale' if args.action == 'freshness' else 'failed')
        except OSError:
            pass  # Nonzero exit still fails the installed job if status storage itself failed.
        print('FAIL: Animus ' + args.action + '; no fresh off-host protection', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
