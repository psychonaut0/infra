"""Synthetic fixture control for the P1 redesign qualification, run only inside the isolated worker.

Executed by run.sh as `ak shell -c "exec(open('/t4/seed.py').read())"` with T4_PHASE set.
Blueprints are applied the CT117 way: the file run.sh installed at
/blueprints/ct-animus/alkion-lan.yaml, the BlueprintInstance Authentik itself discovered,
its owner-commissioning context, and the apply_blueprint task the admin "Apply" sends,
executed by the running worker. Every phase prints one JSON line prefixed `T4RESULT `
with structural inventory only: no secrets, hashes of secrets or tokens. Synthetic
passwords and TOTP keys go only to /state/users.json (mode 0600).
"""
import json
import os
import secrets
import time
from hashlib import sha256, sha512
from pathlib import Path

from authentik.blueprints.models import BlueprintInstance
from authentik.blueprints.v1.importer import Importer
from authentik.core.models import Application, Group, User
from authentik.flows.models import Flow, FlowStageBinding
from authentik.policies.expression.models import ExpressionPolicy
from authentik.policies.models import PolicyBinding
from authentik.stages.authenticator_totp.models import TOTPDevice
from authentik.stages.consent.models import ConsentStage, UserConsent
from authentik.stages.identification.models import IdentificationStage
from authentik.stages.prompt.models import PromptStage

PHASE = os.environ["T4_PHASE"]
PATH = "ct-animus/alkion-lan.yaml"
CONTEXT = {"animus_issuance_profile": "owner-commissioning"}
# Fault injection targets (policy names); restored by re-applying the installed file.
INJECTABLE = {"animus-lan-entry-gate", "animus-lan-login-gate", "animus-lan-denied-notice",
              "animus-lan-application", "animus-lan-pkce"}
# name: (normal group member, superuser, active, has TOTP)
ELIGIBLE = (True, False, True, True)
USERS = {
    "t4-owner": ELIGIBLE, "t4-other": ELIGIBLE, "t4-slow-a": ELIGIBLE, "t4-slow-b": ELIGIBLE,
    "t4-slow-c": ELIGIBLE, "t4-slow-c-other": ELIGIBLE, "t4-notp": (True, False, True, False),
    "t4-nogroup": (False, False, True, True), "t4-inactive": (True, False, False, True),
    "t4-admin": (True, True, True, True), "p1r-slow-d": ELIGIBLE, "p1r-slow-e": ELIGIBLE,
    "p1r-inj-a": ELIGIBLE, "p1r-inj-b": ELIGIBLE, "p1r-inj-c": ELIGIBLE, "p1r-inj-nogroup": (False, False, True, True),
    "p1r-forge": ELIGIBLE, "p1r-replay": ELIGIBLE, "p1g-burst": ELIGIBLE, "p1g-refresh": ELIGIBLE,
    "p1g-redir": ELIGIBLE, "p1g-deact": ELIGIBLE, "p1g-deact-b": ELIGIBLE, "p1g-switch-a": ELIGIBLE, "p1g-switch-b": ELIGIBLE,
    "p1g-res-a": ELIGIBLE, "p1g-res-nogroup": (False, False, True, True), "p1g-across-a": ELIGIBLE,
    "p1g-across-b": ELIGIBLE, "p1g-across-c": ELIGIBLE, "p1g-across-d": ELIGIBLE,
}
NORMAL = "animus-lan-normal"


def bindings(slug):
    flow = Flow.objects.get(slug=slug)
    return [[b.order, b.stage.name, b.evaluate_on_plan, b.re_evaluate_policies, b.invalid_response_action,
             b.policy_engine_mode,
             [[p.policy.name if p.policy else None, p.negate, p.failure_result, p.enabled, p.timeout]
              for p in PolicyBinding.objects.filter(target=b.pbm_uuid).order_by("order")]]
            for b in FlowStageBinding.objects.filter(target=flow).select_subclasses().order_by("order", "stage__name")]


def inventory():
    result = {flow: bindings(flow) for flow in (
        NORMAL, "animus-lan-enrollment", "animus-lan-repair", "animus-lan-authorization")}
    app = Application.objects.get(slug="alkion-lan")
    result["application"] = [app.policy_engine_mode, [
        [p.policy.name if p.policy else None, p.order, p.negate, p.failure_result, p.enabled, p.timeout]
        for p in PolicyBinding.objects.filter(target=app.pbm_uuid).order_by("order")]]
    result["expression_sha256"] = {p.name: sha256(p.expression.encode()).hexdigest()
                                   for p in ExpressionPolicy.objects.filter(name__startswith="animus-lan-").order_by("name")}
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


def instance_state():
    instances = list(BlueprintInstance.objects.filter(path=PATH))
    out = {"instances_for_path": len(instances),
           "instances_under_ct_animus": BlueprintInstance.objects.filter(path__startswith="ct-animus/").count()}
    if len(instances) == 1:
        instance = instances[0]
        content = Path("/blueprints", PATH).read_bytes()
        out.update({"status": instance.status, "enabled": instance.enabled,
                    "context_is_owner_commissioning": instance.context == CONTEXT,
                    "hash_matches_file": instance.last_applied_hash == sha512(content).hexdigest()})
    return out


