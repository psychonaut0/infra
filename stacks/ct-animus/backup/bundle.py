#!/usr/bin/env python3
"""Strict, secret-silent coherent Animus bundle format. No runtime side effects."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import time
import uuid

FORMAT = 'animus-coherent-v1'
REQUIRED = {'app.dump', 'app.globals.sql', 'auth.dump', 'auth.globals.sql',
            'auth-data.tar', 'auth-templates.tar', 'auth-certs.tar', 'private-config.tar'}
HEX = re.compile(r'^[0-9a-f]{64}$')
GENERATION_KEYS = {'HUB_RECOVERY_GENERATION', 'HUB_RECOVERY_GENERATION_FILE'}


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def canonical_uuid(value):
    need(isinstance(value, str) and str(uuid.UUID(value)) == value and int(uuid.UUID(value)) != 0,
         'canonical nonzero UUID required')
    return value


def private_file(path):
    stat = path.lstat()
    need(path.is_file() and not path.is_symlink() and stat.st_uid == os.geteuid()
         and stat.st_mode & 0o077 == 0, 'private owner-controlled regular file required')


def safe_name(name):
    p = PurePosixPath(name)
    need(name and not p.is_absolute() and '..' not in p.parts and str(p) == name,
         'unsafe archive path')
    return name


def check_tar(path):
    names = set()
    with tarfile.open(path, 'r:') as archive:
        for item in archive:
            name = safe_name(item.name)
            need(name not in names and (item.isfile() or item.isdir()), 'unsupported archive member')
            need(not item.mode & 0o6000, 'privileged file mode forbidden')
            names.add(name)
    return names


def inventory(directory):
    return {p.name: {'bytes': p.stat().st_size, 'sha256': sha(p)}
            for p in sorted(directory.iterdir()) if p.name != 'manifest.json'}


def verify(directory, expected_hash=None, max_age=None, now=None):
    need(directory.is_dir() and not directory.is_symlink(), 'bundle directory required')
    manifest_path = directory / 'manifest.json'
    if expected_hash:
        need(HEX.fullmatch(expected_hash) and sha(manifest_path) == expected_hash,
             'manifest hash mismatch')
    manifest = json.loads(manifest_path.read_text())
    need(set(manifest) == {'format', 'capture_id', 'started_unix', 'boundary_unix', 'completed_unix',
                           'project', 'source_revision', 'images', 'postgres_versions', 'quiesced',
                           'databases', 'config_members', 'prior_generation', 'files'}, 'unexpected manifest fields')
    need(manifest['format'] == FORMAT, 'unsupported bundle')
    canonical_uuid(manifest['capture_id'])
    canonical_uuid(manifest['prior_generation'])
    need(re.fullmatch(r'[0-9a-f]{40}', manifest['source_revision']) is not None, 'source revision required')
    need(manifest['quiesced'] == ['api', 'auth-server', 'auth-worker'], 'incomplete quiescence')
    need(manifest['databases'] == {'app': 'animus', 'auth': 'authentik'}, 'incomplete databases')
    need(set(manifest['images']) == {'api', 'app-postgres', 'auth-postgres', 'auth-server', 'auth-worker'},
         'incomplete image inventory')
    for value in manifest['images'].values():
        need(set(value) == {'reference', 'id'} and re.fullmatch(r'sha256:[0-9a-f]{64}', value['id']),
             'immutable image ID required')
    need(set(manifest['postgres_versions']) == {'app', 'auth'}, 'missing PostgreSQL versions')
    times = [manifest[k] for k in ('started_unix', 'boundary_unix', 'completed_unix')]
    need(all(isinstance(x, int) and x > 0 for x in times) and times == sorted(times), 'invalid capture times')
    if max_age is not None:
        age = (int(time.time()) if now is None else now) - times[0]
        need(0 <= age <= max_age, 'bundle missing or stale')
    actual = {p.name for p in directory.iterdir()}
    need(actual == REQUIRED | {'manifest.json'}, 'missing or extra bundle files')
    need(set(manifest['files']) == REQUIRED, 'incomplete manifest inventory')
    for name in REQUIRED:
        path = directory / name
        need(path.is_file() and not path.is_symlink() and path.stat().st_size > 0, 'missing bundle payload')
    need(manifest['files'] == inventory(directory), 'bundle checksum mismatch')
    for name in REQUIRED:
        if name.endswith('.tar'):
            members = check_tar(directory / name)
            if name == 'private-config.tar':
                need(members == set(manifest['config_members']), 'configuration inventory mismatch')
                need('stack.env' in members and 'docker-compose.yml' in members
                     and 'oidc-trust.json' in members, 'required configuration omitted')
    return manifest


def unpack(stream, destination):
    """Extract a flat outer bundle without tar path/link/duplicate tricks."""
    need(not destination.exists(), 'destination must not exist')
    destination.mkdir(mode=0o700, parents=True)
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        names = set()
        for item in archive:
            name = safe_name(item.name)
            need(name in REQUIRED | {'manifest.json'} and name not in names and item.isfile(),
                 'unexpected outer archive member')
            names.add(name)
            with (destination / name).open('xb') as output:
                source = archive.extractfile(item)
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
            (destination / name).chmod(0o600)
    return verify(destination)


def external_control(path):
    private_file(path)
    lines = path.read_text().splitlines()
    need(len(lines) == 3, 'exact fresh enforced external control required')
    values = {}
    for line in lines:
        key, separator, value = line.partition('=')
        need(separator and key not in values, 'invalid external control')
        values[key] = value
    need(set(values) == {'HUB_RECOVERY_MODE', 'HUB_RECOVERY_PROFILE', 'HUB_RECOVERY_GENERATION'}
         and values['HUB_RECOVERY_MODE'] == 'enforced' and values['HUB_RECOVERY_PROFILE'] == 'native-workout-v1',
         'enforced native profile required')
    return canonical_uuid(values['HUB_RECOVERY_GENERATION'])
