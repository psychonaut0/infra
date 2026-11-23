#!/usr/bin/env python3
"""Offline structural checks of the deployed P1 redesign blueprint and its rollback; starts nothing.

The runtime evidence is the isolated campaign (run.sh); these checks pin the shape
the campaign qualified so an edit cannot silently reintroduce a fail-open binding.
The exact binding set, entry order and rollback chain are pinned in ../../../test_blueprint.py.
"""
from pathlib import Path
import subprocess
import unittest

import yaml

HERE = Path(__file__).resolve().parent
STACK = HERE.parents[2]
CANDIDATE = STACK / 'blueprints' / 'alkion-lan.yaml'
ROLLBACK = STACK / 'rollback' / 'alkion-lan-20261123-rollback.yaml'


class Tag:
    def __init__(self, name, value):
        self.name, self.value = name, value

    def __eq__(self, other):
        return isinstance(other, Tag) and (self.name, self.value) == (other.name, other.value)

    def __repr__(self):
        return f'{self.name}{self.value!r}'


class Loader(yaml.SafeLoader):
    pass


for _tag in ('!KeyOf', '!Find', '!Context', '!AtIndex', '!If', '!Condition'):
    Loader.add_constructor(_tag, lambda loader, node, tag=_tag: Tag(
        tag, loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode)
        else loader.construct_sequence(node, deep=True)))

ENTRIES = yaml.load(CANDIDATE.read_text(), Loader=Loader)['entries']
BY_ID = {e['id']: e for e in ENTRIES if 'id' in e}
POLICIES = {e['identifiers']['name']: e for e in ENTRIES if e['model'] == 'authentik_policies_expression.expressionpolicy'}


def flow_bindings(flow_id, present=True):
    out = {}
    for e in ENTRIES:
        if e['model'] != 'authentik_flows.flowstagebinding' or e['identifiers']['target'] != Tag('!KeyOf', flow_id):
            continue
        if (e.get('state', 'present') == 'present') == present:
            out[e['identifiers']['order'], e['identifiers']['stage'].value] = e
    return out


def policy_bindings(target_id):
    return [e for e in ENTRIES if e['model'] == 'authentik_policies.policybinding'
            and e['identifiers']['target'] == Tag('!KeyOf', target_id)]


