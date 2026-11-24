#!/usr/bin/env python3
"""Synthetic checks of the deployed alkion-lan blueprint; no container or provider.

Runs the PKCE, application, gate and validator expressions from
blueprints/alkion-lan.yaml against stub requests, in the style of
qualification/p1/test_candidate.py, mirrors Authentik's !Context/!AtIndex
resolution for the issuance switch, and pins the 2026-11-23 redesign's binding set,
pinned attributes, fail-closed entry order and rollback chain. The real policy
engine, flow planner and blueprint importer are runtime checks
(qualification/p1/redesign/run.sh campaign).
"""
from contextlib import contextmanager
import difflib
import subprocess
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from urllib.parse import urlencode
import unittest
from unittest.mock import patch
from uuid import UUID

import yaml

STACK = Path(__file__).resolve().parent
BLUEPRINT = STACK / 'blueprints' / 'alkion-lan.yaml'
ROLLBACK = STACK / 'rollback' / 'alkion-lan-20261118-rollback.yaml'
REDESIGN_ROLLBACK = STACK / 'rollback' / 'alkion-lan-20261123-rollback.yaml'
# sha256 of blueprints/alkion-lan.yaml as deployed before the 2026-11-23 redesign
# (infra c693d82); the redesign rollback must be exactly that plus added entries.
DEPLOYED_SHA256 = 'd560cd504c803dd4de87f74628ab2400746ae9360079ca351fb43ae814b748ef'
# sha256 of blueprints/alkion-lan.yaml before the 2026-11-18 simplification
# (at bcd1a2e, last changed in add47cb); the rollback must reproduce it exactly.
PREVIOUS_SHA256 = '5d30b62e75ebe714f1ec11258bf813d28849593a8020f25c3c3b6eaf0f06429d'
NATIVE = 'dev.animusstudio.alkion.dev:/oauth2redirect'
LOOPBACK = 'http://127.0.0.1:43819/callback'
FLOW = '1' * 32
AUTHZ = '2' * 32
POLICY = 'animus-password-totp-mechanism-v2'
SESSION_KEY = 'authentik/flows/plan'
CHALLENGE = 'A' * 43


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

DOCUMENT = yaml.load(BLUEPRINT.read_text(), Loader=Loader)
ENTRIES = DOCUMENT['entries']
BY_ID = {entry['id']: entry for entry in ENTRIES if 'id' in entry}
ROLLBACK_DOCUMENT = yaml.load(ROLLBACK.read_text(), Loader=Loader)
REDESIGN_ROLLBACK_DOCUMENT = yaml.load(REDESIGN_ROLLBACK.read_text(), Loader=Loader)
PINNED = {'negate': False, 'failure_result': False, 'enabled': True, 'timeout': 30}
GATED = {'evaluate_on_plan': False, 're_evaluate_policies': True, 'invalid_response_action': 'retry',
         'policy_engine_mode': 'all'}
NOTICE = "Sign-in couldn't be completed. Go back to Alkion and tap Connect again."


def raw_entries(text):
    """Exact source text of each blueprint entry, trailing comments/blank lines dropped."""
    root = yaml.compose(text, Loader=Loader)
    entries = next(value for key, value in root.value if key.value == 'entries')
    result = []
    for node in entries.value:
        lines = text[node.start_mark.index:node.end_mark.index].splitlines()
        while lines and (not lines[-1].strip() or lines[-1].strip().startswith('#')):
            lines.pop()
        result.append('\n'.join(lines))
    return result


def resolve(value, context):
    """Mirror of Authentik 2026.8.5 Context and AtIndex (blueprints/v1/common.py)."""
    if not isinstance(value, Tag):
        return value
    if value.name == '!Context':
        key, default = value.value if isinstance(value.value, list) else (value.value, None)
        return context.get(key, default)
    if value.name == '!AtIndex':
        obj, attribute, default = (resolve(part, context) for part in value.value)
        return obj[attribute] if attribute in obj else default
    raise AssertionError('unexpected tag in switch: ' + value.name)


def merged(instance_context):
    return {**DOCUMENT.get('context', {}), **instance_context}


def fake_user_by(accounts=None):
    """ak_user_by over a fake user table: by default every pk is an active, non-superuser
    account; `accounts` maps a pk to the account's current database row (None: no row)."""
    accounts = accounts or {}

    def lookup(**filters):
        row = accounts[filters['pk']] if filters['pk'] in accounts else user(filters['pk'])
        if row is None or any(getattr(row, key) != value for key, value in filters.items()):
            return None
        return row
    return lookup


def expression(entry_id, **globals_):
    source = BY_ID[entry_id]['attrs']['expression']
    namespace = {'ak_is_group_member': lambda *a, **k: True, 'ak_user_by': fake_user_by()} | globals_
    wrapped = 'def evaluate(request=None, provider=None, token=None):\n' + ''.join(
        '    ' + line + '\n' for line in source.splitlines())
    exec(compile(wrapped, entry_id, 'exec'), namespace)
    return namespace['evaluate']


LAST_LOGIN = 'authentik/providers/oauth2/last_login_uid'
SESSION_GET = 'authentik/flows/get'


@contextmanager
def stock_imports():
    signals = ModuleType('authentik.events.signals')
    signals.get_login_event = lambda obj: getattr(obj, 'event', None)
    executor = ModuleType('authentik.flows.views.executor')
    executor.SESSION_KEY_PLAN = SESSION_KEY
    executor.SESSION_KEY_GET = SESSION_GET
    authorize_view = ModuleType('authentik.providers.oauth2.views.authorize')
    authorize_view.SESSION_KEY_LAST_LOGIN_UID = LAST_LOGIN
    django_cache = ModuleType('django.core.cache')
    django_cache.cache = CACHE
    with patch.dict('sys.modules', {'authentik.events.signals': signals, 'authentik.flows.views.executor': executor,
                                    'authentik.providers.oauth2.views.authorize': authorize_view,
                                    'django.core.cache': django_cache}):
        yield


class Cache:
    """django_postgres_cache add(): an INSERT on the key, False when the key exists."""

    def __init__(self):
        self.rows, self.calls = {}, []

    def add(self, key, value, timeout):
        self.calls.append((key, timeout))
        if key in self.rows:
            return False
        self.rows[key] = value
        return True


CACHE = Cache()


def authorize(**changes):
    context = {'oauth_grant_type': 'authorization_code', 'oauth_response_type': 'code', 'oauth_redirect_uri': NATIVE,
               'oauth_code_challenge_method': 'S256', 'oauth_code_challenge': CHALLENGE} | changes
    return NS(context=context, http_request=NS(path='/application/o/authorize/'))


def user(pk=7, **changes):
    return NS(**({'pk': pk, 'is_active': True, 'is_superuser': False, 'is_authenticated': True} | changes))