def apply_and_wait(instance, timeout=240):
    """The admin interface's Apply: send apply_blueprint and wait for the worker to run it."""
    from authentik.blueprints.v1.tasks import apply_blueprint
    content = instance.retrieve()
    digest = sha512(content.encode()).hexdigest()
    importer = Importer.from_string(content, instance.context)
    valid, logs = importer.validate()
    warnings = [str(log.event)[:200] for log in logs if getattr(log, "log_level", "") in ("warning", "error")]
    before = instance.last_applied
    apply_blueprint.send_with_options(args=(instance.pk,), rel_obj=instance)
    deadline = time.time() + timeout
    while time.time() < deadline:
        instance.refresh_from_db()
        if instance.last_applied > before and instance.status != "unknown":
            break
        time.sleep(2)
    return {"valid": valid, "warnings": warnings, "applied": instance.status == "successful"
            and instance.last_applied_hash == digest, "status": instance.status}


def the_instance():
    instances = list(BlueprintInstance.objects.filter(path=PATH))
    if len(instances) != 1:
        raise SystemExit("expected exactly one instance")
    return instances[0]


def discover_instance():
    """First apply as on CT117: discovery creates the instance (empty context, issuance
    closed), then the operator sets the owner-commissioning context and applies."""
    from authentik.blueprints.v1.tasks import blueprints_find, check_blueprint_v1_file
    if not BlueprintInstance.objects.filter(path=PATH).exists():
        files = [b for b in blueprints_find() if b.path == PATH]
        check_blueprint_v1_file(files[0])
    instance = the_instance()
    deadline = time.time() + 240
    while time.time() < deadline and instance.status != "successful":
        time.sleep(2)
        instance.refresh_from_db()
    closed = PolicyBinding.objects.filter(policy__name="animus-lan-issuance-closed").exists()
    first = {"status": instance.status, "context": instance.context, "issuance_closed_bound": closed}
    instance.context = CONTEXT
    instance.save()
    second = apply_and_wait(instance)
    second["issuance_closed_bound"] = PolicyBinding.objects.filter(policy__name="animus-lan-issuance-closed").exists()
    return {"discovered": first, "owner_commissioning": second, "instance": instance_state()}


def rename_probe():
    """After `run.sh install forward` (hidden file + rename, no explicit Apply): what the
    watcher did on its own within 60 s. The rename is not a watched event, but creating the
    hidden file is, and it schedules a discovery that may find the renamed file already in
    place. Either way the one existing instance, with its context, is what applies it."""
    instance = the_instance()
    content = Path("/blueprints", PATH).read_bytes()
    deadline = time.time() + 60
    while time.time() < deadline and instance.last_applied_hash != sha512(content).hexdigest():
        time.sleep(2)
        instance.refresh_from_db()
    time.sleep(5)
    instance.refresh_from_db()
    return {"applied_without_explicit_apply": instance.last_applied_hash == sha512(content).hexdigest(),
            "status": instance.status, "context_is_owner_commissioning": instance.context == CONTEXT,
            "instances_under_ct_animus": BlueprintInstance.objects.filter(path__startswith="ct-animus/").count()}


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
        entry = {"password": password, "totp_key": None, "pk": user.pk}
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
    if name not in INJECTABLE:
        raise SystemExit("not injectable")
    return {"injected": name, "rows": ExpressionPolicy.objects.filter(name=name).update(
        expression='raise ValueError("p1r-injected fault")\n')}


def negate(name):
    """Misconfiguration probe: invert the binding of one gate policy."""
    if name not in INJECTABLE:
        raise SystemExit("not negatable")
    return {"negated": name, "rows": PolicyBinding.objects.filter(policy__name=name).update(negate=True)}


def normal_binding(order):
    return FlowStageBinding.objects.get(target__slug=NORMAL, order=order)


def residual(case):
    """Spec decision 6 residual cases: changes an admin, an apply or an RBAC grant could make.
    Each goes through model save/delete, so Authentik's own cache invalidation runs."""
    if case in ("entry-disabled", "login-disabled", "application-disabled"):
        policy = {"entry": "animus-lan-entry-gate", "login": "animus-lan-login-gate",
                  "application": "animus-lan-application"}[case.split("-")[0]]
        binding = PolicyBinding.objects.get(policy__name=policy)
        binding.enabled = False
        binding.save()
    elif case in ("login-deleted", "application-deleted"):
        policy = "animus-lan-login-gate" if case.startswith("login") else "animus-lan-application"
        PolicyBinding.objects.get(policy__name=policy).delete()
    elif case == "login-reeval-off":
        # Only possible below the API (its serializer refuses both evaluations off).
        binding = normal_binding(50)
        binding.re_evaluate_policies = False
        binding.save()
    elif case == "login-any-extra":
        binding = normal_binding(50)
        extra = ExpressionPolicy.objects.create(name="p1g-residual-always-true", expression="return True\n")
        PolicyBinding.objects.create(target=binding, policy=extra, order=1)
        binding.policy_engine_mode = "any"
        binding.save()
    elif case == "application-any":
        app = Application.objects.get(slug="alkion-lan")
        app.policy_engine_mode = "any"
        app.save()
    else:
        raise SystemExit("unknown residual")
    return {"residual": case, "inventory": {NORMAL: bindings(NORMAL)}}