class CandidateShape(unittest.TestCase):
    def test_normal_flow_is_password_totp_login_notice(self):
        present = flow_bindings('normal-flow')
        self.assertEqual(sorted(present), [(10, 'normal-identification'), (30, 'totp-validation'),
                                           (50, 'login'), (60, 'normal-denied')])
        for key in ((10, 'normal-identification'), (50, 'login'), (60, 'normal-denied')):
            self.assertEqual(present[key]['attrs'], {'evaluate_on_plan': False, 're_evaluate_policies': True,
                                                     'invalid_response_action': 'retry', 'policy_engine_mode': 'all'})
        self.assertEqual(present[30, 'totp-validation']['attrs']['re_evaluate_policies'], False)

    def test_removed_bindings_are_guarded(self):
        for flow, keys in (('normal-flow', {(0, 'normal-entry'), (40, 'normal-final'), (10, 'identification'), (20, 'password')}),
                           ('authorization-flow', {(0, 'authorization-entry'), (10, 'consent'), (20, 'authorization-final')})):
            absent = flow_bindings(flow, present=False)
            self.assertEqual(set(absent), keys)
            for entry in absent.values():
                self.assertEqual(len(entry['conditions']), 1)
                self.assertEqual(entry['conditions'][0].name, '!Find')

    def test_authorization_flow_has_no_stage(self):
        self.assertEqual(flow_bindings('authorization-flow'), {})

    def test_each_gate_has_exactly_one_plain_binding(self):
        for target, policy in (('normal-identification-binding', 'entry-gate-policy'),
                               ('normal-login-binding', 'login-gate-policy'),
                               ('normal-denied-binding', 'denied-notice-policy')):
            bindings = policy_bindings(target)
            self.assertEqual(len(bindings), 1)
            self.assertEqual(bindings[0]['identifiers']['policy'], Tag('!KeyOf', policy))
            self.assertEqual(bindings[0]['attrs'], {'negate': False, 'failure_result': False, 'enabled': True, 'timeout': 30})

    def test_gates_fail_to_false_and_run_only_in_process(self):
        for name, stage, order in (('animus-lan-entry-gate', 'animus-lan-normal-identification', 10),
                                   ('animus-lan-login-gate', 'animus-lan-login', 50)):
            expression = POLICIES[name]['attrs']['expression']
            self.assertTrue(expression.rstrip().endswith('except Exception:\n    return False'), name)
            self.assertIn('if type(process).__name__ != "_MainProcess" or process.daemon:\n        return False', expression)
            self.assertIn(f'binding.stage.name != "{stage}" or binding.order != {order}', expression)
            self.assertIn('"is_restored"', expression)
            self.assertIn('"is_redirected"', expression)
            self.assertIn('plan is not http.session.get(SESSION_KEY_PLAN)', expression)

    def test_login_gate_keeps_every_p1_check(self):
        expression = POLICIES['animus-lan-login-gate']['attrs']['expression']
        for needle in ('0 <= time.time() - started <= 300', 'user.is_superuser', 'not user.is_active',
                       'ak_is_group_member(user, name="animus-lan-normal")', 'context.get("auth_method") != "password"',
                       'type(device).__name__ != "TOTPDevice"', 'device.user_id != user.pk',
                       'principal != state["initial_session_user"]', 'args.update(marker)',
                       '"hub_policy_gate": "animus-lan-login-gate"'):
            self.assertIn(needle, expression)

    def test_notice_is_never_the_only_denial(self):
        # The notice only explains; on its own error it is removed. An eligible user then
        # completes; an ineligible one has no session because the login gate removed User Login.
        expression = POLICIES['animus-lan-denied-notice']['attrs']['expression']
        self.assertTrue(expression.rstrip().endswith('except Exception:\n    return False'))

    def test_application_policy_is_the_last_check_before_the_code(self):
        expression = POLICIES['animus-lan-application']['attrs']['expression']
        for needle in ('any(value != "login" for value in prompts[0].split())',
                       'query.get("response_mode", ["query"]) != ["query"]',
                       'and current == bound',
                       'args.get("hub_policy_gate") == "animus-lan-login-gate"',
                       'if not cache.add("animus-lan/claimed-login/" + str(event.pk), "claimed", 900):',
                       '"request" in query or "request_uri" in query',
                       'http.method != "GET"'):
            self.assertIn(needle, expression)
        self.assertTrue(expression.rstrip().endswith('except Exception:\n    return False'))
        app = [e for e in ENTRIES if e['model'] == 'authentik_policies.policybinding'
               and e['identifiers']['target'] == Tag('!KeyOf', 'application')]
        self.assertEqual(len(app), 3)
        for binding in app:
            self.assertEqual(binding['attrs'], {'negate': False, 'failure_result': False, 'enabled': True, 'timeout': 30})

    def test_replaced_prompt_objects_stay_for_the_rollback(self):
        for stage in ('normal-entry', 'normal-final', 'authorization-entry', 'authorization-final', 'consent'):
            self.assertIn(stage, BY_ID)

    def test_rollback_is_deployed_blueprint_plus_guarded_removals(self):
        deployed = subprocess.run(['git', '-C', str(HERE), 'show', 'c693d82:stacks/ct-animus/blueprints/alkion-lan.yaml'],
                                  capture_output=True, text=True, check=True).stdout
        rollback = ROLLBACK.read_text()
        added = [line for line in rollback.splitlines() if line not in deployed.splitlines()]
        removed = [line for line in deployed.splitlines() if line not in rollback.splitlines()]
        self.assertEqual(removed, [])
        for line in added:
            self.assertTrue(line.startswith('#') or line.strip().startswith(('- model:', 'state: absent', 'conditions:', '- !Find', 'identifiers:', 'id: rb-', 'attrs: {policy_engine_mode: any}')), line)


if __name__ == '__main__':
    unittest.main(verbosity=1)