def qualified(client_id='alkion-lan'):
    args = {'hub_policy_id': POLICY, 'hub_policy_flow': FLOW, 'hub_policy_subject': 7,
            'hub_policy_execution': 'a' * 32, 'hub_factor_time': 1000,
            'mfa_devices': [{'app': 'authentik_stages_authenticator_totp', 'model_name': 'totpdevice'}]}
    event = NS(pk='event-a', action='login', user={'pk': 7}, created=datetime.fromtimestamp(1100.5, timezone.utc),
               context={'auth_method': 'password', 'auth_method_args': args})
    provider = NS(pk=9, client_id=client_id, authentication_flow_id=UUID(FLOW))
    return user(), event, provider


def authorization_entry(redirect=NATIVE, client_id='alkion-lan', method='S256'):
    subject, event, provider = qualified(client_id)
    binding = NS(stage=NS(name='animus-lan-authorization-entry'), order=0, target_id=UUID(AUTHZ))
    params = NS(provider=provider, client_id=client_id, grant_type='authorization_code', code_challenge_method=method,
                code_challenge=CHALLENGE, redirect_uri=redirect, response_type='code')
    plan = NS(flow_pk=AUTHZ, bindings=[binding],
              context={'goauthentik.io/providers/oauth2/params': params, 'application': NS(provider_id=9)})
    http = NS(session={SESSION_KEY: plan}, user=subject, event=event, path='/api/v3/flows/executor/animus-lan-authorization/')
    return NS(http_request=http, user=subject, context={}, obj=None), plan


NEAR_MISSES = (
    'http://127.0.0.1:43819/callback/', 'http://127.0.0.1:43819/callback?x=1', 'http://127.0.0.1:43819/oauth2redirect',
    'http://127.0.0.1:43820/callback', 'http://127.0.0.1/callback', 'http://localhost:43819/callback',
    'http://[::1]:43819/callback', 'https://127.0.0.1:43819/callback', 'HTTP://127.0.0.1:43819/callback',
    'dev.animusstudio.alkion:/oauth2redirect', 'dev.animusstudio.alkion.dev:/oauth2redirect/',
    'dev.animusstudio.alkion.dev://oauth2redirect', ' ' + NATIVE, NATIVE + ' ', '', None,
)


class RedirectAndPkceTests(unittest.TestCase):
    def test_provider_lists_exactly_the_two_strict_authorization_redirects(self):
        provider = BY_ID['alkion-lan-provider']['attrs']
        self.assertEqual(provider['redirect_uris'], [
            {'matching_mode': 'strict', 'redirect_uri_type': 'authorization', 'url': NATIVE},
            {'matching_mode': 'strict', 'redirect_uri_type': 'authorization', 'url': LOOPBACK}])
        self.assertEqual((provider['client_type'], provider['client_id']), ('public', 'alkion-lan'))
        self.assertEqual(provider['grant_types'], ['authorization_code', 'refresh_token'])
        self.assertNotIn('require_pkce', provider)  # no such provider field in 2026.8.5
        for policy in ('pkce-policy', 'authorization-entry-policy'):
            source = BY_ID[policy]['attrs']['expression']
            self.assertEqual((source.count(NATIVE), source.count(LOOPBACK), source.count('127.0.0.1')), (1, 1, 1))

    def test_pkce_admits_s256_for_each_registered_redirect(self):
        for redirect in (NATIVE, LOOPBACK):
            self.assertIs(expression('pkce-policy')(authorize(oauth_redirect_uri=redirect)), True)

    def test_pkce_refuses_absent_plain_or_malformed_challenge(self):
        for redirect in (NATIVE, LOOPBACK):
            for change in ({'oauth_code_challenge_method': None}, {'oauth_code_challenge_method': 'plain'},
                           {'oauth_code_challenge_method': 's256'}, {'oauth_code_challenge': None},
                           {'oauth_code_challenge': 'A' * 42}, {'oauth_code_challenge': 'A' * 44},
                           {'oauth_code_challenge': 'A' * 42 + '='}, {'oauth_grant_type': 'implicit'},
                           {'oauth_response_type': 'token'}, {'oauth_response_type': 'code id_token'}):
                with self.subTest(redirect=redirect, change=change):
                    self.assertFalse(expression('pkce-policy')(authorize(oauth_redirect_uri=redirect, **change)))

    def test_pkce_refuses_every_other_redirect(self):
        for redirect in NEAR_MISSES:
            with self.subTest(redirect=redirect):
                self.assertFalse(expression('pkce-policy')(authorize(oauth_redirect_uri=redirect)))

    def test_pkce_token_rules_are_unchanged(self):
        def token(**context):
            return NS(context=context, http_request=NS(path='/application/o/token/'))
        evaluate = expression('pkce-policy')
        self.assertTrue(evaluate(token(oauth_grant_type='authorization_code', oauth_code_verifier='a' * 43)))
        self.assertTrue(evaluate(token(oauth_grant_type='refresh_token')))
        for verifier in (None, 'a' * 42, 'a' * 129, 'a' * 42 + '+'):
            self.assertFalse(evaluate(token(oauth_grant_type='authorization_code', oauth_code_verifier=verifier)))
        self.assertFalse(evaluate(token(oauth_grant_type='client_credentials')))
        self.assertFalse(evaluate(NS(context={}, http_request=NS(path='/application/o/device/'))))


class ClientAndAuthorizationTests(unittest.TestCase):
    def test_authorization_entry_admits_both_redirects_for_alkion_lan_only(self):
        with stock_imports(), patch('time.time', return_value=1200):
            for redirect in (NATIVE, LOOPBACK):
                request, plan = authorization_entry(redirect)
                self.assertIs(expression('authorization-entry-policy')(request), True)
                self.assertEqual(plan.context['hub_p1_authorization']['redirect_uri'], redirect)
            for redirect in NEAR_MISSES:
                with self.subTest(redirect=redirect):
                    request, plan = authorization_entry(redirect)
                    self.assertFalse(expression('authorization-entry-policy')(request))
                    self.assertNotIn('hub_p1_authorization', plan.context)
            for kwargs in ({'client_id': 'other-client'}, {'method': 'plain'}, {'method': None}):
                with self.subTest(**kwargs):
                    self.assertFalse(expression('authorization-entry-policy')(authorization_entry(LOOPBACK, **kwargs)[0]))

    def test_application_policy_refuses_another_client(self):
        for client_id, expected in (('alkion-lan', True), ('other-client', False)):
            CACHE.rows.clear()
            request, now = app_request(provider_client=client_id)
            with stock_imports(), patch('time.time', return_value=now):
                self.assertIs(expression('application-policy')(request), expected)


