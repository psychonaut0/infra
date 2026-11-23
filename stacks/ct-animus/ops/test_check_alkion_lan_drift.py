#!/usr/bin/env python3
"""Offline tests of ops/check-alkion-lan-drift; no container or database.

The live observation is simulated from the script's own expectation, so every test
starts from "no drift" and changes one thing a residual case (spec decision 6), an
admin or a stray apply could change. The isolated campaign
(qualification/p1/redesign/run.sh) runs the same script against a real instance.
"""
from copy import deepcopy
import hashlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest

HERE = Path(__file__).resolve().parent
STACK = HERE.parent
loader = importlib.machinery.SourceFileLoader('drift', str(HERE / 'check-alkion-lan-drift'))
spec = importlib.util.spec_from_loader('drift', loader)
drift = importlib.util.module_from_spec(spec)
loader.exec_module(drift)

BLUEPRINT = STACK / 'blueprints' / 'alkion-lan.yaml'
ROLLBACK = STACK / 'rollback' / 'alkion-lan-20261123-rollback.yaml'
RAW = BLUEPRINT.read_bytes()
SHA512 = hashlib.sha512(RAW).hexdigest()
EXPECTED = drift.expected_state(drift.load_blueprint(RAW.decode()))
NORMAL, AUTHZ = 'animus-lan-normal', 'animus-lan-authorization'


def live(expected=EXPECTED, sha=SHA512):
    """The observation a database matching `expected` returns, in the script's SQL shape."""
    rows = [[flow, stage, order, v['evaluate_on_plan'], v['re_evaluate_policies'], v['invalid_response_action'],
             v['policy_engine_mode']] for (flow, stage, order), v in expected['stage_bindings'].items()]
    pbs = [[kind, list(key) if kind == 'fsb' else key, policy, order, v['enabled'], v['negate'], v['failure_result'],
            v['timeout'], False, False, False]
           for ((kind, key), policy, order), v in expected['policy_bindings'].items()]
    apps = []
    for slug, values in expected['applications'].items():
        provider = expected['providers'][values['provider']]
        apps.append([slug, values['policy_engine_mode'], provider['client_id'], provider['authentication_flow'],
                     provider['authorization_flow'], values['provider']])
    return {'read_only': 'on', 'transaction_read_only': 'on', 'flows': list(expected['flows']),
            'stage_bindings': rows, 'policy_bindings': pbs, 'applications': apps,
            'expressions': dict(expected['expressions']), 'mappings': dict(expected['mappings']),
            'brands': [{name: None for name in drift.BRAND_FLOWS} | {'flow_authentication': 'default-authentication-flow'}],
            'instances': [['ct-animus/alkion-lan.yaml', dict(drift.PROFILE), True, 'successful', sha]]}


def find(rows, flow, stage):
    return next(row for row in rows if row[0] == flow and row[1] == stage)


def gate(observed, policy):
    return next(row for row in observed['policy_bindings'] if row[2] == policy)


