"""Synthetic fixture control for the P1 redesign spike, run only inside the isolated worker.

Executed by run.sh as `ak shell -c "exec(open('/t4/seed.py').read())"` with
T4_PHASE set. It applies blueprint files through Authentik's own Importer with
the owner-commissioning instance context (exactly what a BlueprintInstance
apply does), creates synthetic users, and prints one JSON line prefixed
`T4RESULT ` with structural inventory only: no secrets, hashes or tokens.
Synthetic passwords and TOTP keys go only to /state/users.json (mode 0600).
"""
import json
import os
import secrets
from pathlib import Path

from authentik.blueprints.v1.importer import Importer
from authentik.core.models import Group, User
from authentik.flows.models import Flow, FlowStageBinding
from authentik.stages.authenticator_totp.models import TOTPDevice
from authentik.stages.consent.models import ConsentStage, UserConsent
from authentik.stages.identification.models import IdentificationStage
from authentik.stages.prompt.models import PromptStage

PHASE = os.environ["T4_PHASE"]
CONTEXT = {"animus_issuance_profile": "owner-commissioning"}
FILES = {"base": "/state/alkion-lan.base.yaml", "forward": "/t4/alkion-lan.candidate.yaml",
         "rollback": "/t4/alkion-lan.rollback.yaml"}
# Fault injection targets (policy names); restored by re-applying "forward".
INJECTABLE = {"animus-lan-entry-gate", "animus-lan-login-gate", "animus-lan-denied-notice",
              "animus-lan-application", "animus-lan-pkce"}
# name: (normal group member, superuser, active, has TOTP)
USERS = {
    "t4-owner": (True, False, True, True), "t4-other": (True, False, True, True),
    "t4-slow-a": (True, False, True, True), "t4-slow-b": (True, False, True, True),
    "t4-slow-c": (True, False, True, True), "t4-slow-c-other": (True, False, True, True),
    "t4-notp": (True, False, True, False), "t4-nogroup": (False, False, True, True),
    "t4-inactive": (True, False, False, True), "t4-admin": (True, True, True, True),
    "p1r-slow-d": (True, False, True, True), "p1r-slow-e": (True, False, True, True),
    "p1r-inj-a": (True, False, True, True), "p1r-inj-b": (True, False, True, True),
    "p1r-inj-c": (True, False, True, True), "p1r-inj-nogroup": (False, False, True, True),
    "p1r-forge": (True, False, True, True), "p1r-replay": (True, False, True, True),
}


def bindings(slug):
    from authentik.policies.models import PolicyBinding
    flow = Flow.objects.get(slug=slug)
    return [[b.order, b.stage.name, b.evaluate_on_plan, b.re_evaluate_policies, b.invalid_response_action,
             [[p.policy.name if p.policy else None, p.negate, p.failure_result, p.enabled]
              for p in PolicyBinding.objects.filter(target=b.pbm_uuid).order_by("order")]]
            for b in FlowStageBinding.objects.filter(target=flow).select_subclasses().order_by("order", "stage__name")]


def inventory():
    result = {flow: bindings(flow) for flow in (
        "animus-lan-normal", "animus-lan-enrollment", "animus-lan-repair", "animus-lan-authorization")}
    consent = ConsentStage.objects.get(name="animus-lan-consent")
    result["consent"] = [consent.mode, consent.consent_expire_in]
    result["prompt_fields"] = {stage.name: [[p.name, p.field_key, p.type, p.required, p.label] for p in stage.fields.order_by("name")]
                               for stage in PromptStage.objects.filter(name__startswith="animus-lan-").order_by("name")}
    result["identification"] = {}
    for stage in IdentificationStage.objects.filter(name__startswith="animus-lan-").order_by("name"):
        result["identification"][stage.name] = {
            "user_fields": stage.user_fields, "password_stage": stage.password_stage.name if stage.password_stage else None,
            "captcha_stage": stage.captcha_stage_id, "webauthn_stage": stage.webauthn_stage_id,
            "case_insensitive_matching": stage.case_insensitive_matching, "show_matched_user": stage.show_matched_user,
            "pretend_user_exists": stage.pretend_user_exists, "enable_remember_me": stage.enable_remember_me,
            "enrollment_flow": stage.enrollment_flow_id, "recovery_flow": stage.recovery_flow_id,
            "passwordless_flow": stage.passwordless_flow_id, "sources": stage.sources.count(),
            "show_source_labels": stage.show_source_labels}
    result["user_consents"] = UserConsent.objects.filter(application__slug="alkion-lan").count()
    return result


def apply(name):
    importer = Importer.from_string(Path(FILES[name]).read_text(), CONTEXT)
    valid, logs = importer.validate()
    applied = importer.apply() if valid else False
    return {"valid": valid, "applied": applied,
            "warnings": [str(log.event)[:200] for log in logs if getattr(log, "log_level", "") in ("warning", "error")]}