class IssuanceSwitchTests(unittest.TestCase):
    def binding(self, policy):
        return next(e for e in ENTRIES if e['model'] == 'authentik_policies.policybinding'
                    and e['identifiers']['policy'] == Tag('!KeyOf', policy))

    def test_closed_by_default_and_opened_only_by_the_exact_profile(self):
        closure = self.binding('issuance-closed-policy')
        self.assertEqual(DOCUMENT['context'], {'animus_issuance_profile': 'closed'})
        cases = ({}, {'animus_issuance_profile': 'closed'}, {'animus_issuance_profile': 'Owner-Commissioning'},
                 {'animus_issuance_profile': ' owner-commissioning'}, {'animus_issuance_profile': True},
                 {'animus_issuance_profile': 'true'}, {'animus_issuance_profile': 1},
                 {'animus_issuance_profile': None}, {'other': 'owner-commissioning'})
        for context in cases:
            with self.subTest(context=context):
                self.assertEqual(resolve(closure['state'], merged(context)), 'present')
        self.assertEqual(resolve(closure['state'], merged({'animus_issuance_profile': 'owner-commissioning'})), 'absent')
        self.assertEqual(BY_ID['issuance-closed-policy']['attrs']['expression'].strip().splitlines()[-1], 'return False')

    def test_application_bindings_are_pinned_and_ordered(self):
        bindings = [e for e in ENTRIES if e['model'] == 'authentik_policies.policybinding'
                    and e['identifiers']['target'] == Tag('!KeyOf', 'application')]
        self.assertEqual([(e['identifiers']['policy'].value, e['identifiers']['order']) for e in bindings],
                         [('pkce-policy', 0), ('application-policy', 1), ('issuance-closed-policy', 2)])
        for entry in bindings:
            self.assertEqual(entry['attrs'], PINNED)
            self.assertEqual('state' in entry, entry['identifiers']['policy'].value == 'issuance-closed-policy')
        self.assertEqual(BY_ID['application']['attrs']['policy_engine_mode'], 'all')
        switched = [e for e in ENTRIES if 'state' in e or 'conditions' in e]
        self.assertEqual([e['model'] for e in switched], ['authentik_flows.flowstagebinding'] * 7
                         + ['authentik_policies.policybinding'])
        self.assertTrue(all(e.get('state') == 'absent' for e in ENTRIES if 'conditions' in e))

    def test_enrollment_closed_no_recovery_and_no_seeds(self):
        models = {e['model'] for e in ENTRIES}
        for forbidden in ('authentik_core.user', 'authentik_core.token', 'authentik_brands.brand'):
            self.assertNotIn(forbidden, models)
        # The only Deny stage is the explanation-only notice, with OD2's exact text.
        self.assertEqual([e['id'] for e in ENTRIES if e['model'] == 'authentik_stages_deny.denystage'], ['normal-denied'])
        self.assertEqual(BY_ID['normal-denied']['attrs'], {'deny_message': NOTICE})
        for group in ('bootstrap-group', 'repair-group', 'normal-group'):
            self.assertEqual(BY_ID[group]['attrs'], {'is_superuser': False})
        identification = BY_ID['identification']['attrs']
        self.assertEqual((identification['enrollment_flow'], identification['recovery_flow'],
                          identification['passwordless_flow']), (None, None, None))
        self.assertEqual(BY_ID['enrollment-flow']['attrs']['designation'], 'stage_configuration')
        for flow in ('normal-flow', 'enrollment-flow', 'repair-flow', 'authorization-flow'):
            self.assertNotIn('conditions', BY_ID[flow])



def bindings(document, flow):
    return [(e['identifiers']['order'], e['identifiers']['stage'], e.get('state', 'present'), e.get('attrs'))
            for e in document['entries'] if e['model'] == 'authentik_flows.flowstagebinding'
            and e['identifiers']['target'] == Tag('!KeyOf', flow)]


UNCONDITIONAL = {'evaluate_on_plan': True, 're_evaluate_policies': False, 'invalid_response_action': 'retry'}
SHARED = ('prompt-continue', 'identification', 'password', 'enrollment-flow', 'repair-flow', 'bootstrap-entry',
          'bootstrap', 'repair', 'bootstrap-entry-policy', 'bootstrap-policy', 'repair-policy', 'totp-setup',
          'static-setup', 'static-validation', 'logout', 'bootstrap-group', 'repair-group')
LABELS = {
    'prompt-normal-entry': 'Start a new sign-in. From now you have 5 minutes to enter your password and authenticator code.',
    'prompt-normal-final': 'Check that this account used its password and authenticator code within the last 5 minutes.',
    'prompt-authorization-entry': 'Check that you signed in to Alkion with password and authenticator code within the last 5 minutes.',
    'prompt-authorization-final': "Check that Alkion's request and your sign-in are unchanged and still under 5 minutes old.",
}


class FlowSimplificationTests(unittest.TestCase):
    """2026-11-18: username+password on one page, remembered consent, plain labels."""

    def test_combined_identification_stage_is_fully_declared(self):
        self.assertEqual(BY_ID['normal-identification'], {
            'model': 'authentik_stages_identification.identificationstage', 'id': 'normal-identification',
            'identifiers': {'name': 'animus-lan-normal-identification'},
            'attrs': {'user_fields': ['username'], 'password_stage': Tag('!KeyOf', 'password'),
                      'captcha_stage': None, 'webauthn_stage': None, 'case_insensitive_matching': True,
                      'show_matched_user': False, 'pretend_user_exists': True, 'enable_remember_me': False,
                      'enrollment_flow': None, 'recovery_flow': None, 'passwordless_flow': None,
                      'sources': [], 'show_source_labels': False}})
        self.assertEqual(BY_ID['password']['attrs']['backends'], ['authentik.core.auth.InbuiltBackend'])

    def test_shared_stages_enrollment_and_repair_are_byte_for_byte_unchanged(self):
        forward = dict(zip((e.get('id') for e in ENTRIES), raw_entries(BLUEPRINT.read_text())))
        previous = dict(zip((e.get('id') for e in ROLLBACK_DOCUMENT['entries']), raw_entries(ROLLBACK.read_text())))
        for entry_id in SHARED:
            with self.subTest(entry=entry_id):
                self.assertEqual(forward[entry_id], previous[entry_id])
        for flow in ('enrollment-flow', 'repair-flow'):
            with self.subTest(flow=flow):
                self.assertEqual(bindings(DOCUMENT, flow), bindings(ROLLBACK_DOCUMENT, flow))
                forward_raw = [r for e, r in zip(ENTRIES, raw_entries(BLUEPRINT.read_text()))
                               if e['model'] == 'authentik_flows.flowstagebinding'
                               and e['identifiers']['target'] == Tag('!KeyOf', flow)]
                previous_raw = [r for e, r in zip(ROLLBACK_DOCUMENT['entries'], raw_entries(ROLLBACK.read_text()))
                                if e['model'] == 'authentik_flows.flowstagebinding'
                                and e['identifiers']['target'] == Tag('!KeyOf', flow)]
                self.assertEqual(forward_raw, previous_raw)
                self.assertTrue(all(stage != Tag('!KeyOf', 'normal-identification')
                                    for _, stage, _, _ in bindings(DOCUMENT, flow)))

    def test_consent_stage_is_kept_unchanged_for_the_rollback(self):
        self.assertEqual(BY_ID['consent']['attrs'], {'mode': 'permanent', 'consent_expire_in': 'weeks=4'})

    def test_validator_prompts_have_their_own_plain_checkbox(self):
        stages = {'normal-entry': ('prompt-normal-entry', 'entry-policy'),
                  'normal-final': ('prompt-normal-final', 'final-policy'),
                  'authorization-entry': ('prompt-authorization-entry', 'authorization-entry-policy'),
                  'authorization-final': ('prompt-authorization-final', 'authorization-final-policy')}
        for stage, (prompt, policy) in stages.items():
            with self.subTest(stage=stage):
                self.assertEqual(BY_ID[stage]['attrs'], {'fields': [Tag('!KeyOf', prompt)],
                                                         'validation_policies': [Tag('!KeyOf', policy)]})
                self.assertEqual(BY_ID[prompt]['attrs'], {
                    'field_key': 'p1_continue', 'label': LABELS[prompt], 'type': 'checkbox', 'required': True,
                    'placeholder': '', 'initial_value': '', 'sub_text': '', 'order': 0,
                    'placeholder_expression': False, 'initial_value_expression': False})
        self.assertEqual(len(set(LABELS.values())), 4)
        for stage in ('bootstrap-entry', 'bootstrap', 'repair'):
            self.assertEqual(BY_ID[stage]['attrs']['fields'], [Tag('!KeyOf', 'prompt-continue')])

    def test_policies_do_not_depend_on_the_replaced_stages(self):
        for entry in ENTRIES:
            source = entry.get('attrs', {}).get('expression') if isinstance(entry.get('attrs'), dict) else None
            if source:
                with self.subTest(policy=entry.get('id')):
                    for needle in ('animus-lan-identification', 'animus-lan-password', 'len(plan.bindings)',
                                   'bindings[1]', 'animus-lan-normal-identification'):
                        if not (entry.get('id') == 'entry-gate-policy' and needle == 'animus-lan-normal-identification'):
                            self.assertNotIn(needle, source)
        # Entry/final validators still address only their own binding.
        self.assertIn('binding = plan.bindings[0]', BY_ID['entry-policy']['attrs']['expression'])
        self.assertIn('context.get("auth_method") != "password"', BY_ID['final-policy']['attrs']['expression'])