class Expectation(unittest.TestCase):
    def test_derived_from_the_blueprint_not_a_list(self):
        normal = {(stage, order): v for (flow, stage, order), v in EXPECTED['stage_bindings'].items() if flow == NORMAL}
        gated = {'evaluate_on_plan': False, 're_evaluate_policies': True, 'invalid_response_action': 'retry',
                 'policy_engine_mode': 'all'}
        self.assertEqual(normal, {
            ('animus-lan-normal-identification', 10): gated, ('animus-lan-login', 50): gated,
            ('animus-lan-normal-denied', 60): gated,
            ('animus-lan-totp-validation', 30): {'evaluate_on_plan': True, 're_evaluate_policies': False,
                                                 'invalid_response_action': 'retry', 'policy_engine_mode': 'any'}})
        self.assertFalse([key for key in EXPECTED['stage_bindings'] if key[0] == AUTHZ])
        pinned = {'enabled': True, 'negate': False, 'failure_result': False, 'timeout': 30}
        self.assertEqual(EXPECTED['policy_bindings'], {
            (('fsb', (NORMAL, 'animus-lan-normal-identification', 10)), 'animus-lan-entry-gate', 0): pinned,
            (('fsb', (NORMAL, 'animus-lan-login', 50)), 'animus-lan-login-gate', 0): pinned,
            (('fsb', (NORMAL, 'animus-lan-normal-denied', 60)), 'animus-lan-denied-notice', 0): pinned,
            (('app', 'alkion-lan'), 'animus-lan-pkce', 0): pinned,
            (('app', 'alkion-lan'), 'animus-lan-application', 1): pinned})  # issuance-closed: absent in the profile
        self.assertEqual(EXPECTED['applications']['alkion-lan']['policy_engine_mode'], 'all')
        self.assertEqual(EXPECTED['providers']['Alkion LAN commissioning'],
                         {'client_id': 'alkion-lan', 'authentication_flow': NORMAL, 'authorization_flow': AUTHZ})
        self.assertIn('animus-lan-login-gate', EXPECTED['expressions'])
        self.assertEqual(sorted(EXPECTED['flows']), ['animus-lan-authorization', 'animus-lan-enrollment',
                                                     'animus-lan-normal', 'animus-lan-repair'])

    def test_closed_profile_keeps_the_issuance_closure(self):
        closed = drift.expected_state(drift.load_blueprint(RAW.decode()), {})
        self.assertIn((('app', 'alkion-lan'), 'animus-lan-issuance-closed', 2), closed['policy_bindings'])

    def test_rollback_expectation_is_the_deployed_state(self):
        expected = drift.expected_state(drift.load_blueprint(ROLLBACK.read_text()))
        deployed = subprocess.run(['git', '-C', str(STACK), 'show', 'c693d82:stacks/ct-animus/blueprints/alkion-lan.yaml'],
                                  capture_output=True, text=True, check=True).stdout
        before = drift.expected_state(drift.load_blueprint(deployed))
        self.assertEqual(expected['stage_bindings'], before['stage_bindings'])
        self.assertEqual(expected['policy_bindings'], before['policy_bindings'])
        self.assertEqual(expected['expressions'], before['expressions'])
        self.assertEqual(expected['applications'], before['applications'])

    def test_expressions_are_hashed_as_stored(self):
        self.assertEqual(drift.stored_hash('return False\n'), hashlib.sha256(b'return False').hexdigest())

    def test_yaml_via_container_gives_the_same_expectation(self):
        local = drift.load_blueprint(RAW.decode())
        script = drift.YAML_TO_JSON

        def runner(argv, input, **kwargs):
            self.assertEqual(argv[:4], ['docker', 'exec', '-i', 'auth-worker'])
            out = subprocess.run(['python3', '-I', '-c', script], input=input, capture_output=True, text=True, check=True)
            return subprocess.CompletedProcess(argv, 0, out.stdout, '')
        remote = drift.load_blueprint(RAW.decode(), yaml_via='auth-worker', runner=runner)
        self.assertEqual(drift.expected_state(remote), drift.expected_state(local))


