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
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch
from uuid import UUID

import yaml

BLUEPRINT = Path(__file__).resolve().parent / 'blueprints' / 'alkion-lan.yaml'
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


@contextmanager
def stock_imports():
    signals = ModuleType('authentik.events.signals')
    signals.get_login_event = lambda obj: getattr(obj, 'event', None)
    executor = ModuleType('authentik.flows.views.executor')
    executor.SESSION_KEY_PLAN = SESSION_KEY
    with patch.dict('sys.modules', {'authentik.events.signals': signals, 'authentik.flows.views.executor': executor}):
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
                request = NS(http_request=NS(user=subject, event=event, path='/application/o/authorize/'),
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
        self.assertEqual(len(switched), 1)

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


if __name__ == '__main__':
    unittest.main(verbosity=1)
