#!/usr/bin/env python3
"""Restore exact checksummed artifacts into a blank isolated target; never open it."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from bundle import HEX, external_control, need, private_file, sha, verify
from capture import command


def fetch(snapshot, manifest_hash, source_path, destination):
    need(HEX.fullmatch(snapshot) and HEX.fullmatch(manifest_hash), 'full snapshot ID and manifest SHA required')
    need(source_path.is_absolute() and '..' not in source_path.parts, 'absolute exact snapshot bundle path required')
    need(not destination.exists(), 'restore destination must not exist')
    snapshots = json.loads(command(['restic', 'snapshots', '--json', snapshot]))
    need(len(snapshots) == 1 and snapshots[0]['id'] == snapshot, 'exact immutable snapshot absent')
    need('animus-coherent' in snapshots[0].get('tags', []), 'coherent snapshot tag required')
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.animus-fetch-', dir=destination.parent) as temporary:
        command(['restic', 'restore', snapshot, '--target', temporary, '--include', str(source_path)])
        bundle = Path(temporary) / str(source_path).lstrip('/')
        verify(bundle, expected_hash=manifest_hash)
        shutil.move(bundle, destination)
        destination.chmod(0o700)
    return verify(destination, expected_hash=manifest_hash)


def import_databases(directory, manifest_hash, target_path, control_path, fence_path):
    manifest = verify(directory, expected_hash=manifest_hash)
    generation = external_control(control_path)
    need(generation != manifest['prior_generation'], 'fresh G must differ from backup predecessor')
    need(not control_path.resolve().is_relative_to(directory.resolve()), 'external G must be held outside restored bundle')
    private_file(fence_path)
    fence = json.loads(fence_path.read_text())
    need(set(fence) == {'generation', 'backup_manifest_sha256', 'old_host_fenced', 'evidence_sha256'}
         and fence['generation'] == generation and fence['backup_manifest_sha256'] == manifest_hash
         and fence['old_host_fenced'] is True and HEX.fullmatch(fence['evidence_sha256']),
         'explicit old-host fence evidence required')
    private_file(target_path)
    target = json.loads(target_path.read_text())
    need(set(target) == {'project', 'databases'} and target['project'] != manifest['project']
         and re.fullmatch(r'animus-restore-[a-z0-9-]+', target['project']), 'separate restore namespace required')
    need(set(target['databases']) == {'app', 'auth'}, 'both target databases required')
    # No writers/proxy may exist in this namespace, even stopped ones. Import is
    # deliberately prior to a separately reviewed deployment/repair procedure.
    ids = command(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + target['project']]).decode().split()
    need(len(ids) == 2, 'restore target must have only its two blank DB containers')
    actual = {}
    for identifier in ids:
        item = json.loads(command(['docker', 'inspect', identifier]))[0]
        name = item['Config']['Labels'].get('com.docker.compose.service')
        need(name in ('app-postgres', 'auth-postgres') and name not in actual, 'foreign restore container')
        need(item['State']['Running'] and not item['State'].get('Paused')
             and not item['HostConfig'].get('PortBindings') and not item['HostConfig'].get('Privileged'),
             'private isolated running DB required')
        actual[name] = item
    all_ids = {item['Id'] for item in actual.values()}
    for item in actual.values():
        need(item['NetworkSettings']['Networks'], 'isolated DB network required')
        for network in item['NetworkSettings']['Networks']:
            value = json.loads(command(['docker', 'network', 'inspect', network]))[0]
            need(value['Internal'] and set(value.get('Containers', {})) <= all_ids, 'target network is not isolated')
    for which in ('app', 'auth'):
        db = target['databases'][which]
        need(set(db) == {'container', 'admin'} and db['admin'] == 'animus_restore_operator', 'distinct blank-target admin required')
        item = actual[which + '-postgres']
        need(db['container'] == item['Id'] and item['Image'] == manifest['images'][which + '-postgres']['id'],
             'target identity or pinned image differs')
        def sql(query):
            return command(['docker', 'exec', '-i', item['Id'], 'psql', '-X', '-U', db['admin'], '-d', 'postgres',
                            '-At', '-v', 'ON_ERROR_STOP=1', '-c', query]).decode().strip()
        need(sql("SELECT rolname FROM pg_roles WHERE rolname NOT LIKE 'pg_%' ORDER BY rolname") == db['admin'],
             'target roles are not blank')
        need(sql("SELECT datname FROM pg_database WHERE datname NOT IN ('postgres','template0','template1')") == '',
             'target databases are not blank')
        need(sql("SELECT count(*) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND backend_type='client backend'") == '0',
             'unexpected target database client')
    # Check BOTH targets before importing either. A partial failure is never
    # retried over nonempty state: discard this disposable target and start blank.
    for which in ('app', 'auth'):
        item = actual[which + '-postgres']
        base = ['docker', 'exec', '-i', item['Id']]
        for name, args in [(which + '.globals.sql', ['psql', '-X', '-U', 'animus_restore_operator', '-d', 'postgres',
                                                   '-v', 'ON_ERROR_STOP=1']),
                           (which + '.dump', ['pg_restore', '-U', 'animus_restore_operator', '-d', 'postgres',
                                             '--create', '--exit-on-error'])]:
            with (directory / name).open('rb') as source:
                result = subprocess.run(base + args, stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                need(result.returncode == 0, 'restore import failed; discard isolated target')
    receipt = {'state': 'imported-closed', 'generation': generation, 'backup_manifest_sha256': manifest_hash,
               'old_host_fence_sha256': sha(fence_path), 'repair': 'pending', 'provider_files': 'verified-artifacts-only'}
    output = directory.parent / (directory.name + '-imported-closed.json')
    need(not output.exists(), 'restore receipt already exists')
    output.write_text(json.dumps(receipt, sort_keys=True) + '\n')
    output.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    fetch_parser = sub.add_parser('fetch')
    fetch_parser.add_argument('--snapshot', required=True)
    fetch_parser.add_argument('--manifest-sha256', required=True)
    fetch_parser.add_argument('--source-path', type=Path, required=True)
    fetch_parser.add_argument('--destination', type=Path, required=True)
    imported = sub.add_parser('import-databases')
    imported.add_argument('--bundle', type=Path, required=True)
    imported.add_argument('--manifest-sha256', required=True)
    imported.add_argument('--target', type=Path, required=True)
    imported.add_argument('--external-control', type=Path, required=True)
    imported.add_argument('--old-host-fence', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == 'fetch':
            fetch(args.snapshot, args.manifest_sha256, args.source_path, args.destination)
        else:
            import_databases(args.bundle, args.manifest_sha256, args.target, args.external_control, args.old_host_fence)
        print('PASS: isolated restore artifacts verified; ingress/provider/jobs/repair/open remain closed')
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('FAIL: restore remains closed; discard any partial target before retry', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