ADDED = """  # Objects added by the simplification. !Find (not !KeyOf): absent entries never
  # become references. Each removal runs only while its object exists; in 2026.8.5
"""


class RollbackTests(unittest.TestCase):
    def test_rollback_is_the_previous_blueprint_plus_explicit_removals(self):
        text = ROLLBACK.read_text()
        self.assertTrue(text.startswith('# ROLLBACK of the 2026-11-18 normal-flow simplification.'))
        body = text[text.index('# Application issuance is CLOSED by default.'):]
        start = body.index(ADDED)
        end = body.index('    identifiers: {target: !KeyOf enrollment-flow', start) - len('  - model: authentik_flows.flowstagebinding\n')
        self.assertEqual(hashlib.sha256((body[:start] + body[end:]).encode()).hexdigest(), PREVIOUS_SHA256)
        removed = yaml.load('entries:\n' + body[start:end], Loader=Loader)['entries']
        stage = Tag('!Find', ['authentik_stages_identification.identificationstage',
                              ['name', 'animus-lan-normal-identification']])
        self.assertEqual(removed, [
            {'model': 'authentik_flows.flowstagebinding', 'state': 'absent',
             'conditions': [Tag('!Find', ['authentik_flows.flowstagebinding', ['target', Tag('!KeyOf', 'normal-flow')],
                                          ['stage__name', 'animus-lan-normal-identification'], ['order', 10]])],
             'identifiers': {'target': Tag('!KeyOf', 'normal-flow'), 'stage': stage, 'order': 10}},
            {'model': 'authentik_stages_identification.identificationstage', 'state': 'absent',
             'conditions': [stage], 'identifiers': {'name': 'animus-lan-normal-identification'}},
            *({'model': 'authentik_stages_prompt.prompt', 'state': 'absent',
               'conditions': [Tag('!Find', ['authentik_stages_prompt.prompt', ['name', BY_ID[prompt]['identifiers']['name']]])],
               'identifiers': {'name': BY_ID[prompt]['identifiers']['name']}} for prompt in LABELS)])

    def test_rollback_restores_previous_attributes_explicitly(self):
        by_id = {e['id']: e for e in ROLLBACK_DOCUMENT['entries'] if 'id' in e}
        self.assertEqual(by_id['consent']['attrs'], {'mode': 'always_require'})
        self.assertEqual([(o, s, state) for o, s, state, _ in bindings(ROLLBACK_DOCUMENT, 'normal-flow')], [
            (0, Tag('!KeyOf', 'normal-entry'), 'present'), (10, Tag('!KeyOf', 'identification'), 'present'),
            (20, Tag('!KeyOf', 'password'), 'present'), (30, Tag('!KeyOf', 'totp-validation'), 'present'),
            (40, Tag('!KeyOf', 'normal-final'), 'present'), (50, Tag('!KeyOf', 'login'), 'present'),
            (10, Tag('!Find', ['authentik_stages_identification.identificationstage',
                               ['name', 'animus-lan-normal-identification']]), 'absent')])
        for stage in ('normal-entry', 'normal-final', 'authorization-entry', 'authorization-final'):
            self.assertEqual(by_id[stage]['attrs']['fields'], [Tag('!KeyOf', 'prompt-continue')])
        self.assertNotIn('normal-identification', by_id)
        self.assertEqual(ROLLBACK_DOCUMENT['metadata'], DOCUMENT['metadata'])
        self.assertEqual(ROLLBACK_DOCUMENT['context'], DOCUMENT['context'])
        self.assertIn('Remembered consent', ROLLBACK.read_text().split('version: 1')[0])

    def test_rollback_is_outside_the_mounted_blueprint_directory(self):
        self.assertEqual({path.name for path in (STACK / 'blueprints').iterdir()}, {'alkion-lan.yaml'})
        self.assertNotEqual(ROLLBACK.parent, BLUEPRINT.parent)


class TOTPDevice:
    def __init__(self, user_id=7, confirmed=True):
        self.user_id, self.confirmed = user_id, confirmed


def final_request(next_url):
    binding = NS(stage=NS(name='animus-lan-normal-final'), order=40, target_id=UUID(FLOW))
    context = {'hub_p1_execution': {'policy': POLICY, 'flow': FLOW, 'nonce': 'a' * 32, 'factor_time': 1000,
                                    'initial_session_user': None, 'initial_login_event': None},
               'pending_user': user(), 'auth_method': 'password', 'auth_method_args': {'mfa_devices': [TOTPDevice()]}}
    plan = NS(flow_pk=FLOW, bindings=[binding], context=context)
    session = {SESSION_KEY: plan} | ({SESSION_GET: {'next': next_url}} if next_url is not None else {})
    http = NS(session=session, user=user(None, is_authenticated=False), event=None)
    return NS(http_request=http, user=http.user, context={}, obj=None), plan


REQUEST = {'client_id': 'alkion-lan', 'redirect_uri': NATIVE, 'code_challenge': CHALLENGE, 'code_challenge_method': 'S256'}


