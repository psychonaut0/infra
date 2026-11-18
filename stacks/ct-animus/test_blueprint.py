#!/usr/bin/env python3
"""Synthetic checks of the deployed alkion-lan blueprint; no container or provider.

Runs the PKCE, application and authorization-entry expressions from
blueprints/alkion-lan.yaml against stub requests, in the style of
qualification/p1/test_candidate.py, and mirrors Authentik's !Context/!AtIndex
resolution for the issuance switch. The real policy engine, flow planner and
blueprint importer remain runtime checks.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch
from uuid import UUID

import yaml

STACK = Path(__file__).resolve().parent
BLUEPRINT = STACK / 'blueprints' / 'alkion-lan.yaml'
ROLLBACK = STACK / 'rollback' / 'alkion-lan-20261118-rollback.yaml'
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


def expression(entry_id, **globals_):
    source = BY_ID[entry_id]['attrs']['expression']
    namespace = {'ak_is_group_member': lambda *a, **k: True} | globals_
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
    with patch.dict('sys.modules', {'authentik.events.signals': signals, 'authentik.flows.views.executor': executor,
                                    'authentik.providers.oauth2.views.authorize': authorize_view}):
        yield


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
        with stock_imports(), patch('time.time', return_value=1200):
            for client_id, expected in (('alkion-lan', True), ('other-client', False)):
                subject, event, provider = qualified(client_id)
                request = NS(http_request=NS(user=subject, event=event, path='/application/o/authorize/',
                                             method='GET', GET={}, session={}),
                             user=subject, obj=NS(provider=provider))
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

    def test_other_bindings_stay_unconditional_and_ordered(self):
        bindings = [e for e in ENTRIES if e['model'] == 'authentik_policies.policybinding']
        self.assertEqual([(e['identifiers']['policy'].value, e['identifiers']['order']) for e in bindings],
                         [('pkce-policy', 0), ('application-policy', 1), ('issuance-closed-policy', 2)])
        for entry in bindings:
            self.assertEqual(entry['identifiers']['target'], Tag('!KeyOf', 'application'))
            self.assertEqual(entry['attrs'], {'negate': False, 'failure_result': False})
            self.assertEqual('state' in entry, entry['identifiers']['policy'].value == 'issuance-closed-policy')
        self.assertEqual(BY_ID['application']['attrs']['policy_engine_mode'], 'all')
        switched = [e for e in ENTRIES if 'state' in e or 'conditions' in e]
        self.assertEqual([e['model'] for e in switched], ['authentik_flows.flowstagebinding'] * 2
                         + ['authentik_policies.policybinding'])
        self.assertTrue(all(e.get('state') == 'absent' for e in ENTRIES if 'conditions' in e))

    def test_enrollment_closed_no_recovery_and_no_seeds(self):
        models = {e['model'] for e in ENTRIES}
        for forbidden in ('authentik_core.user', 'authentik_core.token', 'authentik_brands.brand',
                          'authentik_stages_deny.denystage'):
            self.assertNotIn(forbidden, models)
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

    def test_normal_flow_binds_combined_page_and_removes_old_bindings(self):
        guards = [e.get('conditions') for e in ENTRIES if e.get('state') == 'absent']
        self.assertEqual(guards, [
            [Tag('!Find', ['authentik_flows.flowstagebinding', ['target', Tag('!KeyOf', 'normal-flow')],
                           ['stage', Tag('!KeyOf', stage)], ['order', order]])]
            for stage, order in (('identification', 10), ('password', 20))])
        self.assertEqual(bindings(DOCUMENT, 'normal-flow'), [
            (0, Tag('!KeyOf', 'normal-entry'), 'present', UNCONDITIONAL),
            (10, Tag('!KeyOf', 'identification'), 'absent', None),
            (20, Tag('!KeyOf', 'password'), 'absent', None),
            (10, Tag('!KeyOf', 'normal-identification'), 'present', UNCONDITIONAL),
            (30, Tag('!KeyOf', 'totp-validation'), 'present', UNCONDITIONAL),
            (40, Tag('!KeyOf', 'normal-final'), 'present', UNCONDITIONAL),
            (50, Tag('!KeyOf', 'login'), 'present', UNCONDITIONAL)])
        # The absent entries come before the replacement, which comes after its stage.
        ids = [e.get('id') for e in ENTRIES]
        self.assertLess(ids.index('password'), ids.index('normal-identification'))
        self.assertLess(ids.index('normal-identification'), ids.index('totp-validation'))

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

    def test_consent_is_remembered_permanently(self):
        self.assertEqual(BY_ID['consent']['attrs'], {'mode': 'permanent', 'consent_expire_in': 'weeks=4'})
        self.assertEqual([(o, s) for o, s, _, _ in bindings(DOCUMENT, 'authorization-flow')], [
            (0, Tag('!KeyOf', 'authorization-entry')), (10, Tag('!KeyOf', 'consent')),
            (20, Tag('!KeyOf', 'authorization-final'))])

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


def app_request(prompt=None, challenge=CHALLENGE, bound=CHALLENGE, now=1200, method='GET'):
    subject, event, provider = qualified()
    event.context['auth_method_args']['hub_policy_request'] = bound
    query = {'code_challenge': challenge} | ({'prompt': prompt} if prompt is not None else {})
    http = NS(user=subject, event=event, path='/application/o/authorize/', method=method, GET=query, session={})
    return NS(http_request=http, user=subject, obj=NS(provider=provider)), now


class PromptLoginTests(unittest.TestCase):
    """prompt=login re-authenticates every Connect once and then continues that request."""

    AUTHORIZE = '/application/o/authorize/?client_id=alkion-lan&code_challenge=' + CHALLENGE + '&prompt=login'

    def test_final_binds_the_ceremony_to_its_authorize_request(self):
        request, plan = final_request(self.AUTHORIZE)
        with stock_imports(), patch('time.time', return_value=1100):
            self.assertIs(expression('final-policy')(request), True)
        self.assertEqual(plan.context['auth_method_args']['hub_policy_request'], CHALLENGE)
        self.assertNotIn('redirect', plan.context)
        self.assertEqual(plan.context['auth_method_args']['hub_factor_time'], 1000)

    def test_final_never_binds_another_target(self):
        for target in (None, 'http://evil.example/application/o/authorize/?code_challenge=' + CHALLENGE,
                       '//evil.example/application/o/authorize/?code_challenge=' + CHALLENGE,
                       '/application/o/token/?code_challenge=' + CHALLENGE, '/application/o/authorize/',
                       '/application/o/authorize/?code_challenge=' + 'A' * 42,
                       '/application/o/authorize/?code_challenge=' + CHALLENGE + '&code_challenge=' + CHALLENGE):
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

    def test_without_prompt_login_access_is_unchanged(self):
        for kwargs, expected in (({}, True), ({'now': 1400}, False), ({'bound': None}, True),
                                 ({'prompt': 'none'}, True), ({'prompt': 'login', 'method': 'POST', 'now': 1400}, False)):
            with self.subTest(**kwargs):
                request, now = app_request(**kwargs)
                with stock_imports(), patch('time.time', return_value=now):
                    self.assertIs(expression('application-policy')(request), expected)
                self.assertNotIn(LAST_LOGIN, request.http_request.session)

    def test_prompt_login_continues_only_the_bound_fresh_ceremony(self):
        request, now = app_request(prompt='login')
        with stock_imports(), patch('time.time', return_value=now):
            self.assertIs(expression('application-policy')(request), True)
        self.assertEqual(request.http_request.session[LAST_LOGIN], 'animus-lan-continue:event-a')

    def test_prompt_login_sends_every_other_session_to_reauthentication(self):
        cases = ({'now': 1400}, {'challenge': 'B' * 43}, {'bound': None}, {'challenge': None})
        for kwargs in cases:
            with self.subTest(**kwargs):
                request, now = app_request(prompt='login', **kwargs)
                with stock_imports(), patch('time.time', return_value=now):
                    self.assertIs(expression('application-policy')(request), True)
                # Equal to the current login: the authorize view re-authenticates first.
                self.assertEqual(request.http_request.session[LAST_LOGIN], 'event-a')
        request, now = app_request(prompt='consent login')
        request.user.is_superuser = True
        with stock_imports(), patch('time.time', return_value=now):
            self.assertIs(expression('application-policy')(request), True)
        self.assertEqual(request.http_request.session[LAST_LOGIN], 'event-a')

    def test_prompt_login_fails_closed_without_event_or_in_a_policy_subprocess(self):
        request, now = app_request(prompt='login')
        request.http_request.event = None
        with stock_imports(), patch('time.time', return_value=now):
            self.assertFalse(expression('application-policy')(request))
        request, now = app_request(prompt='login', now=1400)
        with stock_imports(), patch('time.time', return_value=now), \
                patch('multiprocessing.current_process', return_value=NS(daemon=False)):
            self.assertFalse(expression('application-policy')(request))
        self.assertNotIn(LAST_LOGIN, request.http_request.session)
        request, now = app_request(prompt='login', now=1400)
        request.http_request.path = '/api/v3/core/applications/'
        with stock_imports(), patch('time.time', return_value=now):
            self.assertFalse(expression('application-policy')(request))


if __name__ == '__main__':
    unittest.main(verbosity=1)
