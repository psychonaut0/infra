"""Synthetic fixture control, run only inside the isolated harness worker.

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
FILES = {"base": "/state/alkion-lan.base.yaml", "forward": "/t4-blueprints/alkion-lan.yaml",
         "rollback": "/t4-rollback/alkion-lan-20261118-rollback.yaml"}
# name: (normal group member, superuser, active, has TOTP)
USERS = {
    "t4-owner": (True, False, True, True), "t4-other": (True, False, True, True),
    "t4-slow-a": (True, False, True, True), "t4-slow-b": (True, False, True, True),
    "t4-slow-c": (True, False, True, True), "t4-slow-c-other": (True, False, True, True),
    "t4-notp": (True, False, True, False), "t4-nogroup": (False, False, True, True),
    "t4-inactive": (True, False, False, True), "t4-admin": (True, True, True, True),
}


def bindings(slug):
    flow = Flow.objects.get(slug=slug)
    return [[b.order, b.stage.name, b.evaluate_on_plan, b.re_evaluate_policies, b.invalid_response_action]
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


if PHASE == "ready":
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
