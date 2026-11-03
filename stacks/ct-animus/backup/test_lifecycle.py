#!/usr/bin/env python3
"""Focused startup/capture interleavings and secret-free monitor failures."""
import contextlib
import copy
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
import capture
import compose
import lifecycle
import monitor

G = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.control = self.root / 'recovery.env'
        self.write_control(G)
        self.startup = self.root / 'startup-hold'
        self.intent = self.root / 'stop-intent'
        self.hold = self.root / 'capture-hold.json'
        self.context = contextlib.ExitStack()
        self.addCleanup(self.context.close)
        for name, path in (('STARTUP_HOLD', self.startup), ('STOP_INTENT', self.intent)):
            self.context.enter_context(patch.object(lifecycle, name, path))
        self.items = {}
        for number, name in enumerate((*lifecycle.WRITERS, 'app-postgres', 'auth-postgres'), 1):
            self.items[name] = {'Id': str(number) * 64, 'Image': 'sha256:' + 'a' * 64,
                'Config': {'Env': ['HUB_RECOVERY_MODE=enforced', 'HUB_RECOVERY_PROFILE=native-workout-v1',
                                   'HUB_RECOVERY_GENERATION=' + G],
                           'Labels': {'com.docker.compose.project': 'ct-animus', 'com.docker.compose.service': name}},
                'HostConfig': {'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0}},
                'State': {'Running': False, 'StartedAt': '2026-11-03T12:00:00Z', 'Health': {'Status': 'healthy'}}}

    def write_control(self, generation):
        self.control.write_text('HUB_RECOVERY_MODE=enforced\nHUB_RECOVERY_PROFILE=native-workout-v1\n'
                                'HUB_RECOVERY_GENERATION=' + generation + '\n')
        self.control.chmod(0o600)

    def gate(self, row=None, **options):
        return lifecycle.gate(self.control, lambda generation: row if row is not None else {'matched': True, 'state': 'open'},
                              api=self.items['api'], **options)

    def test_closed_and_open_match_without_writing_control(self):
        before = self.control.read_bytes()
        for state in ('open', 'closed'):
            self.assertEqual(self.gate({'matched': True, 'state': state}), G)
        self.assertEqual(self.control.read_bytes(), before)

    def test_missing_duplicate_unknown_or_stale_database_control(self):
        for row in ({}, {'matched': False, 'state': 'open'}, {'matched': True, 'state': 'unknown'}):
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.gate(row)

    def test_missing_stale_control_or_snapshotted_environment_denies(self):
        self.write_control(OTHER)
        with self.assertRaises(ValueError):
            self.gate({'matched': True, 'state': 'open'})
        self.control.unlink()
        with self.assertRaises(OSError):
            self.gate()

    def test_independent_holds_and_unsafe_policy_deny(self):
        for path in (self.startup, self.intent):
            path.touch()
            with self.assertRaises(ValueError):
                self.gate()
            path.unlink()
        self.items['api']['HostConfig']['RestartPolicy']['Name'] = 'unless-stopped'
        with self.assertRaises(ValueError):
            lifecycle.snapshot(self.items)

    def test_private_adapter_missing_or_malformed_blocks_and_generation_uses_stdin(self):
        adapter = self.root / 'private-adapter'
        with patch.object(lifecycle, 'STATUS_ADAPTER', adapter):
            with self.assertRaises(OSError): lifecycle.database_status(self.items, G)
            adapter.write_text('separately reviewed private implementation')
            adapter.chmod(0o700)
            with patch.object(lifecycle, 'run', return_value=b'{"matched":true,"state":"closed"}') as run:
                self.assertEqual(lifecycle.database_status(self.items, G), {'matched': True, 'state': 'closed'})
            self.assertEqual(run.call_args.kwargs['input'], (G + '\n').encode())
            self.assertNotIn(G, run.call_args.args[0])
            with patch.object(lifecycle, 'run', return_value=b'not-json'):
                with self.assertRaises(ValueError): lifecycle.database_status(self.items, G)
        for value in (None, [], {'matched': True, 'state': 'open', 'extra': True}):
            with self.assertRaises(ValueError): self.gate(value if value is not None else [])

    def test_lock_free_stop_drains_without_latching_clean_reboot(self):
        with patch.object(lifecycle, 'stop_owners') as stop:
            self.assertEqual(compose.launch(['stop-response-owners'], hold=self.hold), 0)
        stop.assert_called_once()
        self.assertFalse(self.intent.exists())

    def test_contended_stop_persists_intent_before_wait_and_drain(self):
        order = []
        def lock(fd, operation):
            if operation & fcntl.LOCK_NB:
                order.append('busy')
                raise BlockingIOError()
            self.assertTrue(self.intent.exists())
            order.append('wait')
        with patch.object(compose.fcntl, 'flock', side_effect=lock), \
             patch.object(lifecycle, 'stop_owners', side_effect=lambda: order.append('drain')):
            self.assertEqual(compose.launch(['stop-response-owners'], hold=self.hold), 0)
        self.assertEqual(order, ['busy', 'wait', 'drain'])

    def test_failed_stop_retains_intent_even_without_contention(self):
        with patch.object(lifecycle, 'stop_owners', side_effect=ValueError('uncertain drain')):
            with self.assertRaises(ValueError):
                compose.launch(['stop-response-owners'], hold=self.hold)
        self.assertTrue(self.intent.exists())

    def test_contended_stop_persistence_failure_still_waits_and_drains(self):
        order = []
        def lock(fd, operation):
            if operation & fcntl.LOCK_NB: raise BlockingIOError()
            order.append('wait')
        def persist():
            order.append('persist-failed')
            raise OSError('no space')
        with patch.object(compose.fcntl, 'flock', side_effect=lock), \
             patch.object(lifecycle, 'persist_stop', side_effect=persist), \
             patch.object(lifecycle, 'stop_owners', side_effect=lambda: order.append('drain')):
            with self.assertRaises(OSError): compose.launch(['stop-response-owners'], hold=self.hold)
        self.assertEqual(order, ['persist-failed', 'wait', 'drain'])

    def test_post_start_gate_failure_and_intent_enospc_still_drains_started_owners(self):
        def command(args, *a, **k):
            if '--force-recreate' in args:
                for name in lifecycle.WRITERS: self.items[name]['State']['Running'] = True
            return 0
        status = {'matched': True, 'state': 'closed'}
        with patch.object(lifecycle, 'stop_owners') as stop, \
             patch.object(compose, 'compose', side_effect=command), \
             patch.object(lifecycle, 'inventory', return_value=self.items), \
             patch.object(lifecycle, 'database_status', side_effect=[status, status, ValueError('gate changed')]), \
             patch.object(lifecycle, 'persist_stop', side_effect=OSError('no space')):
            with self.assertRaises(OSError): compose.boot(self.root, self.control, self.hold)
        self.assertEqual(stop.call_count, 2)

    def test_boot_mismatch_never_starts_response_owners_and_latches_failure(self):
        calls = []
        with patch.object(lifecycle, 'stop_owners') as stop, \
             patch.object(compose, 'compose', side_effect=lambda args, *a, **k: calls.append(args) or 0), \
             patch.object(lifecycle, 'inventory', return_value=self.items), \
             patch.object(lifecycle, 'database_status', return_value={'matched': False, 'state': 'open'}):
            with self.assertRaises(ValueError):
                compose.boot(self.root, self.control, self.hold)
        self.assertEqual(stop.call_count, 1)
        self.assertEqual(len(calls), 1)
        self.assertNotIn('--force-recreate', calls[0])
        self.assertTrue(self.intent.exists())

    def test_current_closed_boot_force_recreates_only_after_handshake(self):
        order = []
        def command(args, *a, **k):
            order.append(args)
            if '--force-recreate' in args:
                self.assertIn('handshake', order)
                for name in lifecycle.WRITERS:
                    self.items[name]['State']['Running'] = True
            return 0
        def query(*args):
            order.append('handshake')
            return {'matched': True, 'state': 'closed'}
        with patch.object(lifecycle, 'stop_owners'), patch.object(compose, 'compose', side_effect=command), \
             patch.object(lifecycle, 'inventory', return_value=self.items), patch.object(lifecycle, 'database_status', side_effect=query):
            self.assertEqual(compose.boot(self.root, self.control, self.hold), 0)
        self.assertEqual(sum(isinstance(item, list) and '--force-recreate' in item for item in order), 1)

    def test_default_compose_up_and_operator_response_start_cannot_bypass_gate(self):
        for args in (['up', '-d'], ['--profile', 'commissioning', 'up', '-d'], ['start'],
                     ['run', '--rm', 'api'], ['restart', 'auth-worker']):
            with self.subTest(args=args), patch.object(compose, 'compose', side_effect=AssertionError('bypass')):
                with self.assertRaises(ValueError):
                    compose.launch(args, stack=self.root, control=self.control, hold=self.hold)

    def resume_fixture(self):
        subject = capture.Capture({'spool': str(self.root), 'external_control': str(self.control), 'stop_seconds': 30})
        record = {'phase': 'complete-local', 'generation': G, 'containers': lifecycle.snapshot(self.items)}
        subject.save_hold(record)
        by_id = {item['Id']: item for item in self.items.values()}
        return subject, record, by_id

    def test_capture_resume_keeps_no_restart_and_checks_exact_owners(self):
        subject, record, by_id = self.resume_fixture()
        calls = []
        def command(args):
            calls.append(args)
            by_id[args[-1]]['State']['Running'] = True
            return b''
        with patch.object(subject, 'inspect', side_effect=lambda identifier: copy.deepcopy(by_id[identifier])), \
             patch.object(lifecycle, 'database_status', return_value={'matched': True, 'state': 'open'}), patch.object(capture, 'command', side_effect=command):
            subject.resume(record)
        self.assertEqual([row[1] for row in calls], ['start'] * 3)
        self.assertTrue(all(item['HostConfig']['RestartPolicy']['Name'] == 'no' for item in by_id.values()))

    def test_stop_and_capture_containment_attempt_every_stop_and_verify_after_errors(self):
        identifiers = [self.items[name]['Id'] for name in lifecycle.WRITERS]
        by_id = {item['Id']: item for item in self.items.values()}
        for path in ('stop', 'capture'):
            for operation in ('update', 'stop'):
                for error in (ValueError('failure'), subprocess.TimeoutExpired('docker', 10)):
                    with self.subTest(path=path, operation=operation, error=type(error).__name__):
                        self.intent.unlink(missing_ok=True)
                        calls, inspections = [], []
                        def command(args, **options):
                            if args[1] == 'ps': return ('\n'.join(identifiers) + '\n').encode()
                            calls.append(args)
                            if args[1] == operation and args[-1] == identifiers[0]: raise error
                            return b''
                        def inspect(identifier):
                            inspections.append(identifier)
                            return copy.deepcopy(by_id[identifier])
                        with patch.object(lifecycle, 'run', side_effect=command), \
                             patch.object(lifecycle, 'inspect', side_effect=inspect):
                            if path == 'stop':
                                with self.assertRaises(ValueError): lifecycle.stop_owners()
                                self.assertEqual({item: inspections.count(item) for item in identifiers},
                                                 {item: 2 for item in identifiers})
                            else:
                                subject, record, _ = self.resume_fixture()
                                with patch.object(subject, 'resume', side_effect=subprocess.TimeoutExpired('resume', 10)):
                                    with self.assertRaises(subprocess.TimeoutExpired): subject.resume_or_contain(record)
                                self.assertTrue(subject.hold.exists())
                                self.assertEqual(sorted(inspections), sorted(identifiers))
                        self.assertEqual(sorted(row[-1] for row in calls if row[1] == 'update'), sorted(identifiers))
                        self.assertEqual(sorted(row[-1] for row in calls if row[1] == 'stop'), sorted(identifiers))
                        self.assertTrue(self.intent.exists())

    def test_containment_command_budget_fits_unit_stop_deadline(self):
        # 32 discovery inspections are serial; update/stop/verification phases
        # are parallel. Include the initial list call and a 15s stop margin.
        worst_case = 10 + 32 * 10 + 10 + (60 + 15) + 10
        self.assertLess(worst_case, 600)
        identifiers = [self.items[name]['Id'] for name in lifecycle.WRITERS]
        bounds = []
        def command(args, **options):
            bounds.append(options['timeout'])
            return b''
        with patch.object(lifecycle, 'run', side_effect=command), \
             patch.object(lifecycle, 'inspect', return_value={'State': {'Running': False},
                                                           'HostConfig': {'RestartPolicy': {'Name': 'no'}}}):
            lifecycle.contain_ids(identifiers, stop_seconds=30)
        self.assertEqual(sorted(bounds), sorted([10, 45] * 3))

    def test_capture_stop_intent_or_generation_change_after_first_start_aborts(self):
        for fault in ('stop', 'generation', 'database', 'startup-hold', 'environment'):
            with self.subTest(fault=fault):
                self.intent.unlink(missing_ok=True)
                self.startup.unlink(missing_ok=True)
                self.write_control(G)
                for item in self.items.values():
                    item['State']['Running'] = False
                    item['Config']['Env'][-1] = 'HUB_RECOVERY_GENERATION=' + G
                subject, record, by_id = self.resume_fixture()
                calls = []
                def command(args):
                    calls.append(args)
                    by_id[args[-1]]['State']['Running'] = True
                    if fault == 'stop': lifecycle.persist_stop()
                    if fault == 'generation': self.write_control(OTHER)
                    if fault == 'startup-hold': self.startup.touch()
                    if fault == 'environment': self.items['api']['Config']['Env'][-1] += 'changed'
                    return b''
                def query(*args):
                    return {'matched': not (fault == 'database' and calls), 'state': 'open'}
                with patch.object(subject, 'inspect', side_effect=lambda identifier: copy.deepcopy(by_id[identifier])), \
                     patch.object(lifecycle, 'database_status', side_effect=query), patch.object(capture, 'command', side_effect=command):
                    with self.assertRaises(ValueError): subject.resume(record)
                self.assertEqual(len(calls), 1)
                self.assertTrue(subject.hold.exists())

    def test_capture_waits_for_both_provider_healthchecks_before_api_resume(self):
        subject, record, by_id = self.resume_fixture()
        calls = []
        for name in ('auth-server', 'auth-worker'):
            self.items[name]['State']['Health']['Status'] = 'starting'
        def command(args):
            if args[-1] == self.items['api']['Id']:
                self.assertTrue(all(self.items[name]['State']['Health']['Status'] == 'healthy'
                                    for name in ('auth-server', 'auth-worker')))
            by_id[args[-1]]['State']['Running'] = True
            calls.append(args)
            return b''
        def become_ready(delay):
            for name in ('auth-server', 'auth-worker'):
                self.items[name]['State']['Health']['Status'] = 'healthy'
        with patch.object(subject, 'inspect', side_effect=lambda identifier: copy.deepcopy(by_id[identifier])), \
             patch.object(lifecycle, 'database_status', return_value={'matched': True, 'state': 'open'}), \
             patch.object(capture, 'command', side_effect=command), patch.object(capture.time, 'sleep', side_effect=become_ready) as sleep:
            subject.resume(record)
        sleep.assert_called_once()
        self.assertEqual(len(calls), 3)

    def test_capture_provider_timeout_or_stop_during_readiness_wait_never_starts_api(self):
        for fault in ('timeout', 'stop'):
            self.intent.unlink(missing_ok=True)
            for item in self.items.values(): item['State']['Running'] = False
            subject, record, by_id = self.resume_fixture()
            for name in ('auth-server', 'auth-worker'):
                self.items[name]['State']['Health']['Status'] = 'starting'
            calls = []
            def command(args):
                by_id[args[-1]]['State']['Running'] = True
                calls.append(args)
                return b''
            with patch.object(subject, 'inspect', side_effect=lambda identifier: copy.deepcopy(by_id[identifier])), \
                 patch.object(lifecycle, 'database_status', return_value={'matched': True, 'state': 'open'}), \
                 patch.object(capture, 'command', side_effect=command), \
                 patch.object(capture.time, 'monotonic', side_effect=[0, 181] if fault == 'timeout' else [0, 1]), \
                 patch.object(capture.time, 'sleep', side_effect=lambda delay: lifecycle.persist_stop()):
                with self.assertRaises(ValueError): subject.resume(record)
            self.assertEqual(len(calls), 2)
            self.assertTrue(subject.hold.exists())

    def test_read_receipt_expiry_restart_and_generation_fail_closed(self):
        api = {'id': 'a' * 64, 'started': 'start-1'}
        receipt = {'version': 1, 'success': True, 'checked_unix': 100, 'expires_unix': 200,
                   'api_container_id': api['id'], 'api_started_at': api['started'], 'generation': G,
                   'evidence_sha256': 'b' * 64}
        path = self.root / 'read.json'
        path.write_text(json.dumps(receipt))
        path.chmod(0o600)
        self.assertEqual(monitor.recent_read(path, api, G, 150), (True, 200))
        for changed_api, generation, now in ((api, G, 200), ({**api, 'started': 'start-2'}, G, 150), (api, OTHER, 150)):
            self.assertEqual(monitor.recent_read(path, changed_api, generation, now), (False, 0))

    def test_monitor_missing_stale_future_or_malformed_remote_observation_denies(self):
        value = monitor.empty()
        value.update(observed=True, timestamp_unix=100)
        self.assertEqual(monitor.validate(value, 101), value)
        for changed in ({**value, 'observed': False}, {**value, 'timestamp_unix': 102},
                        {**value, 'timestamp_unix': -100}, {**value, 'database_ready': 'true'}):
            with self.assertRaises(ValueError): monitor.validate(changed, 101)

    def test_monitor_failed_collect_and_stale_collector_clear_green(self):
        status = self.root / 'operations.json'
        good = {**monitor.empty(), 'observed': True, 'timestamp_unix': 1, 'receiver_storage_ok': True}
        with patch.object(monitor, 'STATUS', status):
            for action in ('collect', 'freshness'):
                status.write_text(json.dumps(good))
                with patch.object(sys, 'argv', ['monitor.py', action]), \
                     patch.object(monitor, 'collect', side_effect=ValueError('SSH unavailable')):
                    with self.assertRaises(ValueError): monitor.main()
                result = json.loads(status.read_text())
                self.assertFalse(result['observed'])
                self.assertFalse(result['authenticated_read_recent'])

    def test_read_begin_immediately_removes_old_success(self):
        receipt = self.root / 'read.json'
        receipt.write_text('old successful evidence')
        with patch.object(monitor, 'READINESS', receipt), patch.object(sys, 'argv', ['monitor.py', 'read-begin']):
            monitor.main()
        self.assertFalse(receipt.exists())

    def test_storage_thresholds_include_capture_reserve(self):
        for free, used, expected in ((2 * monitor.RESERVE, 3 * monitor.RESERVE, True),
                                      (monitor.RESERVE - 1, 1, False),
                                      (monitor.RESERVE, 9 * monitor.RESERVE, False)):
            usage = type('Usage', (), {'free': free, 'used': used, 'total': free + used})()
            with patch.object(monitor.shutil, 'disk_usage', return_value=usage):
                self.assertEqual(monitor.storage_ok(self.root), expected)

    def test_backup_check_sources_export_file_and_passes_literal_subset(self):
        source = Path(__file__).parents[2] / 'ct-backup/scripts/backup-check.sh'
        env = self.root / 'b2.env'
        env.write_text('export RESTIC_REPOSITORY=synthetic\nexport RESTIC_PASSWORD_FILE=/synthetic\n')
        fake = self.root / 'restic'
        fake.write_text('#!/bin/bash\n[[ "$RESTIC_REPOSITORY" == synthetic && "$RESTIC_PASSWORD_FILE" == /synthetic ]]\n'
                        '[[ "$#" == 3 && "$1" == check && "$2" == --read-data-subset && "$3" == "5%" ]]\n')
        fake.chmod(0o700)
        script = self.root / 'check.sh'
        script.write_text(source.read_text().replace('/etc/restic/b2.env', str(env)).replace('/usr/bin/restic', str(fake)))
        self.assertEqual(subprocess.run(['bash', str(script)], capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