def request_hash(fields):
    return hashlib.sha256(json.dumps([fields[k] for k in ('client_id', 'redirect_uri', 'code_challenge',
                                                         'code_challenge_method')]).encode()).hexdigest()


def authorize_url(prefix='/application/o/authorize/', **changes):
    query = {k: v for k, v in (REQUEST | {'response_type': 'code', 'prompt': 'login'} | changes).items() if v is not None}
    return prefix + '?' + urlencode(query)


def app_request(prompt=None, now=1200, method='GET', bound=None, gate='animus-lan-login-gate',
                provider_client='alkion-lan', oauth=None, **changes):
    subject, event, provider = qualified(provider_client)
    event.context['auth_method_args']['hub_policy_request'] = request_hash(REQUEST) if bound is None else bound
    if gate is not None:
        event.context['auth_method_args']['hub_policy_gate'] = gate
    query = {k: v for k, v in (REQUEST | {'response_type': 'code'} | changes).items() if v is not None}
    query |= {'prompt': prompt} if prompt is not None else {}
    http = NS(user=subject, event=event, path='/application/o/authorize/', method=method, GET=query,
              META={'QUERY_STRING': urlencode(query)}, session={})
    context = {'oauth_grant_type': 'authorization_code', 'oauth_response_type': 'code'} | (oauth or {})
    return NS(http_request=http, user=subject, obj=NS(provider=provider), context=context), now


class PromptLoginTests(unittest.TestCase):
    """prompt=login re-authenticates every Connect once and then continues that request."""

    AUTHORIZE = authorize_url()

    def test_final_binds_the_ceremony_to_its_normalised_authorize_request(self):
        for redirect in (NATIVE, LOOPBACK):
            request, plan = final_request(authorize_url(redirect_uri=redirect, state='x', scope='openid profile'))
            with stock_imports(), patch('time.time', return_value=1100):
                self.assertIs(expression('final-policy')(request), True)
            self.assertEqual(plan.context['auth_method_args']['hub_policy_request'],
                             request_hash(REQUEST | {'redirect_uri': redirect}))
            self.assertNotIn('redirect', plan.context)
            self.assertEqual(plan.context['auth_method_args']['hub_factor_time'], 1000)

    def test_final_never_binds_another_target(self):
        duplicate = self.AUTHORIZE + '&code_challenge=' + CHALLENGE
        for target in (None, 'http://evil.example' + self.AUTHORIZE, '//evil.example' + self.AUTHORIZE,
                       authorize_url('/application/o/token/'), '/application/o/authorize/', duplicate,
                       authorize_url(client_id='other-client'), authorize_url(code_challenge_method='plain'),
                       authorize_url(code_challenge='A' * 42), authorize_url(redirect_uri=None),
                       authorize_url(client_id=None), authorize_url(code_challenge_method=None)):
            with self.subTest(target=target):
                request, plan = final_request(target)
                with stock_imports(), patch('time.time', return_value=1100):
                    self.assertIs(expression('final-policy')(request), True)
                self.assertIsNone(plan.context['auth_method_args']['hub_policy_request'])
                self.assertNotIn('redirect', plan.context)

    def test_final_still_requires_the_fresh_ceremony(self):
        request, plan = final_request(self.AUTHORIZE)
        with stock_imports(), patch('time.time', return_value=1300.5):
            self.assertFalse(expression('final-policy')(request))
        self.assertNotIn('redirect', plan.context)
        self.assertNotIn('hub_policy_request', plan.context['auth_method_args'])

    def setUp(self):
        CACHE.rows.clear()
        CACHE.calls.clear()

    def run_policy(self, request, now):
        with stock_imports(), patch('time.time', return_value=now):
            return expression('application-policy')(request)

    def test_bound_fresh_ceremony_gets_one_code_claimed_atomically(self):
        for prompt in ('login', None):
            with self.subTest(prompt=prompt):
                CACHE.rows.clear()
                CACHE.calls.clear()
                request, now = app_request(prompt=prompt)
                self.assertIs(self.run_policy(request, now), True)
                self.assertEqual(CACHE.calls, [('animus-lan/claimed-login/event-a', 900)])
                self.assertGreater(CACHE.calls[0][1], 300 + 60)  # TTL beyond the ceremony plus skew
                expected = {LAST_LOGIN: 'animus-lan-continue:event-a'} if prompt else {}
                self.assertEqual(request.http_request.session, expected)
                # The same login again (sequential replay or a parallel loser): refused, no re-authentication.
                replay, now = app_request(prompt=prompt)
                self.assertIs(self.run_policy(replay, now), False)
                self.assertEqual(replay.http_request.session, {})

    def test_prompt_login_sends_every_other_session_to_reauthentication_without_a_claim(self):
        cases = ({'now': 1400}, {'code_challenge': 'B' * 43}, {'bound': 'not-this-request'},
                 {'client_id': 'other-client'}, {'redirect_uri': LOOPBACK}, {'code_challenge_method': 'plain'},
                 {'code_challenge': None}, {'redirect_uri': None}, {'gate': None}, {'gate': 'animus-lan-final'})
        for kwargs in cases:
            with self.subTest(**kwargs):
                request, now = app_request(prompt='login', **kwargs)
                self.assertIs(self.run_policy(request, now), True)
                # Equal to the current login: the authorize view re-authenticates first.
                self.assertEqual(request.http_request.session[LAST_LOGIN], 'event-a')
                without, now = app_request(**kwargs)
                self.assertIs(self.run_policy(without, now), False)
                self.assertEqual(CACHE.calls, [])
        request, now = app_request(prompt='login')
        request.user.is_superuser = True
        self.assertIs(self.run_policy(request, now), True)
        self.assertEqual(request.http_request.session[LAST_LOGIN], 'event-a')
        # A duplicated parameter never matches the single-valued hash.
        request, now = app_request(prompt='login')
        request.http_request.META['QUERY_STRING'] += '&client_id=alkion-lan'
        self.assertIs(self.run_policy(request, now), True)
        self.assertEqual(request.http_request.session[LAST_LOGIN], 'event-a')
        self.assertEqual(CACHE.calls, [])

    def test_refused_options_deny_without_claim_or_reauthentication(self):
        cases = ({'prompt': 'none'}, {'prompt': 'consent'}, {'prompt': 'login consent'}, {'prompt': 'none login'},
                 {'prompt': 'select_account'}, {'prompt': 'login', 'response_mode': 'form_post'},
                 {'prompt': 'login', 'response_mode': 'fragment'}, {'response_mode': 'form_post'},
                 {'prompt': 'login', 'request': 'x'}, {'prompt': 'login', 'request_uri': 'urn:x'},
                 {'prompt': 'login', 'method': 'POST'}, {'method': 'POST'},
                 {'prompt': 'login', 'oauth': {'oauth_response_type': 'token'}},
                 {'oauth': {'oauth_grant_type': 'implicit'}})
        for kwargs in cases:
            with self.subTest(**kwargs):
                request, now = app_request(**kwargs)
                self.assertIs(self.run_policy(request, now), False)
                self.assertEqual(request.http_request.session, {})
        self.assertEqual(CACHE.calls, [])
        request, now = app_request(prompt='login', response_mode='query')
        self.assertIs(self.run_policy(request, now), True)
        request, now = app_request(prompt='login')
        request.http_request.META['QUERY_STRING'] += '&prompt=login'
        self.assertIs(self.run_policy(request, now), False)

    def test_fails_closed_without_event_elsewhere_or_in_a_policy_subprocess(self):
        request, now = app_request(prompt='login')
        request.http_request.event = None
        self.assertFalse(self.run_policy(request, now))
        request, now = app_request(prompt='login')
        with stock_imports(), patch('time.time', return_value=now), \
                patch('multiprocessing.current_process', return_value=NS(daemon=False)):
            self.assertFalse(expression('application-policy')(request))
        self.assertNotIn(LAST_LOGIN, request.http_request.session)
        request, now = app_request(prompt='login')
        request.http_request.path = '/api/v3/core/applications/'
        self.assertFalse(self.run_policy(request, now))
        self.assertEqual(CACHE.calls, [])

    def test_claim_is_made_last(self):
        source = BY_ID['application-policy']['attrs']['expression']
        success = source.index('if (proof is not None and isinstance(bound, str) and current == bound')
        claim = source.index('cache.add("animus-lan/claimed-login/" + str(event.pk), "claimed", 900)')
        self.assertLess(success, claim)
        self.assertLess(source.index('oauth_response_type'), claim)
        self.assertEqual(source.count('cache.add('), 1)
        self.assertNotIn('consumed-login', source)