class Comparison(unittest.TestCase):
    def failures(self, mutate):
        observed = deepcopy(live())
        mutate(observed)
        return drift.compare(EXPECTED, observed, SHA512)

    def test_matching_state_passes(self):
        self.assertEqual(drift.compare(EXPECTED, live(), SHA512), [])

    def test_each_residual_case_is_flagged(self):
        def set_row(rows, index, value):
            rows[index] = value
        cases = {
            'gate disabled': lambda o: gate(o, 'animus-lan-login-gate').__setitem__(4, False),
            'gate deleted': lambda o: o['policy_bindings'].remove(gate(o, 'animus-lan-entry-gate')),
            'gate negated': lambda o: gate(o, 'animus-lan-login-gate').__setitem__(5, True),
            'gate failure_result true': lambda o: gate(o, 'animus-lan-entry-gate').__setitem__(6, True),
            'gate timeout': lambda o: gate(o, 'animus-lan-login-gate').__setitem__(7, 5),
            'gate expiring': lambda o: gate(o, 'animus-lan-login-gate').__setitem__(10, True),
            're_evaluate off': lambda o: find(o['stage_bindings'], NORMAL, 'animus-lan-login').__setitem__(4, False),
            'stage engine any': lambda o: find(o['stage_bindings'], NORMAL, 'animus-lan-login').__setitem__(6, 'any'),
            'extra always-true binding': lambda o: o['policy_bindings'].append(
                ['fsb', [NORMAL, 'animus-lan-login', 50], 'always-true', 1, True, False, False, 30, False, False, False]),
            'group binding': lambda o: o['policy_bindings'].append(
                ['fsb', [NORMAL, 'animus-lan-login', 50], None, 2, True, False, False, 30, True, False, False]),
            'application binding disabled': lambda o: gate(o, 'animus-lan-application').__setitem__(4, False),
            'application binding deleted': lambda o: o['policy_bindings'].remove(gate(o, 'animus-lan-application')),
            'application engine any': lambda o: o['applications'][0].__setitem__(1, 'any'),
            'provider flow changed': lambda o: o['applications'][0].__setitem__(3, 'default-authentication-flow'),
            'prompt bound again': lambda o: o['stage_bindings'].append(
                [AUTHZ, 'animus-lan-consent', 10, True, False, 'retry', 'any']),
            'stage binding removed': lambda o: o['stage_bindings'].remove(find(o['stage_bindings'], NORMAL, 'animus-lan-login')),
            'flow-level binding': lambda o: o['policy_bindings'].append(
                ['flow', NORMAL, 'animus-lan-pkce', 0, True, False, False, 30, False, False, False]),
            'expression edited': lambda o: o['expressions'].__setitem__('animus-lan-login-gate', '0' * 64),
            'expression missing': lambda o: o['expressions'].pop('animus-lan-application'),
            'mapping edited': lambda o: o['mappings'].__setitem__('animus.lan/qualified-openid-v2', '0' * 64),
            'brand authentication flow': lambda o: o['brands'][0].__setitem__('flow_authentication', NORMAL),
            'brand user switch flow': lambda o: o['brands'][0].__setitem__('flow_user_switch', NORMAL),
            'brand authentication unset': lambda o: o['brands'][0].__setitem__('flow_authentication', None),
            'second instance': lambda o: o['instances'].append(
                ['ct-animus/alkion-lan.yaml.bak.yaml', {}, True, 'successful', 'x']),
            'duplicate instance': lambda o: o['instances'].append(list(o['instances'][0])),
            'no instance': lambda o: o['instances'].clear(),
            'context changed': lambda o: o['instances'][0].__setitem__(1, {}),
            'instance disabled': lambda o: o['instances'][0].__setitem__(2, False),
            'instance error': lambda o: o['instances'][0].__setitem__(3, 'error'),
            'file not applied': lambda o: o['instances'][0].__setitem__(4, 'f' * 128),
            'session writable': lambda o: o.__setitem__('read_only', 'off'),
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                self.assertTrue(self.failures(mutate))

    def test_messages_name_the_drift_and_no_values(self):
        failures = self.failures(lambda o: (gate(o, 'animus-lan-login-gate').__setitem__(4, False),
                                            o['instances'][0].__setitem__(4, 'f' * 128)))
        self.assertEqual(failures, [
            'stage binding animus-lan-normal/animus-lan-login@50: policy binding animus-lan-login-gate@0: '
            'enabled is False, expected True',
            "blueprint file sha512 differs from the instance's last_applied_hash"])
        self.assertFalse(any('f' * 16 in failure for failure in failures))


class Command(unittest.TestCase):
    def run_main(self, observed, extra=()):
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs.get('input', '')))
            return subprocess.CompletedProcess(argv, 0, json.dumps(observed) + '\n', '')
        out = io.StringIO()
        status = drift.main(['--db-container', 'ct-animus-auth-postgres-1', '--blueprint', str(BLUEPRINT), *extra],
                            runner=runner, out=out)
        return status, out.getvalue(), calls

    def test_pass_and_fail_exit_codes(self):
        status, text, calls = self.run_main(live())
        self.assertEqual(status, 0)
        self.assertTrue(text.startswith('PASS: '))
        observed = live()
        gate(observed, 'animus-lan-entry-gate')[4] = False
        status, text, _ = self.run_main(observed)
        self.assertEqual(status, 1)
        self.assertTrue(text.startswith('FAIL: 1 difference(s)'))
        status, text, _ = self.run_main(observed, ['--json'])
        self.assertEqual((status, json.loads(text)['pass']), (1, False))

    def test_query_is_read_only_and_needs_no_secret(self):
        _, _, calls = self.run_main(live())
        (argv, sql), = calls
        self.assertEqual(argv[:6], ['docker', 'exec', '-i', '-e', 'PGOPTIONS=-c default_transaction_read_only=on',
                                    'ct-animus-auth-postgres-1'])
        self.assertIn('-U', argv)
        self.assertNotIn('PGPASSWORD', ' '.join(argv))
        self.assertTrue(sql.startswith('BEGIN READ ONLY;'))
        for statement in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'ALTER', 'DROP', 'CREATE', 'GRANT'):
            self.assertNotIn(statement, sql.upper().replace('LAST_UPDATED', ''))
        for column in ('client_secret', 'password', 'token', 'signing_key_id', 'content', 'value', 'cache_key'):
            self.assertNotRegex(sql, r'\b' + column + r'\b')

    def test_errors_exit_2_with_a_generic_message(self):
        def failing(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, '', 'psql: FATAL: details that must not be printed')
        out = io.StringIO()
        status = drift.main(['--db-container', 'c', '--blueprint', str(BLUEPRINT)], runner=failing, out=out)
        self.assertEqual(status, 2)
        self.assertEqual(out.getvalue(), 'ERROR: read-only database query failed\n')
        out = io.StringIO()
        self.assertEqual(drift.main(['--db-container', 'c; rm -rf /', '--blueprint', str(BLUEPRINT)],
                                    runner=failing, out=out), 2)
        out = io.StringIO()
        self.assertEqual(drift.main(['--db-container', 'c', '--blueprint', '/nonexistent'], runner=failing, out=out), 2)


if __name__ == '__main__':
    unittest.main(verbosity=1)