def ready():
    from authentik.crypto.models import CertificateKeyPair
    from authentik.providers.oauth2.models import ScopeMapping
    return {"ready": CertificateKeyPair.objects.filter(name="authentik Self-signed Certificate").exists()
            and Flow.objects.filter(slug="default-provider-invalidation-flow").exists()
            and Flow.objects.filter(slug="default-authentication-flow").exists()
            and ScopeMapping.objects.filter(managed="goauthentik.io/providers/oauth2/scope-profile").exists()
            and ScopeMapping.objects.filter(managed="goauthentik.io/providers/oauth2/scope-offline_access").exists()}


def seed_users():
    path = Path("/state/users.json")
    normal = Group.objects.get(name="animus-lan-normal")
    users = {}
    for name, (member, superuser, active, totp) in USERS.items():
        user = User.objects.create(username=name, name=name, is_active=active)
        password = secrets.token_urlsafe(24)
        user.set_password(password)
        user.save()
        if member:
            normal.users.add(user)
        if superuser:
            # 2026.8 exposes superuser through group membership.
            admins, _ = Group.objects.get_or_create(name="t4-superusers", defaults={"is_superuser": True})
            admins.users.add(user)
        entry = {"password": password, "totp_key": None}
        if totp:
            device = TOTPDevice.objects.create(user=user, name="synthetic", confirmed=True)
            entry["totp_key"] = device.key
        users[name] = entry
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(users, stream)
    return {"users": sorted(users), "superuser": User.objects.get(username="t4-admin").is_superuser,
            "inactive_active_flag": User.objects.get(username="t4-inactive").is_active}


def inject(name):
    """Make one policy raise inside the expression (PolicyException -> failure_result)."""
    from authentik.policies.expression.models import ExpressionPolicy
    if name not in INJECTABLE:
        raise SystemExit("not injectable")
    return {"injected": name, "rows": ExpressionPolicy.objects.filter(name=name).update(
        expression='raise ValueError("p1r-injected fault")\n')}


def negate(name):
    """Misconfiguration probe: invert the binding of one gate policy."""
    from authentik.policies.models import PolicyBinding
    if name not in INJECTABLE:
        raise SystemExit("not negatable")
    return {"negated": name, "rows": PolicyBinding.objects.filter(policy__name=name).update(negate=True)}


def forge(variant):
    """A FlowToken whose plan claims a finished ceremony (login variant) or a fresh plan
    (full variant). The executor marks any token plan is_restored; nothing else differs."""
    import time
    import uuid
    from datetime import timedelta
    from django.utils.timezone import now
    from authentik.flows.markers import ReevaluateMarker, StageMarker
    from authentik.flows.models import FlowToken
    from authentik.flows.planner import FlowPlan
    flow = Flow.objects.get(slug="animus-lan-normal")
    user = User.objects.get(username="p1r-forge")
    device = TOTPDevice.objects.get(user=user)
    plan = FlowPlan(flow_pk=flow.pk.hex)
    fsbs = list(FlowStageBinding.objects.filter(target=flow).order_by("order"))
    keep = {"login": {50, 60}, "full": {10, 30, 50, 60}}[variant]
    for binding in fsbs:
        if binding.order in keep:
            plan.append(binding, ReevaluateMarker(binding=binding) if binding.re_evaluate_policies else StageMarker())
    if variant == "login":
        plan.context.update({
            "pending_user": user, "auth_method": "password", "auth_method_args": {"mfa_devices": [device]},
            "hub_p1_execution": {"policy": "animus-password-totp-mechanism-v2", "flow": flow.pk.hex,
                                 "nonce": uuid.uuid4().hex, "factor_time": int(time.time()),
                                 "initial_session_user": None, "initial_login_event": None}})
    token = FlowToken.objects.create(user=user, identifier="p1r-forged-" + variant + "-" + uuid.uuid4().hex[:8],
                                     flow=flow, _plan=FlowToken.pickle(plan), expires=now() + timedelta(minutes=30),
                                     expiring=True, revoke_on_execution=True)
    path = Path("/state/forged-" + variant)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(token.key)
    return {"forged": variant, "bindings": [b.order for b in plan.bindings]}


def sessions():
    """Authenticated-session owners (usernames only, synthetic)."""
    from authentik.core.models import AuthenticatedSession
    return {"authenticated_sessions": sorted(s.user.username for s in AuthenticatedSession.objects.select_related("user"))}


if PHASE.startswith("inject:"):
    out = inject(PHASE.removeprefix("inject:"))
elif PHASE.startswith("negate:"):
    out = negate(PHASE.removeprefix("negate:"))
elif PHASE.startswith("forge:"):
    out = forge(PHASE.removeprefix("forge:"))
elif PHASE == "sessions":
    out = sessions()
elif PHASE == "ready":
    out = ready()
elif PHASE == "users":
    out = seed_users()
elif PHASE == "inventory":
    out = inventory()
elif PHASE in FILES:
    out = {"apply": apply(PHASE), "inventory": inventory()}
else:
    raise SystemExit("unknown phase")
print("T4RESULT " + json.dumps(out, sort_keys=True, default=str))
