#!/usr/bin/env python3
"""Deterministic faults only. Never invoke Docker, SSH, restic, alerts or a DB."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bundle
import capture
import restore
import compose
spec = importlib.util.spec_from_file_location('restic_copy', HERE / 'restic-copy.py')
copy_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(copy_module)

OLD = '11111111-1111-4111-8111-111111111111'
FRESH = '22222222-2222-4222-8222-222222222222'


def private(path, data):
    path.write_text(data)
    path.chmod(0o600)
    return path


def control(path, generation=FRESH):
    return private(path, 'HUB_RECOVERY_MODE=enforced\nHUB_RECOVERY_PROFILE=native-workout-v1\nHUB_RECOVERY_GENERATION=' + generation + '\n')


def make_tar(path, members):
    with tarfile.open(path, 'w:') as archive:
        for name, value in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))


def fixture(root):
    root.mkdir()
    for name in bundle.REQUIRED:
        if name == 'private-config.tar':
            make_tar(root / name, {'stack.env': b'SECRET=synthetic\n', 'docker-compose.yml': b'synthetic',
                                   'oidc-trust.json': b'{}', 'db/init.sh': b'synthetic',
                                   'blueprints/synthetic.yaml': b'synthetic', 'backup/capture.py': b'synthetic'})
        elif name.endswith('.tar'):
            make_tar(root / name, {name.removesuffix('.tar') + '/synthetic': b'original\x00bytes'})
        else:
            (root / name).write_bytes(b'synthetic logical archive or globals')
    now = int(time.time())
    manifest = {'format': bundle.FORMAT, 'capture_id': '33333333-3333-4333-8333-333333333333',
                'started_unix': now - 2, 'boundary_unix': now - 1, 'completed_unix': now,
                'project': 'ct-animus', 'source_revision': 'a' * 40, 'prior_generation': OLD,
                'images': {name: {'id': 'sha256:' + 'b' * 64, 'reference': 'synthetic@sha256:' + 'b' * 64}
                           for name in capture.SERVICES},
                'postgres_versions': {'app': 'pg_dump (PostgreSQL) 17.11', 'auth': 'pg_dump (PostgreSQL) 17.11'},
                'quiesced': list(capture.WRITERS), 'databases': {'app': 'animus', 'auth': 'authentik'},
                'config_members': sorted(bundle.check_tar(root / 'private-config.tar')), 'files': bundle.inventory(root)}
    private(root / 'manifest.json', json.dumps(manifest))
    return manifest


class RepositoryTests(unittest.TestCase):
    def test_native_and_tls_backblaze_s3_profiles(self):
        for value in ('b2:synthetic:repo', 'b2:synthetic:',
                      's3:s3.eu-central-003.backblazeb2.com/synthetic/backup',
                      's3:https://s3.eu-central-003.backblazeb2.com/synthetic/backup',
                      's3:https://s3.us-west-004.backblazeb2.com:443/synthetic'):
            with self.subTest(value=value):
                self.assertTrue(copy_module.b2_repository(value))

    def test_other_backends_and_ambiguous_or_insecure_profiles_fail_closed(self):
        for value in ('', '/tmp/local', 'b2:', 'b2::repo', 'b2:synthetic',
                      's3:http://s3.eu-central-003.backblazeb2.com/synthetic',
                      's3:https://localhost/synthetic', 's3:127.0.0.1/synthetic',
                      's3:s3.us-east-1.amazonaws.com/synthetic',
                      's3:s3.eu-central-003.backblazeb2.com.evil.invalid/synthetic',
                      's3:s3.eu-central-003.backblazeb2.com/',
                      's3:https://user:secret@s3.eu-central-003.backblazeb2.com/synthetic',
                      's3:s3.eu-central-003.backblazeb2.com/synthetic#fragment',
                      's3:s3.eu-central-003.backblazeb2.com/synthetic?query',
                      's3:s3.eu-central-003.backblazeb2.com:80/synthetic',
                      's3:s3.eu-central-003.backblazeb2.com/%73ynthetic',
                      's3:s3.eu-central-003.backblazeb2.com/synthetic/../other',
                      's3:s3.eu-central-003.backblazeb2.com/synthetic\n'):
            with self.subTest(value=value):
                self.assertFalse(copy_module.b2_repository(value))


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.directory = self.root / 'bundle'
        self.manifest = fixture(self.directory)

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_bundle_and_original_bytes(self):
        self.assertEqual(bundle.verify(self.directory)['prior_generation'], OLD)
        with tarfile.open(self.directory / 'auth-data.tar') as archive:
            self.assertEqual(archive.extractfile(archive.getmembers()[0]).read(), b'original\x00bytes')

    def test_missing_roles_or_provider_files_never_complete(self):
        for name in ('auth.globals.sql', 'app.globals.sql', 'auth-certs.tar', 'auth-data.tar'):
            with self.subTest(name=name):
                path = self.directory / name
                payload = path.read_bytes()
                path.unlink()
                with self.assertRaises(ValueError):
                    bundle.verify(self.directory)
                path.write_bytes(payload)

    def test_partial_dump_checksum_wrong_manifest_and_stale(self):
        with self.assertRaises(ValueError):
            bundle.verify(self.directory, expected_hash='f' * 64)
        with self.assertRaises(ValueError):
            bundle.verify(self.directory, max_age=1, now=self.manifest['started_unix'] + 2)
        with (self.directory / 'app.dump').open('ab') as output:
            output.write(b'partial')
        with self.assertRaises(ValueError):
            bundle.verify(self.directory)

    def test_outer_archive_rejects_path_escape_link_and_duplicate(self):
        for kind in ('escape', 'link', 'duplicate'):
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode='w:') as archive:
                item = tarfile.TarInfo('../app.dump' if kind == 'escape' else 'app.dump')
                item.size = 1
                if kind == 'link':
                    item.type = tarfile.SYMTYPE
                    item.linkname = '/etc/shadow'
                archive.addfile(item, io.BytesIO(b'x'))
                if kind == 'duplicate':
                    archive.addfile(item, io.BytesIO(b'x'))
            data.seek(0)
            with self.assertRaises(ValueError):
                bundle.unpack(data, self.root / kind)

    def test_fetch_requires_exact_snapshot_hash_and_new_destination(self):
        with patch.object(restore, 'command', return_value=json.dumps([{'id': 'b' * 64, 'tags': ['animus-coherent']}]).encode()):
            with self.assertRaises(ValueError):
                restore.fetch('a' * 64, 'c' * 64, Path('/bundle'), self.root / 'new')
        with self.assertRaises(ValueError):
            restore.fetch('latest', 'c' * 64, Path('/bundle'), self.root / 'new')
        with self.assertRaises(ValueError):
            restore.fetch('a' * 64, bundle.sha(self.directory / 'manifest.json'), Path('/bundle'), self.directory)

    def test_restore_refuses_missing_fence_and_reused_generation_before_docker(self):
        digest = bundle.sha(self.directory / 'manifest.json')
        fresh = control(self.root / 'external.env')
        with patch.object(restore, 'command', side_effect=AssertionError('Docker must not run')):
            with self.assertRaises(FileNotFoundError):
                restore.import_databases(self.directory, digest, self.root / 'target.json', fresh, self.root / 'absent')
            control(fresh, OLD)
            with self.assertRaises(ValueError):
                restore.import_databases(self.directory, digest, self.root / 'target.json', fresh, self.root / 'absent')

    def test_foreign_restore_container_denies_and_no_open_command(self):
        digest = bundle.sha(self.directory / 'manifest.json')
        fresh = control(self.root / 'external.env')
        fence = private(self.root / 'fence.json', json.dumps({'generation': FRESH, 'backup_manifest_sha256': digest,
            'old_host_fenced': True, 'evidence_sha256': 'e' * 64}))
        target = private(self.root / 'target.json', json.dumps({'project': 'animus-restore-test', 'databases': {'app': {}, 'auth': {}}}))
        calls = []
        def run(args):
            calls.append(args)
            if args[1] == 'ps':
                return b'one\ntwo\n'
            return json.dumps([{'Config': {'Labels': {'com.docker.compose.service': 'foreign'}}}]).encode()
        with patch.object(restore, 'command', side_effect=run):
            with self.assertRaises(ValueError):
                restore.import_databases(self.directory, digest, target, fresh, fence)
        self.assertFalse(any('start' in row or 'recovery-open' in row for row in calls))

    def test_local_capture_and_fleet_success_do_not_mark_protected(self):
        status = self.root / 'status.json'
        copy_module.status(status, 'local-only', capture_id=self.manifest['capture_id'])
        document = json.loads(status.read_text())
        self.assertFalse(document['protected'])
        self.assertEqual(document['timestamp'], '')
        with patch.dict('os.environ', {'RESTIC_REPOSITORY': str(self.root / 'local')}, clear=True):
            with patch.object(copy_module, 'run', side_effect=AssertionError('no restic')):
                with self.assertRaises(ValueError):
                    copy_module.protect({'staging': str(self.directory), 'status': str(status), 'max_age_seconds': 86400})

    def test_staged_files_are_byte_exact_and_inactive(self):
        destination = self.root / 'inactive'
        restore.stage_files(self.directory, bundle.sha(self.directory / 'manifest.json'), destination)
        self.assertEqual((destination / 'auth-data/auth-data/synthetic').read_bytes(), b'original\x00bytes')
        self.assertTrue((destination / 'private-config/stack.env').exists())
        self.assertEqual(json.loads((destination / 'staged-closed.json').read_text())['state'], 'files-staged-closed')
        with self.assertRaises(ValueError):
            restore.stage_files(self.directory, bundle.sha(self.directory / 'manifest.json'), destination)

    def test_failed_current_capture_cannot_protect_old_fresh_bundle(self):
        status = self.root / 'animus.json'
        copy_module.status(status, 'failed')
        value = {'staging': str(self.directory), 'status': str(status), 'max_age_seconds': 86400}
        with patch.dict('os.environ', {'RESTIC_REPOSITORY': 'b2:synthetic:repo'}, clear=True):
            with patch.object(copy_module, 'run', side_effect=AssertionError('must not upload failed capture')):
                with self.assertRaises(ValueError):
                    copy_module.protect(value)

    def test_supported_operator_path_refuses_capture_hold_before_docker(self):
        fresh = control(self.root / 'external.env')
        hold = private(self.root / 'capture-hold.json', '{}')
        with patch.object(compose.subprocess, 'run', side_effect=AssertionError('must not run Docker')):
            for args in (['up', '-d'], ['run', '--rm', 'migrate'], ['restart', 'api']):
                with self.assertRaises(ValueError):
                    compose.launch(args, stack=self.root, control=fresh, hold=hold)


    def test_invalid_collector_config_clears_old_green_status(self):
        status = self.root / 'animus.json'
        copy_module.status(status, 'protected', timestamp='old-success')
        bad = private(self.root / 'collector.json', '{')
        with patch.object(copy_module, 'DEFAULT_STATUS', status):
            with patch.object(sys, 'argv', ['restic-copy.py', 'collect', '--config', str(bad)]):
                with patch('sys.stderr', io.StringIO()):
                    self.assertEqual(copy_module.main(), 1)
        self.assertFalse(json.loads(status.read_text())['protected'])



class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.spool = self.root / 'spool'
        self.spool.mkdir(mode=0o700)
        self.subject = capture.Capture({'project': 'ct-animus', 'spool': str(self.spool), 'reserve_bytes': 268435456,
                                        'stop_seconds': 30, 'external_control': str(self.root / 'external.env')})
        self.subject.containers = {name: {'Id': name, 'HostConfig': {'RestartPolicy': {'Name': 'unless-stopped'}}}
                                   for name in capture.SERVICES}

    def tearDown(self):
        self.temp.cleanup()

    def test_quiescence_failure_retains_hold_and_no_dump(self):
        record = {'phase': 'holding'}
        calls = []
        def run(args):
            calls.append(args)
            if args[1] == 'stop':
                raise ValueError('stop failed')
            return b''
        with patch.object(capture, 'command', side_effect=run):
            with self.assertRaises(ValueError):
                self.subject.quiesce(record)
        self.assertTrue(self.subject.hold.exists())
        self.assertFalse(any('pg_dump' in row for row in calls))

    def test_uncertain_sigkill_retains_hold(self):
        with patch.object(capture, 'command', return_value=b''):
            with patch.object(self.subject, 'inspect', return_value={'State': {'Running': False, 'ExitCode': 137},
                       'HostConfig': {'RestartPolicy': {'Name': 'no'}}}):
                with self.assertRaises(ValueError):
                    self.subject.quiesce({'phase': 'holding'})
        self.assertTrue(self.subject.hold.exists())

    def test_disk_full_before_runtime_mutation(self):
        with patch.object(capture.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 0})()):
            with self.assertRaises(ValueError):
                self.subject.headroom()

    def test_foreign_project_container_rejected(self):
        self.subject.containers = {}
        with patch.object(capture, 'command', return_value=b'foreign'):
            with patch.object(self.subject, 'inspect', return_value={'Config': {'Labels': {'com.docker.compose.service': 'stranger'}}}):
                with self.assertRaises(ValueError):
                    self.subject.identify()

    def test_environment_capture_excludes_boot_generation_and_keeps_secrets(self):
        source = private(self.root / 'stack.env', '\n'.join([
            'API_IMAGE=synthetic', 'APP_ADMIN_DB_PASSWORD=synthetic', 'APP_RUNTIME_DB_PASSWORD=synthetic',
            'AUTH_ADMIN_DB_PASSWORD=synthetic', 'AUTH_DB_PASSWORD=synthetic', 'AUTHENTIK_SECRET_KEY=synthetic',
            'OIDC_TRUST_FILE=/synthetic', 'HUB_RECOVERY_GENERATION=' + OLD,
            'HUB_RECOVERY_GENERATION_FILE=/old/external.env']) + '\n')
        output = self.root / 'config.tar'
        self.subject.archive([(source, 'stack.env')], output, env=True)
        with tarfile.open(output) as archive:
            value = archive.extractfile('stack.env').read()
        self.assertNotIn(OLD.encode(), value)
        self.assertNotIn(b'HUB_RECOVERY_GENERATION', value)
        self.assertIn(b'AUTHENTIK_SECRET_KEY=synthetic', value)

    def test_external_control_inside_source_tree_rejected(self):
        control(self.root / 'external.env')
        with self.assertRaises(ValueError):
            self.subject.archive([(self.root, 'tree')], self.spool / 'bad.tar')

    def test_partial_dump_and_midstream_disk_full_keep_hold(self):
        private(self.subject.hold, '{"phase":"quiesced"}')
        class Process:
            def __init__(self):
                self.stdout = io.BytesIO(b'partial dump')
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def wait(self):
                return 1
            def kill(self):
                pass
        with patch.object(capture.subprocess, 'Popen', return_value=Process()):
            with self.assertRaises(ValueError):
                self.subject.dump(['fake'], self.root / 'partial.dump')
        with patch.object(capture.subprocess, 'Popen', return_value=Process()):
            with patch.object(self.subject, 'headroom', side_effect=ValueError('full')):
                with self.assertRaises(ValueError):
                    self.subject.dump(['fake'], self.root / 'disk-full.dump')
        self.assertTrue(self.subject.hold.exists())
        self.assertFalse(any(path.name == 'manifest.json' for path in self.root.rglob('*')))

    def test_successful_resume_restores_exact_retry_policy(self):
        record = {'containers': {name: {'id': name, 'restart': {'Name': 'on-failure', 'MaximumRetryCount': 7}}
                                 for name in capture.WRITERS}}
        calls = []
        with patch.object(capture, 'command', side_effect=lambda args: calls.append(args) or b''):
            with patch.object(self.subject, 'inspect', side_effect=lambda identifier: {'Id': identifier, 'State': {'Running': False}}):
                self.subject.resume(record)
        self.assertEqual(sum('--restart=on-failure:7' in row for row in calls), 3)



class ProviderVolumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.subject = capture.Capture({'project': 'ct-animus', 'spool': str(self.root), 'stop_seconds': 30,
            'config_sources': [{'name': 'blueprints', 'source': str(self.root / 'blueprints')}]})
        self.items = {}
        self.volumes = {}
        self.calls = []
        for name in ('auth-data', 'auth-templates', 'auth-certs'):
            self.volumes[name] = {'Name': name, 'Mountpoint': str(self.root / name), 'Labels': {
                'com.docker.compose.project': 'ct-animus', 'com.docker.compose.volume': name}}
        for service in capture.WRITERS:
            mounts = []
            if service != 'api':
                for name, destination in [('auth-data', '/data'), ('auth-templates', '/templates')]:
                    mounts.append({'Type': 'volume', 'Name': name, 'Source': str(self.root / name),
                                   'Destination': destination, 'RW': True})
            if service == 'auth-worker':
                mounts += [{'Type': 'volume', 'Name': 'auth-certs', 'Source': str(self.root / 'auth-certs'),
                            'Destination': '/certs', 'RW': True},
                           {'Type': 'bind', 'Source': str(self.root / 'blueprints'),
                            'Destination': '/blueprints/ct-animus', 'RW': False}]
            self.items[service] = {'Id': service, 'Config': {'Labels': {'com.docker.compose.project': 'ct-animus',
                'com.docker.compose.service': service}}, 'Mounts': mounts,
                'State': {'Running': False, 'ExitCode': 0}, 'HostConfig': {'RestartPolicy': {'Name': 'no'}}}
        self.subject.containers = self.items.copy()

    def tearDown(self):
        self.temp.cleanup()

    def command(self, args):
        self.calls.append(args)
        if args == ['docker', 'ps', '-aq']:
            return '\n'.join(self.items).encode()
        if args[:3] == ['docker', 'volume', 'inspect']:
            return json.dumps([self.volumes[args[3]]]).encode()
        if args[1] in ('stop', 'update'):
            return b''
        raise AssertionError('unexpected fake command')

    def foreign(self, writable):
        self.items['foreign'] = {'Id': 'foreign', 'Mounts': [{'Type': 'volume', 'Name': 'auth-data',
            'Source': str(self.root / 'auth-data'), 'Destination': '/unrelated', 'RW': writable}],
            'State': {'Running': False}, 'NetworkSettings': {'Networks': {'other-network': {}}}}

    def test_foreign_even_stopped_other_network_consumer_blocks_copy_without_mutation(self):
        for writable in (True, False):
            with self.subTest(writable=writable):
                self.foreign(writable)
                with patch.object(capture, 'command', side_effect=self.command):
                    with patch.object(self.subject, 'inspect', side_effect=lambda identifier: self.items[identifier]):
                        with patch.object(self.subject, 'archive', side_effect=AssertionError('must not copy')):
                            with self.assertRaises(ValueError):
                                self.subject.copy_provider('auth-data', self.root / 'never.tar')
                self.assertFalse(any('foreign' in row and row[1] in ('stop', 'update', 'start') for row in self.calls))
        self.assertFalse((self.root / 'never.tar').exists())

    def test_foreign_bind_of_provider_path_or_parent_is_also_denied(self):
        for source in (self.root / 'auth-data', self.root):
            self.foreign(True)
            self.items['foreign']['Mounts'] = [{'Type': 'bind', 'Source': str(source),
                                              'Destination': '/foreign', 'RW': True}]
            with patch.object(capture, 'command', side_effect=self.command):
                with patch.object(self.subject, 'inspect', side_effect=lambda identifier: self.items[identifier]):
                    with self.assertRaises(ValueError):
                        self.subject.provider_volumes()

    def test_foreign_discovered_after_stop_preserves_hold(self):
        self.foreign(True)
        with patch.object(capture, 'command', side_effect=self.command):
            with patch.object(self.subject, 'inspect', side_effect=lambda identifier: self.items[identifier]):
                with patch.object(self.subject, 'sql', return_value='0'):
                    with self.assertRaises(ValueError):
                        self.subject.quiesce({'phase': 'holding'})
        self.assertTrue(self.subject.hold.exists())
        self.assertFalse(any('foreign' in row and row[1] in ('stop', 'update', 'start') for row in self.calls))

    def test_unexpected_persistent_mount_or_wrong_rw_is_rejected(self):
        for change in ('extra', 'readonly'):
            original = copy.deepcopy(self.items['auth-server']['Mounts'])
            if change == 'extra':
                self.items['auth-server']['Mounts'].append({'Type': 'bind', 'Source': '/unaccounted',
                    'Destination': '/unaccounted', 'RW': True})
            else:
                self.items['auth-server']['Mounts'][0]['RW'] = False
            with patch.object(capture, 'command', side_effect=self.command):
                with patch.object(self.subject, 'inspect', side_effect=lambda identifier: self.items[identifier]):
                    with self.assertRaises(ValueError):
                        self.subject.provider_volumes()
            self.items['auth-server']['Mounts'] = original

    def test_only_known_topology_and_consumers_pass(self):
        with patch.object(capture, 'command', side_effect=self.command):
            with patch.object(self.subject, 'inspect', side_effect=lambda identifier: self.items[identifier]):
                self.assertEqual(set(self.subject.provider_volumes(held=True)), {'auth-data', 'auth-templates', 'auth-certs'})


if __name__ == '__main__':
    unittest.main()