def unresidual(case):
    """Restore: a re-apply resets every pinned attribute and recreates deleted bindings;
    an extra binding is not something a re-apply removes, so it is deleted here."""
    if case == "login-any-extra":
        PolicyBinding.objects.filter(policy__name="p1g-residual-always-true").delete()
        ExpressionPolicy.objects.filter(name="p1g-residual-always-true").delete()
    return {"restored": case, "apply": apply_and_wait(the_instance())}


def forge(variant):
    """A FlowToken whose plan claims a finished ceremony (login variant) or a fresh plan
    (full variant). The executor marks any token plan is_restored; nothing else differs."""
    import uuid
    from datetime import timedelta
    from django.utils.timezone import now
    from authentik.flows.markers import ReevaluateMarker, StageMarker
    from authentik.flows.models import FlowToken
    from authentik.flows.planner import FlowPlan
    flow = Flow.objects.get(slug=NORMAL)
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


def redirector():
    """A synthetic flow whose redirect stage switches into the normal flow keeping its
    context, so the normal flow is planned with is_redirected (stages/redirect/stage.py)."""
    from authentik.stages.redirect.models import RedirectStage
    flow = Flow.objects.create(slug="p1g-redirector", name="p1g redirector", title="p1g redirector",
                               designation="authentication", authentication="none")
    stage = RedirectStage.objects.create(name="p1g-redirect", mode="flow", target_flow=Flow.objects.get(slug=NORMAL),
                                         keep_context=True)
    FlowStageBinding.objects.create(target=flow, stage=stage, order=0)
    return {"redirector": True}


def deactivate(names):
    """Deactivate one or more accounts (comma-separated), as an admin would."""
    for name in names.split(","):
        user = User.objects.get(username=name)
        user.is_active = False
        user.save()
    return {"deactivated": names.split(",")}


def brand(variant):
    """User switching needs the brand: `add` plans brand.flow_authentication, `switch`
    plans brand.flow_user_switch. Pointing either at the Alkion flow is a misconfiguration."""
    from authentik.brands.models import Brand
    target = Brand.objects.get(default=True)
    saved = Path("/state/brand.json")
    if variant == "restore":
        previous = json.loads(saved.read_text())
        target.flow_authentication = Flow.objects.filter(slug=previous["flow_authentication"]).first()
        target.flow_user_switch = Flow.objects.filter(slug=previous["flow_user_switch"]).first()
        target.save()
        saved.unlink()
    else:
        if not saved.exists():
            fd = os.open(saved, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump({key: getattr(target, key).slug if getattr(target, key) else None
                           for key in ("flow_authentication", "flow_user_switch")}, stream)
        normal = Flow.objects.get(slug=NORMAL)
        default = Flow.objects.get(slug="default-authentication-flow")
        if variant == "switch-add":
            target.flow_authentication, target.flow_user_switch = normal, default
        elif variant == "switch-target":
            target.flow_authentication, target.flow_user_switch = default, normal
        else:
            raise SystemExit("unknown brand variant")
        target.save()
    return {"brand": variant, "flow_authentication": target.flow_authentication.slug if target.flow_authentication else None,
            "flow_user_switch": target.flow_user_switch.slug if target.flow_user_switch else None}


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
elif PHASE.startswith("residual:"):
    out = residual(PHASE.removeprefix("residual:"))
elif PHASE.startswith("unresidual:"):
    out = unresidual(PHASE.removeprefix("unresidual:"))
elif PHASE.startswith("deactivate:"):
    out = deactivate(PHASE.removeprefix("deactivate:"))
elif PHASE.startswith("brand:"):
    out = brand(PHASE.removeprefix("brand:"))
elif PHASE == "redirector":
    out = redirector()
elif PHASE == "sessions":
    out = sessions()
elif PHASE == "ready":
    out = ready()
elif PHASE == "users":
    out = seed_users()
elif PHASE == "inventory":
    out = {"inventory": inventory(), "instance": instance_state()}
elif PHASE == "instance":
    out = discover_instance()
elif PHASE == "rename-probe":
    out = rename_probe()
elif PHASE == "apply":
    out = {"apply": apply_and_wait(the_instance()), "instance": instance_state(), "inventory": inventory()}
else:
    raise SystemExit("unknown phase")
print("T4RESULT " + json.dumps(out, sort_keys=True, default=str))