def entry_key(entry):
    """(flow id, stage ref, order) of a flow-stage binding entry."""
    ids = entry['identifiers']
    stage = ids['stage'].value if ids['stage'].name == '!KeyOf' else ids['stage'].value[1][1]
    return ids['target'].value, stage, ids['order']


def final_bindings(document, flow):
    """Flow-stage bindings left after a full apply: later entries update earlier ones."""
    state = {}
    for entry in document['entries']:
        if entry['model'] != 'authentik_flows.flowstagebinding' or entry['identifiers']['target'] != Tag('!KeyOf', flow):
            continue
        key = entry_key(entry)[1:]
        if entry.get('state', 'present') == 'absent':
            state.pop(key, None)
        else:
            state[key] = {**state.get(key, {}), **entry.get('attrs', {})}
    return state


def policy_bindings_on(target_id, document=DOCUMENT):
    return [e for e in document['entries'] if e['model'] == 'authentik_policies.policybinding'
            and e['identifiers']['target'] == Tag('!KeyOf', target_id)]


class RedesignShapeTests(unittest.TestCase):
    """2026-11-23: gates on the granting stages, no authorization stages, pinned bindings."""

    def test_exact_normal_and_authorization_binding_sets(self):
        self.assertEqual(final_bindings(DOCUMENT, 'normal-flow'), {
            ('normal-identification', 10): GATED, ('totp-validation', 30): UNCONDITIONAL,
            ('login', 50): GATED, ('normal-denied', 60): GATED})
        self.assertEqual(final_bindings(DOCUMENT, 'authorization-flow'), {})
        absent = [entry_key(e) for e in ENTRIES if e['model'] == 'authentik_flows.flowstagebinding'
                  and e.get('state') == 'absent']
        self.assertEqual(sorted(absent), sorted([
            ('normal-flow', 'identification', 10), ('normal-flow', 'password', 20),
            ('normal-flow', 'normal-entry', 0), ('normal-flow', 'normal-final', 40),
            ('authorization-flow', 'authorization-entry', 0), ('authorization-flow', 'consent', 10),
            ('authorization-flow', 'authorization-final', 20)]))
        for entry in ENTRIES:
            if entry.get('state') == 'absent':
                # Guarded: in 2026.8.5 an unguarded absent entry with no row fails every re-apply.
                flow, stage, order = entry_key(entry)
                self.assertEqual(entry['conditions'], [Tag('!Find', [
                    'authentik_flows.flowstagebinding', ['target', Tag('!KeyOf', flow)],
                    ['stage', Tag('!KeyOf', stage)], ['order', order]])])

    def test_each_gate_has_exactly_one_pinned_binding(self):
        for target, policy in (('normal-identification-binding', 'entry-gate-policy'),
                               ('normal-login-binding', 'login-gate-policy'),
                               ('normal-denied-binding', 'denied-notice-policy')):
            with self.subTest(target=target):
                found = policy_bindings_on(target)
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]['identifiers'], {'target': Tag('!KeyOf', target),
                                                           'policy': Tag('!KeyOf', policy), 'order': 0})
                self.assertEqual(found[0]['attrs'], PINNED)
        all_bindings = [e for e in ENTRIES if e['model'] == 'authentik_policies.policybinding']
        self.assertEqual(len(all_bindings), 6)
        self.assertTrue(all(e['attrs'] == PINNED for e in all_bindings))

    def test_fail_closed_entry_order(self):
        """Every prefix of the file is fail-closed (defence in depth; the import is atomic)."""
        state = {'normal': {('normal-entry', 0): True, ('normal-final', 40): True},
                 'authz_prompts': True, 'app_policy_new': False, 'gates': set(), 'reevaluate': set()}
        granting = {('normal-identification', 10): 'entry-gate-policy', ('login', 50): 'login-gate-policy'}
        for index, entry in enumerate(ENTRIES):
            if entry.get('id') == 'application-policy':
                state['app_policy_new'] = True
            if entry['model'] == 'authentik_flows.flowstagebinding':
                flow, stage, order = entry_key(entry)
                if entry.get('state') == 'absent':
                    if flow == 'normal-flow' and (stage, order) in state['normal']:
                        state['normal'][stage, order] = False
                    if flow == 'authorization-flow':
                        state['authz_prompts'] = False
                elif (stage, order) in granting and entry['attrs'].get('re_evaluate_policies'):
                    # Switching to re-evaluation only after the gate is bound.
                    self.assertIn(granting[stage, order], state['gates'], f'entry {index}')
                    state['reevaluate'].add((stage, order))
            if entry['model'] == 'authentik_policies.policybinding' and entry['identifiers']['policy'].value in (
                    'entry-gate-policy', 'login-gate-policy'):
                state['gates'].add(entry['identifiers']['policy'].value)
            # Invariants after this entry.
            entry_guarded = state['normal']['normal-entry', 0] or (
                'entry-gate-policy' in state['gates'] and ('normal-identification', 10) in state['reevaluate'])
            login_guarded = state['normal']['normal-final', 40] or (
                'login-gate-policy' in state['gates'] and ('login', 50) in state['reevaluate'])
            self.assertTrue(entry_guarded and login_guarded, f'prefix up to entry {index} is not fail-closed')
            self.assertTrue(state['authz_prompts'] or state['app_policy_new'], f'entry {index}')
        self.assertFalse(state['normal']['normal-entry', 0] or state['normal']['normal-final', 40])
        self.assertFalse(state['authz_prompts'])

    def test_replaced_objects_stay_declared_for_the_rollback(self):
        for entry_id in ('normal-entry', 'normal-final', 'authorization-entry', 'authorization-final', 'consent',
                         'entry-policy', 'final-policy', 'authorization-entry-policy', 'authorization-final-policy',
                         *LABELS):
            self.assertIn(entry_id, BY_ID)
        previous = dict(zip((e.get('id') for e in REDESIGN_ROLLBACK_DOCUMENT['entries']),
                            raw_entries(REDESIGN_ROLLBACK.read_text())))
        forward = dict(zip((e.get('id') for e in ENTRIES), raw_entries(BLUEPRINT.read_text())))
        for entry_id in ('normal-entry', 'normal-final', 'authorization-entry', 'authorization-final',
                         'entry-policy', 'final-policy', 'authorization-entry-policy', 'authorization-final-policy',
                         'pkce-policy', 'assurance-mapping', *LABELS):
            with self.subTest(entry=entry_id):
                self.assertEqual(forward[entry_id], previous[entry_id])

    def test_enrollment_and_repair_unchanged_from_the_deployed_blueprint(self):
        for flow in ('enrollment-flow', 'repair-flow'):
            self.assertEqual(final_bindings(DOCUMENT, flow), final_bindings(REDESIGN_ROLLBACK_DOCUMENT, flow))
            self.assertEqual(policy_bindings_on(flow), [])

    def test_gates_fail_to_false_and_run_only_in_process(self):
        for policy, stage, order in (('entry-gate-policy', 'animus-lan-normal-identification', 10),
                                     ('login-gate-policy', 'animus-lan-login', 50)):
            source = BY_ID[policy]['attrs']['expression']
            self.assertTrue(source.rstrip().endswith('except Exception:\n    return False'), policy)
            self.assertIn('if type(process).__name__ != "_MainProcess" or process.daemon:\n        return False', source)
            self.assertIn(f'binding.stage.name != "{stage}" or binding.order != {order}', source)
            self.assertIn('key.startswith("user_switch_")', source)

    def test_notice_explains_only(self):
        source = BY_ID['denied-notice-policy']['attrs']['expression']
        self.assertTrue(source.rstrip().endswith('except Exception:\n    return False'))
        self.assertNotIn('ends without a session', source)
        self.assertIn('an eligible user still\n# completes and gets a code', source.replace('        # ', '# '))


