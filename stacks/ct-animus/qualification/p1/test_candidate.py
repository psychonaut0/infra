#!/usr/bin/env python3
"""Synthetic expression/model checks, not actual HTTP/provider qualification."""
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch
from uuid import UUID
import yaml

ROOT = Path(__file__).resolve().parent
FLOW = '1' * 32
AUTHZ = '2' * 32
POLICY = 'animus-password-totp-mechanism-v2'
SESSION_KEY = 'authentik/flows/plan'
spec = importlib.util.spec_from_file_location('p1_render', ROOT / 'render.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


def expression(name, **globals_):
    source = renderer.expression(name)
    namespace = {'ak_is_group_member': lambda *a, **k: True, 'ak_user_has_authenticator': lambda *a, **k: False} | globals_
    wrapped = 'def evaluate(request=None, provider=None, token=None):\n' + ''.join('    ' + line + '\n' for line in source.splitlines())
    exec(compile(wrapped, name, 'exec'), namespace)
    return namespace['evaluate']


@contextmanager
def stock_imports():
    signals = ModuleType('authentik.events.signals')
    signals.get_login_event = lambda obj: getattr(obj, 'event', None)
    executor = ModuleType('authentik.flows.views.executor')
    executor.SESSION_KEY_PLAN = SESSION_KEY
    with patch.dict('sys.modules', {'authentik.events.signals': signals, 'authentik.flows.views.executor': executor}):
        yield


def user(pk=7, **changes):
    return NS(**({'pk': pk, 'is_active': True, 'is_superuser': False, 'is_authenticated': True} | changes))


def request(stage='normal-entry', flow=FLOW):
    binding = NS(stage=NS(name='p1-mechanism-' + stage), order=0, target_id=UUID(flow))
    plan = NS(flow_pk=flow, context={}, bindings=[binding])
    http = NS(session={SESSION_KEY: plan}, user=user(None, is_authenticated=False), event=None, path='/api/v3/flows/executor/p1-mechanism-normal/')
    return NS(http_request=http, user=http.user, context={}, obj=None)


def plan(req):
    return req.http_request.session[SESSION_KEY]


def started():
    req = request()
    with stock_imports(), patch('time.time', return_value=1000.25):
        assert expression('entry.expr')(req) is True
    return req


class TOTPDevice:
    def __init__(self, user_id=7, confirmed=True):
        self.user_id = user_id
        self.confirmed = confirmed


def factored():
    req = started()
    plan(req).bindings[0].stage.name = 'p1-mechanism-normal-final'
    plan(req).context.update({'pending_user': user(), 'auth_method': 'password',
                             'auth_method_args': {'mfa_devices': [TOTPDevice()]}})
    return req


def qualified():
    req = factored()
    with stock_imports(), patch('time.time', return_value=1100):
        assert expression('final.expr')(req)
    args = copy.deepcopy(plan(req).context['auth_method_args'])
    args['mfa_devices'] = [{'app': 'authentik_stages_authenticator_totp', 'model_name': 'totpdevice'}]
    timestamp = datetime.fromtimestamp(1100.5, timezone.utc)
    event = NS(pk='event-a', action='login', user={'pk': 7}, created=timestamp,
               context={'auth_method': 'password', 'auth_method_args': args})
    provider = NS(pk=9, client_id='alkion-lan', authentication_flow_id=UUID(FLOW))
    return user(), event, provider


class TaggedLoader(yaml.SafeLoader):
    pass
TaggedLoader.add_constructor('!Find', lambda loader, node: {'Find': loader.construct_sequence(node, deep=True)})
TaggedLoader.add_constructor('!KeyOf', lambda loader, node: {'KeyOf': loader.construct_scalar(node)})


class CandidateTests(unittest.TestCase):
    def test_entry_creates_server_bound_and_never_renews(self):
        req = started()
        before = copy.deepcopy(plan(req).context)
        with stock_imports(), patch('time.time', return_value=2000):
            self.assertFalse(expression('entry.expr')(req))
        self.assertEqual(plan(req).context, before)

    def test_new_clean_execution_has_new_bound_nonce(self):
        first = plan(started()).context['hub_p1_execution']
        req = request()
        with stock_imports(), patch('time.time', return_value=2000.5):
            self.assertTrue(expression('entry.expr')(req))
        second = plan(req).context['hub_p1_execution']
        self.assertNotEqual(first['nonce'], second['nonce'])
        self.assertEqual((first['factor_time'], second['factor_time']), (1000, 2000))

    def test_inherited_restored_and_nonentry_context_cannot_initialize(self):
        for key in ('hub_p1_execution', 'auth_method', 'auth_method_args', 'is_restored', 'is_redirected'):
            req = request()
            plan(req).context[key] = {}
            with stock_imports(): self.assertFalse(expression('entry.expr')(req))
        req = request('normal-final')
        with stock_imports(): self.assertFalse(expression('entry.expr')(req))

    def test_submitted_prompt_query_or_body_cannot_choose_bound(self):
        req = request()
        req.context['prompt_data'] = {'hub_factor_time': 999999, 'hub_p1_execution': {'nonce': 'client'}}
        req.http_request.GET = req.context['prompt_data']
        with stock_imports(), patch('time.time', return_value=1000):
            self.assertTrue(expression('entry.expr')(req))
        self.assertEqual(plan(req).context['hub_p1_execution']['factor_time'], 1000)

    def test_fork_unknown_or_daemonic_modes_fail_positive_validation(self):
        for name, req in [('entry.expr', request()), ('final.expr', factored())]:
            with stock_imports(), patch('multiprocessing.current_process', return_value=NS(daemon=False)):
                self.assertFalse(expression(name)(req))

    def test_final_requires_complete_fresh_ceremony_and_preserves_bound(self):
        req = factored()
        state = copy.deepcopy(plan(req).context['hub_p1_execution'])
        for now in (1100, 1250, 1300):
            with stock_imports(), patch('time.time', return_value=now):
                self.assertTrue(expression('final.expr')(req))
            self.assertEqual(plan(req).context['hub_p1_execution'], state)
        self.assertEqual(plan(req).context['auth_method_args']['hub_factor_time'], 1000)

    def test_pause_after_each_factor_cannot_freshen_time(self):
        for stage in ('password', 'totp'):
            with self.subTest(stage=stage):
                req = factored()
                with stock_imports(), patch('time.time', return_value=1300.001):
                    self.assertFalse(expression('final.expr')(req))
                self.assertNotIn('hub_authentication_policy', plan(req).context['auth_method_args'])

    def test_missing_future_malformed_time_cannot_mark(self):
        for value in (None, True, -1, 1400, '1000'):
            req = factored()
            plan(req).context['hub_p1_execution']['factor_time'] = value
            with stock_imports(), patch('time.time', return_value=1100):
                self.assertFalse(expression('final.expr')(req))

    def test_mfa_requires_actual_confirmed_same_user_totp(self):
        for devices in ([], [NS(user_id=7, confirmed=True)], [TOTPDevice(8)], [TOTPDevice(confirmed=False)]):
            req = factored();plan(req).context['auth_method_args']['mfa_devices'] = devices
            with stock_imports(), patch('time.time', return_value=1100):
                self.assertFalse(expression('final.expr')(req))

    def test_inactive_admin_or_ineligible_subject_cannot_mark(self):
        for change in ({'is_active': False}, {'is_superuser': True}):
            req = factored();plan(req).context['pending_user'] = user(**change)
            with stock_imports(), patch('time.time', return_value=1100): self.assertFalse(expression('final.expr')(req))
        with stock_imports(), patch('time.time', return_value=1100):
            self.assertFalse(expression('final.expr', ak_is_group_member=lambda *a, **k: False)(factored()))

    def test_session_identity_switch_before_login_is_rejected(self):
        req = factored();req.http_request.user = user(8)
        req.http_request.event = NS(pk='other', user={'pk': 8})
        with stock_imports(), patch('time.time', return_value=1100): self.assertFalse(expression('final.expr')(req))

    def test_app_access_requires_exact_current_qualified_event(self):
        subject, event, provider = qualified()
        req = NS(http_request=NS(user=subject, event=event, path='/application/o/authorize/'), user=subject, obj=NS(provider=provider))
        with stock_imports(), patch('time.time', return_value=1200):
            self.assertTrue(expression('application.expr')(req))
            req.http_request.user = user(8)
            self.assertFalse(expression('application.expr')(req))
            req.http_request.user = subject
            del event.context['auth_method_args']['hub_authentication_policy']
            self.assertFalse(expression('application.expr')(req))

    def test_stale_sso_and_operator_event_cannot_authorize(self):
        subject, event, provider = qualified()
        req = NS(http_request=NS(user=subject, event=event, path='/application/o/authorize/'), user=subject, obj=NS(provider=provider))
        with stock_imports(), patch('time.time', return_value=2000): self.assertFalse(expression('application.expr')(req))
        subject.is_superuser = True
        with stock_imports(), patch('time.time', return_value=1200): self.assertFalse(expression('application.expr')(req))

    def test_authz_snapshot_rejects_event_identity_or_parameter_switch(self):
        subject, event, provider = qualified()
        req = request('authorization-entry', AUTHZ);req.user=subject;req.http_request.user=subject;req.http_request.event=event
        params = NS(provider=provider, client_id='alkion-lan', grant_type='authorization_code', code_challenge_method='S256', code_challenge='A'*43, redirect_uri='dev.animusstudio.alkion.dev:/oauth2redirect', response_type='code')
        plan(req).context.update({'goauthentik.io/providers/oauth2/params': params, 'application': NS(provider_id=9)})
        with stock_imports(), patch('time.time', return_value=1200):
            self.assertTrue(expression('authorization-entry.expr')(req))
            original = copy.deepcopy(plan(req).context['hub_p1_authorization'])
            plan(req).bindings[0].stage.name = 'p1-mechanism-authorization-final'
            self.assertTrue(expression('authorization-final.expr')(req))
            event.pk = 'replacement'
            self.assertFalse(expression('authorization-final.expr')(req))
            event.pk = 'event-a';params.code_challenge = 'B'*43
            self.assertFalse(expression('authorization-final.expr')(req))
            self.assertEqual(plan(req).context['hub_p1_authorization'], original)

    def test_claims_bind_original_exact_event_and_never_freshen_refresh(self):
        subject, event, provider = qualified()
        token = NS(provider_id=9, user_id=7, user=subject, session=NS(event=event), auth_time=event.created)
        with stock_imports(), patch('time.time', return_value=2000):
            self.assertEqual(expression('claims.expr')(provider=provider, token=token), {'hub_authentication_policy': POLICY, 'hub_factor_time': 1000})
            event.created = datetime.fromtimestamp(1100.6, timezone.utc)
            self.assertEqual(expression('claims.expr')(provider=provider, token=token), {})

    def test_pkce_absent_plain_and_callback_negatives(self):
        context = {'oauth_grant_type': 'authorization_code', 'oauth_response_type': 'code', 'oauth_redirect_uri': 'dev.animusstudio.alkion.dev:/oauth2redirect', 'oauth_code_challenge_method': 'S256', 'oauth_code_challenge': 'A'*43}
        req = NS(context=context, http_request=NS(path='/application/o/authorize/'))
        self.assertTrue(expression('pkce.expr')(req))
        for change in ({'oauth_code_challenge_method': None}, {'oauth_code_challenge_method': 'plain'}, {'oauth_code_challenge': None}, {'oauth_redirect_uri': 'other:/redirect'}):
            req.context=context|change
            self.assertFalse(expression('pkce.expr')(req))

    def test_blueprint_uses_unconditional_prompt_validation(self):
        document = yaml.load(renderer.render(), Loader=TaggedLoader)
        entries = document['entries'];by_id={e['id']:e for e in entries if 'id' in e}
        self.assertEqual((ROOT/'candidate.yaml').read_text(), renderer.render())
        self.assertFalse(any(e['model'] in ('authentik_core.user','authentik_stages_deny.denystage') for e in entries))
        bindings=[e for e in entries if e['model']=='authentik_flows.flowstagebinding']
        self.assertTrue(all(not e['attrs']['re_evaluate_policies'] and not e['attrs']['evaluate_on_plan'] for e in bindings))
        self.assertTrue(all(e['attrs']['invalid_response_action']=='retry' for e in bindings))
        normal=sorted((e for e in bindings if e['identifiers']['target']=={'KeyOf':'normal-flow'}),key=lambda e:e['identifiers']['order'])
        self.assertEqual([e['identifiers']['stage']['KeyOf'] for e in normal],['normal-entry','identification','password','totp-validation','normal-final','login'])
        for stage in ('normal-entry','normal-final','authorization-entry','authorization-final'):
            self.assertTrue(by_id[stage]['attrs']['validation_policies'])
        self.assertEqual(by_id['totp-validation']['attrs']['device_classes'],['totp'])
        self.assertEqual(by_id['totp-validation']['attrs']['last_auth_threshold'],'seconds=0')
        self.assertEqual(by_id['totp-validation']['attrs']['not_configured_action'],'deny')
        for flow in ('enrollment-flow','repair-flow'):
            self.assertFalse(any(e['identifiers']['target']=={'KeyOf':flow} and e['identifiers']['stage']=={'KeyOf':'login'} for e in bindings))

    def test_initial_app_closure_and_explicit_separate_experiment(self):
        closed = yaml.load(renderer.render(), Loader=TaggedLoader)
        opened = yaml.load(renderer.render(True), Loader=TaggedLoader)
        old = next(e for e in closed['entries'] if e.get('id')=='issuance-closed-policy')
        new = next(e for e in opened['entries'] if e.get('id')=='issuance-closed-policy')
        self.assertIn('return False',old['attrs']['expression'])
        self.assertIn('return True',new['attrs']['expression'])
        for entry in closed['entries']:
            if entry['model']=='authentik_policies.policybinding':
                self.assertEqual(entry['attrs'],{'negate':False,'failure_result':False})
        self.assertEqual(next(e for e in closed['entries'] if e.get('id')=='application')['attrs']['policy_engine_mode'],'all')


if __name__ == '__main__': unittest.main(verbosity=1)