def gate_request(policy, context, now_user=None, event=None, stage='animus-lan-normal-identification', order=10):
    binding = NS(stage=NS(name=stage), order=order, target_id=UUID(FLOW))
    plan = NS(flow_pk=FLOW, bindings=[binding], context=context)
    http = NS(session={SESSION_KEY: plan, SESSION_GET: {'next': authorize_url()}},
              user=now_user or user(None, is_authenticated=False), event=event)
    pending = context.get('pending_user')
    return NS(http_request=http, user=pending or http.user, context={'flow_plan': plan}, obj=None), plan


class GateTests(unittest.TestCase):
    def run_gate(self, policy, request, now=1000, **globals_):
        with stock_imports(), patch('time.time', return_value=now), patch('uuid.uuid4', return_value=UUID('a' * 32)):
            return expression(policy, **globals_)(request)

    def test_entry_gate_starts_the_clock_once_and_never_renews_it(self):
        request, plan = gate_request('entry-gate-policy', {})
        self.assertIs(self.run_gate('entry-gate-policy', request, 1000), True)
        self.assertEqual(plan.context['hub_p1_execution']['factor_time'], 1000)
        self.assertIs(self.run_gate('entry-gate-policy', request, 1250), True)
        self.assertEqual(plan.context['hub_p1_execution']['factor_time'], 1000)
        # A changed session user between the two requests is refused.
        request.http_request.user = user(8)
        request.http_request.event = NS(pk='event-b', user={'pk': 8})
        self.assertIs(self.run_gate('entry-gate-policy', request, 1100), False)

    def test_entry_gate_refuses_switch_restored_redirected_and_finished_plans(self):
        for extra in ({'user_switch_add_user': True}, {'user_switch_target_session': 'x'},
                      {'user_switch_from_user': user(8)}, {'is_restored': True}, {'is_redirected': True},
                      {'auth_method': 'password'}, {'auth_method_args': {}}):
            with self.subTest(extra=list(extra)):
                request, plan = gate_request('entry-gate-policy', dict(extra))
                self.assertIs(self.run_gate('entry-gate-policy', request), False)
                self.assertNotIn('hub_p1_execution', plan.context)
        request, plan = gate_request('entry-gate-policy', {})
        request.http_request.session[SESSION_KEY] = NS(**vars(plan))  # not the session's own plan
        self.assertIs(self.run_gate('entry-gate-policy', request), False)
        request, _ = gate_request('entry-gate-policy', {}, stage='animus-lan-login', order=50)
        self.assertIs(self.run_gate('entry-gate-policy', request), False)

    def login_context(self, **changes):
        context = {'hub_p1_execution': {'policy': POLICY, 'flow': FLOW, 'nonce': 'a' * 32, 'factor_time': 1000,
                                        'initial_session_user': None, 'initial_login_event': None},
                   'pending_user': user(), 'auth_method': 'password',
                   'auth_method_args': {'mfa_devices': [TOTPDevice()]}}
        context.update(changes)
        return context

    def test_login_gate_writes_the_marker_with_its_own_gate_key(self):
        request, plan = gate_request('login-gate-policy', self.login_context(), stage='animus-lan-login', order=50)
        self.assertIs(self.run_gate('login-gate-policy', request, 1200), True)
        args = plan.context['auth_method_args']
        self.assertEqual(args['hub_policy_gate'], 'animus-lan-login-gate')
        self.assertEqual(args['hub_policy_request'], request_hash(REQUEST))
        self.assertEqual((args['hub_policy_id'], args['hub_factor_time'], args['hub_policy_subject']), (POLICY, 1000, 7))

    def test_login_gate_refuses_every_ineligible_ceremony_without_a_marker(self):
        cases = {'stale': ({}, 1301), 'switch-add': ({'user_switch_add_user': True}, 1200),
                 'switch-target': ({'user_switch_target_session': 's'}, 1200),
                 'restored': ({'is_restored': True}, 1200), 'redirected': ({'is_redirected': True}, 1200),
                 'superuser': ({'pending_user': user(is_superuser=True)}, 1200),
                 'inactive': ({'pending_user': user(is_active=False)}, 1200),
                 'other-method': ({'auth_method': 'token'}, 1200),
                 'no-totp': ({'auth_method_args': {'mfa_devices': []}}, 1200),
                 'other-users-totp': ({'auth_method_args': {'mfa_devices': [TOTPDevice(8)]}}, 1200),
                 'unconfirmed-totp': ({'auth_method_args': {'mfa_devices': [TOTPDevice(confirmed=False)]}}, 1200),
                 'no-envelope': ({'hub_p1_execution': None}, 1200)}
        for label, (changes, now) in cases.items():
            with self.subTest(case=label):
                request, plan = gate_request('login-gate-policy', self.login_context(**changes),
                                             stage='animus-lan-login', order=50)
                self.assertIs(self.run_gate('login-gate-policy', request, now), False)
                self.assertNotIn('hub_policy_id', plan.context.get('auth_method_args') or {})
        request, plan = gate_request('login-gate-policy', self.login_context(), stage='animus-lan-login', order=50)
        with stock_imports(), patch('time.time', return_value=1200):
            self.assertIs(expression('login-gate-policy', ak_is_group_member=lambda *a, **k: False)(request), False)

    def test_login_gate_reads_the_account_again_from_the_database(self):
        # The plan's pending user is the copy stored at identification: a deactivation, a
        # deleted account or a superuser grant during the ceremony is seen only in the
        # database, and refuses before User Login creates a session or a login event.
        for label, row in {'deactivated': user(is_active=False), 'deleted': None,
                           'made-superuser': user(is_superuser=True)}.items():
            with self.subTest(case=label):
                request, plan = gate_request('login-gate-policy', self.login_context(), stage='animus-lan-login', order=50)
                self.assertIs(self.run_gate('login-gate-policy', request, 1200, ak_user_by=fake_user_by({7: row})), False)
                self.assertNotIn('hub_policy_id', plan.context['auth_method_args'])
        lookups, members = [], []

        def user_by(**filters):
            lookups.append(filters)
            return fake_user_by()(**filters)

        def is_member(account, **group):
            members.append((account, group))
            return True
        request, _ = gate_request('login-gate-policy', self.login_context(), stage='animus-lan-login', order=50)
        self.assertIs(self.run_gate('login-gate-policy', request, 1200, ak_user_by=user_by,
                                    ak_is_group_member=is_member), True)
        self.assertEqual(lookups, [{'pk': 7, 'is_active': True}])
        # Group membership is checked on the account read again, not on the stored copy.
        self.assertEqual(len(members), 1)
        self.assertIsNot(members[0][0], request.context['flow_plan'].context['pending_user'])
        self.assertEqual(members[0][1], {'name': 'animus-lan-normal'})

    def test_notice_shows_unless_this_ceremonys_qualified_login_happened(self):
        state = {'nonce': 'a' * 32}
        plan = NS(context={'hub_p1_execution': state})
        done = NS(pk='e', user={'pk': 7}, context={'auth_method_args': {'hub_policy_execution': 'a' * 32}})
        other = NS(pk='e', user={'pk': 7}, context={'auth_method_args': {'hub_policy_execution': 'b' * 32}})
        for http, expected in ((NS(user=user(None, is_authenticated=False), event=None), True),
                               (NS(user=user(), event=other), True), (NS(user=user(), event=done), False)):
            with stock_imports():
                self.assertIs(expression('denied-notice-policy')(NS(http_request=http, context={'flow_plan': plan})),
                              expected)
        with stock_imports():  # its own error removes the notice: explanation only
            self.assertIs(expression('denied-notice-policy')(NS(http_request=None, context={})), False)


class RedesignRollbackTests(unittest.TestCase):
    """The rollback chain: 2026-11-23 rollback = deployed c693d82 + added removals only."""

    def test_rollback_is_the_deployed_blueprint_plus_added_entries_only(self):
        text = REDESIGN_ROLLBACK.read_text()
        self.assertTrue(text.startswith('# ROLLBACK of the 2026-11-23 P1 sign-in redesign.'))
        body = text[text.index('# Application issuance is CLOSED by default.'):]
        deployed = subprocess.run(['git', '-C', str(STACK), 'show', 'c693d82:stacks/ct-animus/blueprints/alkion-lan.yaml'],
                                  capture_output=True, text=True, check=True).stdout
        self.assertEqual(hashlib.sha256(deployed.encode()).hexdigest(), DEPLOYED_SHA256)
        old, new = deployed.splitlines(), body.splitlines()
        inserted = []
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            self.assertIn(op, ('equal', 'insert'))
            if op == 'insert':
                inserted += new[j1:j2]
        ids = [line.strip() for line in inserted if line.strip().startswith('id: ')]
        self.assertEqual(ids, ['id: rb-identification-binding', 'id: rb-login-binding'])

    def test_added_entries_remove_gates_and_notice_and_reset_the_engine_mode(self):
        added = [e for e in REDESIGN_ROLLBACK_DOCUMENT['entries']
                 if (e['model'] == 'authentik_policies.policybinding' and e.get('state') == 'absent')
                 or e.get('attrs') == {'policy_engine_mode': 'any'}
                 or (e.get('state') == 'absent' and e['identifiers'].get('order') == 60)]
        self.assertEqual(len(added), 5)
        gates = [e for e in added if e['model'] == 'authentik_policies.policybinding'
                 and e['identifiers']['target'].value.startswith('rb-')]
        self.assertEqual(sorted(e['identifiers']['policy'].value[1][1] for e in gates),
                         ['animus-lan-entry-gate', 'animus-lan-login-gate'])
        for entry in gates:
            self.assertEqual(entry['state'], 'absent')
            self.assertEqual(entry['conditions'][0].value[0], 'authentik_policies.policybinding')
        self.assertEqual(final_bindings(REDESIGN_ROLLBACK_DOCUMENT, 'normal-flow'), {
            ('normal-entry', 0): UNCONDITIONAL, ('normal-identification', 10): {**UNCONDITIONAL, 'policy_engine_mode': 'any'},
            ('totp-validation', 30): UNCONDITIONAL, ('normal-final', 40): UNCONDITIONAL,
            ('login', 50): {**UNCONDITIONAL, 'policy_engine_mode': 'any'}})
        self.assertEqual([k for k in final_bindings(REDESIGN_ROLLBACK_DOCUMENT, 'authorization-flow')],
                         [('authorization-entry', 0), ('consent', 10), ('authorization-final', 20)])
        self.assertEqual(REDESIGN_ROLLBACK_DOCUMENT['metadata'], DOCUMENT['metadata'])
        self.assertEqual(REDESIGN_ROLLBACK_DOCUMENT['context'], DOCUMENT['context'])

    def test_previous_rollback_is_marked_superseded(self):
        header = ROLLBACK.read_text().split('version: 1')[0]
        self.assertIn('SUPERSEDED 2026-11-23 by rollback/alkion-lan-20261123-rollback.yaml', header)
        self.assertIn('every login would be refused', header)
        self.assertIn('supersedes rollback/alkion-lan-20261118-rollback.yaml', REDESIGN_ROLLBACK.read_text())

    def test_only_the_blueprint_lives_in_the_watched_directory(self):
        self.assertEqual({path.name for path in (STACK / 'blueprints').iterdir()}, {'alkion-lan.yaml'})
        for rollback in (ROLLBACK, REDESIGN_ROLLBACK):
            self.assertEqual(rollback.parent, STACK / 'rollback')

if __name__ == '__main__':
    unittest.main(verbosity=1)
