# Mandatory Prompt mechanism experiment

Review artifacts only. This directory is outside the deployed blueprint mount;
base Compose, the running provider and the global operator flow are unchanged.
No user, membership, credential, Hub owner or grant is seeded.

The default generated candidate **unconditionally denies application issuance**.
A second, explicitly reviewed phase may explore issuance in a fresh isolated
synthetic instance. That is an experiment, not production proof activation or P1
lifecycle readiness. No container, provider or account action has run for this
amendment. Owner enrollment and real-data opening remain closed.

## Exact candidate

The normal flow uses mandatory entry Prompt → identification → unconditional
inbuilt password → unconditional TOTP-only validation → mandatory final Prompt →
User Login. TOTP missing behavior denies and reuse threshold is zero. Every Prompt
has a positive `validation_policies` list; stage bindings are unconditional.
Submitted checkbox, query and body fields never supply proof or time.

The entry validator reads the trusted server session's FlowPlan, checks its
current stage/flow and initializes an absent timing envelope before either factor.
It stores a server time, nonce and initial session identity. Re-entry/inheritance,
restored/redirected context and unsupported process modes fail. The final validator
preserves that bound, checks same pending subject, active non-admin normal-group
eligibility, actual inbuilt-password method and confirmed same-user TOTP devices,
and requires a whole ceremony age of at most 300 seconds. It never renews time.

Pinned [Prompt validation](https://github.com/goauthentik/authentik/blob/version/2026.8.5/authentik/stages/prompt/stage.py)
uses uncached ALL evaluation and raises ValidationError on a false result. Its
constructed bindings default to non-negated/failure-false. The
[challenge handler](https://github.com/goauthentik/authentik/blob/version/2026.8.5/authentik/flows/stage.py)
advances only after validation succeeds. This replaces the rejected conditional
Deny approach, whose false runner result could skip denial. The exact HTTP worker,
FlowPlan persistence and request/session binding still require runtime tests.

Application access separately requires the exact current qualified login event.
Authorization has mandatory entry Prompt → consent → final Prompt: it snapshots
and compares subject, exact event datetime/identity, original factor bound, client,
provider and authorization parameters. These checks are not atomic with User Login
or fulfillment. Identity changes at every intervening redirect remain explicit
negative experiments; no race safety is assumed.

The experimental policy ID is `animus-password-totp-mechanism-v2`. The mapping
requires exact original-grant/event association and emits only
`hub_authentication_policy` and `hub_factor_time`. A refresh must retain the
original bound or lose proof. Absence of an access-policy check at refresh is not
inherently a failure if the original grant chain was qualified; that origin and
non-upgrade property must be demonstrated. No production P2 trust policy is made.

Controlled enrollment/repair use separate empty eligibility groups and positive
Prompt checks; repair requires password plus static-code validation before
replacement TOTP. They have no User Login/normal marker. Complete revocation,
predecessor retirement, final-factor protection and safe public exposure remain
unqualified. No public enrollment/recovery link is added.

## Build and check

```sh
python3 render.py --check
python3 test_candidate.py
```

`candidate.yaml` is generated from the template and policy inputs. The optional
`--issuance-experiment --output SEPARATE_FILE` renderer mode changes only the
explicit experiment closure and phase comment; it cannot overwrite the default.
Never mount both candidates or place either in the running provider without the
specific configuration/recipe review. The default base stack mounts neither.

Fixture inputs are the selected official pins:

```
ghcr.io/goauthentik/server:2026.8.5@sha256:602227a86455d6772e3d16b7d852713c4f864f40df5df894abd1285bb53979a5
postgres:17.11-bookworm@sha256:839fbb5a4942a7758dd93e18b42ea5649807491a0545b5eeeb8f0b8bb11857a5
```

Private qualification tooling retains official versioned source hashes, a fresh
internal-network fixture, file/stdin secret delivery, a single-step HTTP actor and
an exact review recipe. The actor parks every redirect, saves sensitive responses
only in restrictive files, and reports bounded status/component/boolean data.
There are no host ports, external networks, shared deployment volumes or socket
mounts. Review exact fixture/actor hashes before any execution.

## Required experiments and closed gates

Observe actual normal timing through repeated/new/restored/concurrent executions,
server restart and context tampering; pause after password and TOTP beyond 300
seconds. False, exception, timeout and unsupported-process cases must not advance
mandatory Prompts or escape into application credentials. Empty/forged POST cannot
bypass validation. Inspect only safe event/protocol fields, never secrets.

With separate issuance-experiment approval, exercise S256, absent/plain challenge,
wrong/missing verifier, exact callback, stale/operator SSO, identity switches after
final normal Prompt/before User Login and after final authorization Prompt/before
fulfillment, original-grant event replacement and non-upgrading refresh. Successful
JWTs are signature/origin verified; token contents are not logged.

The complete hold denies every minting route and drains all HTTP, worker, control
and protocol owners before inventory. Park consent/login executors before it;
restart under hold and keep stored routes denied. Empty authenticated-session
inventory does not enumerate anonymous/partial plans. After factor replacement and
all-credential revocation/reopening, try old plans parked after each factor, after
final normal Prompt before User Login, and after final authorization Prompt before
fulfillment. They must not produce qualified sessions/codes. The candidate has no
qualified epoch/plan-invalidation solution yet; a failing negative keeps lifecycle
readiness closed. No provider database cleanup or fabricated evidence is allowed.

Checkpoint 2 still requires actual browser request inventory, reviewed public
allowlist/default denial, controlled independent repair and complete credential
revocation. Scratch experiment results authorize no live provider change, owner
custody, grants or personal-data opening.

## Normal-flow simplification (2026-11-18)

Subject: the deployed `../../blueprints/alkion-lan.yaml`, not `candidate.yaml`
(which stays unchanged). Changes: username+password on one page through a
dedicated `animus-lan-normal-identification` stage embedding the existing
password stage; consent `permanent`; one plain-word checkbox per validator Prompt;
guarded `state: absent` removal of the replaced bindings; the `prompt=login`
binding to a hash of the normalised authorize request; reviewed rollback `../../rollback/alkion-lan-20261118-rollback.yaml`.

### Source verification (version/2026.8.5, commit 4ec0d19b)

The loaded backport image `a662172c` matches the tag in every module cited here
except the two backported `authenticator_validate` files (checked by hash).
Paths are under `authentik/` or `web/src/` at that tag.

- **Same `auth_method`.** The combined page checks the password with the same
  `authenticate()` and the password stage's backends
  (`stages/identification/stage.py:231-256`, `stages/password/stage.py:39-71`).
  `InbuiltBackend` sets `auth_method="password"` and empty args by `setdefault`
  whichever stage calls it (`core/auth.py:38-45,56-66`); TOTP then appends
  `mfa_devices` without changing the method (backport `authenticator_validate/stage.py:149-157`).
  The final validator therefore sees the same method and arguments, and adds the
  same `hub_policy_*` marker.
- **Timing and order.** Bindings are planned by `order` (`flows/planner.py:340`);
  the executor serves `bindings[0]` and pops it on success (`planner.py:97-122`,
  `flows/views/executor.py:421-432`). The entry Prompt stays at order 0, so
  `hub_factor_time` is still set before either factor; every validator still reads
  only its own `plan.bindings[0]`. No policy names the replaced stages.
- **Why explicit removal.** Bindings are unique only per (flow, stage, order)
  (`flows/models.py:299-304`), so the old order-10 stage would stay planned next to
  the new one. An absent binding entry whose row is already gone makes the
  importer delete an unsaved placeholder and raise (`blueprints/v1/importer.py:444-447`,
  reproduced at runtime: every re-apply failed). Each removal is therefore guarded
  by an existence `conditions` entry (`blueprints/v1/common.py:176-178,369-413`).
- **Combined-page differences.** An unknown user always fails on that page
  (`identification/stage.py:210-213`). The password stage's
  `failed_attempts_before_cancel` restart (`password/stage.py:165-175`) does not
  apply; failed attempts still fire `login_failed` (`password/stage.py:65-71`). That
  counter only restarts the flow and never locked anyone out.
- **Consent.** In `permanent` mode a stored consent makes the stage call
  `stage_ok()` on GET without a page (`stages/consent/stage.py:81-130`). It is the
  order-10 stage between the order-0 and order-20 validator Prompts, which run
  unconditionally either way. `permanent` rather than `expiring`: A7 asks for
  consent once, and an expiry would only add a page every few weeks without
  touching either validator. Any plan with a non-fulfillment stage needs the flow
  executor (`flows/planner.py:129-146`, `providers/oauth2/views/authorize.py:594-606`),
  so remembered consent cannot make authorization silent.
- **prompt=none.** Only an anonymous request is refused early
  (`authorize.py:455-464`); an authenticated one runs the same authorization flow.
- **prompt=login in stock 2026.8.5.** Application policies run in
  `PolicyAccessView.dispatch` before the authorize view's `get()`
  (`policies/views.py:62-84`), and `get()` re-authenticates only while its
  last-login marker is absent or equal to the current login (`authorize.py:552-560`).
  The marker is never cleared, and an anonymous first request goes to login without
  setting it (`policies/views.py:78-79`). Authentik never stores Django's user key
  in session data (`core/sessions.py:118-136`), so `login()` keeps the session
  (key cycled) even when the account changes; the stored `next` and marker survive.
- **Limits of the `prompt=login` binding.**
  - The `SESSION_KEY_LAST_LOGIN_UID` marker is global to the session, so a
    leftover `animus-lan-continue:` value suppresses `prompt=login` for any other
    OAuth2 provider used in the same session (today there is only one).
  - The marker survives a rollback, which restores the stock behaviour, including
    the wrong-account no-op for another user's live session.
  - The re-authentication branch returns True and relies on the order of
    operations inside Authentik's authorize view (`authorize.py:525-560`). The
    authorization entry and final validators remain the backstop, and any
    Authentik upgrade must re-qualify this branch before deploy.
  - The label is read from the executor's stored query on the final POST. A
    client that drops `query` there loses it and pays a second ceremony; T7
    checks one ceremony per Connect on the phone.
- **Hidden-field candidate: not adoptable.** The server returns the Prompt
  challenge on every GET and advances only on a valid POST
  (`flows/stage.py:96-153`, `stages/prompt/stage.py:120-164,273-277`); a hidden
  field just takes its server default (`stages/prompt/models.py:303-306`). The UI
  renders hidden fields as `<input type="hidden">` inside a form with a Continue
  button, and submits only on that form's submit event
  (`web/src/flow/stages/prompt/PromptStage.ts:212-220,324-342`,
  `web/src/flow/stages/base.ts:113-141`). Nothing auto-submits a Prompt; only the
  autosubmit stage does (`web/src/flow/stages/autosubmit/AutosubmitStage.ts:29-36`).
  A hidden-only validator Prompt is still a page with one click, so it saves nothing.

### Isolated synthetic run

`flow-simplify/run.sh campaign` (with `T4_STATE` a private 0700 directory, then
`run.sh down`) starts the privately loaded backport image (ID checked) and the
pinned PostgreSQL on one `--internal` Docker network: no published port, no
egress, no socket, no shared volume, no real credential. `seed.py` applies
blueprints through Authentik's own importer with the owner-commissioning instance
context: the previous blueprint, the new one twice, the rollback twice, the new one
again. It then creates ten synthetic users. `actor.py` drives the flow-executor API
as the web UI does, redeems codes with PKCE and verifies the ID token signature,
and reports only components, page counts, outcomes and booleans.
`test_harness.py` checks the isolation offline.

Run 2026-11-18 at commit `da99ae5` (clean tree; blueprint sha256 `d560cd50…48ef`,
rollback sha256 `dd06188c…7689`, all recorded in `00-up.json`; receipts private,
outside the repository). A full rerun at that commit gave the same results as
the earlier campaign:

- **Blueprint apply.** Previous → new → new again → rollback → rollback → new,
  all valid and applied. After each new apply the normal flow is exactly entry (0),
  combined page (10), TOTP (30), final (40), login (50), with no order-20 binding.
  Enrollment, repair, the shared identification stage and the authorization flow
  match the previous blueprint's runtime inventory. Without the existence guards
  the second apply failed (importer exception), which is why they exist.
- **Page counts.** Pages are challenges the user answers. Every code was redeemed
  with PKCE. Each ID token passed RS256 signature, issuer and audience checks,
  carried `hub_authentication_policy` and an integer `hub_factor_time` no later
  than `auth_time`, came with a refresh token, and named the user who signed in.

  | Connect | Previous blueprint | New blueprint |
  | --- | --- | --- |
  | No session, no prompt | 8 incl. consent, 1 ceremony | 6, 1 ceremony |
  | No session, `prompt=login`, first Connect | 13 incl. consent, 2 ceremonies | 7 incl. consent, 1 ceremony |
  | No session, `prompt=login`, later Connect | — | 6, 1 ceremony |
  | Same user's live session, `prompt=login` (twice) | — | 6, 1 ceremony / 6, 1 ceremony |
  | Another user's live session, `prompt=login` | — | 6, 1 ceremony |
  | Same user's session older than 300 s, `prompt=login` | — | 6, 1 ceremony |
  | Another user's session older than 300 s, `prompt=login` | — | 7 incl. consent, 1 ceremony |
  | Login from another flow, `prompt=login` | — | 6, 1 ceremony |
  | Same user's live session, no prompt | — | 2, 0 ceremonies |

  Before the `prompt=login` binding (exploratory run, same image and new
  blueprint) stock handling gave: no session, 11 pages over two ceremonies; same
  user's live session, 2 pages and no ceremony (marker already set, so the request
  was ignored); another user's live session, 2 pages and no ceremony, so the code
  went to the other, already signed-in account; a session older than 300 s
  (either account) or a login from another flow, "Permission denied". The
  binding closes all four.
- **A8 negatives, all without a code.**
  - A user without TOTP is denied at the TOTP stage; a wrong TOTP is refused.
  - A ceremony paused more than 300 s after both factors is refused at the final
    check. A same-user session older than 300 s without `prompt` is denied.
  - Superuser and outside-group accounts are refused at the final check, also
    when `prompt=login` sends their live session back to sign in. An inactive
    account is refused on the combined page.
  - Unknown client: 400. Unregistered or near-miss redirect: "Redirect URI Error".
    Plain or missing PKCE: denied, also with `prompt=login`.
  - A session parked after TOTP and replayed after login restarts at the entry
    check. A login from the default authentication flow is denied without `prompt`.
  - `prompt=none` with no session gets a 400 error page (the native callback
    scheme cannot carry `login_required`); with a live session it stops at the
    authorization entry check.
  - A second authorization within 300 s with remembered consent also stops at the
    authorization entry check: no silent code.
- **Consent and rollback.** The first Connect per account shows consent (7 pages),
  later ones do not (6). After the rollback every Connect is back to 8 pages with
  the consent page, although the 5 stored consents remain. After re-applying the
  new blueprint the stored consent is honoured again (6 pages): remembered consent
  survives a rollback.
- **Labels.** Each of the four plain-word labels appeared at its own position in
  every run.

Not runtime-qualified here: real browser and phone rendering (combined page,
labels, Continue buttons), the Custom Tab cookie jar, the CT117 edge, the
BlueprintInstance watcher path on CT117 (the harness calls the same importer
directly), and restored/redirected flow-token plan contexts (unchanged
expressions; unit tests only). CT117 re-apply, the read-only SQL binding check and
the phone rerun stay with T6/T7.

## P1 sign-in redesign (2026-11-23)

Subject: the deployed `../../blueprints/alkion-lan.yaml` (Animus spec and plan
`docs/specs|plans/2026-11-23-p1-signin-redesign.md`, decisions 1–3 and 5–8), its
rollback `../../rollback/alkion-lan-20261123-rollback.yaml` and the read-only drift
check `../../ops/check-alkion-lan-drift`. The 2026-11-18 section above describes the
state this replaces; its rollback is superseded.

### Mechanism and why it is fail-closed

- **Gates on the granting stages.** The entry gate sits on the username+password
  binding, the login gate on the User Login binding. Both bindings are
  `evaluate_on_plan: false`, `re_evaluate_policies: true`, engine mode `all`, so
  `ReevaluateMarker` (`flows/markers.py`) runs the gate uncached in the request that is
  about to serve the stage, and a false result, a `PolicyException`
  (`failure_result: false`) or any exception caught inside the gate (`return False`)
  removes the stage (`flows/planner.py:96-115`). Without the identification stage
  there is no pending user and no `hub_p1_execution` envelope, so the login gate
  refuses (with a live session the TOTP stage may still challenge the session's own
  user, as in case 33, but nothing after it grants); without User Login there
  is no session and no `hub_policy_*` marker. Gate, PKCE and application bindings are
  pinned `enabled: true`, `negate: false`, `failure_result: false`, `timeout: 30`.
- **Entry gate.** First request: refuses restored, redirected, user-switch and
  already-authenticated (`auth_method`) plans and a plan that is not the session's;
  records `hub_p1_execution` (start time, nonce, session user, login event). Later
  requests accept only the same untouched envelope and never renew the time.
- **Login gate.** Today's final check (300 s, same subject and session, active,
  non-superuser, normal group, password method, the user's own confirmed TOTP
  devices, no `user_switch_*` key) in the request that logs in. Its success path
  alone writes the marker, now with `hub_policy_gate: animus-lan-login-gate`.
- **Authorization without stages.** With only the in-memory fulfilment stage, the
  authorize view issues the code in the request whose application policies passed
  (`flows/planner.py:129-176`, `providers/oauth2/views/authorize.py:594-606`). The
  application policy requires a fresh proof written by the login gate and bound to
  this exact request; it refuses `prompt` values other than `login`, non-query
  response modes, `request`/`request_uri`, non-GET and policy subprocesses. Single
  use: `cache.add("animus-lan/claimed-login/<event>", ..., 900)` is the last step of
  the success path; `django_postgres_cache` implements `add` as an INSERT on the
  cache table's primary key, so exactly one concurrent caller wins, and the 900 s TTL
  outlives the 300 s freshness window plus skew. A refused claim denies without
  re-authentication.
- **Notice.** A re-evaluated Deny stage after login with OD2's text, shown unless
  this ceremony's own qualified login happened. It is explanation only: if its policy
  raises, the notice is removed; an eligible user still completes and an ineligible
  user, whose User Login is already gone, has no session and no code.
- **Fail-closed entry order** (defence in depth; the importer applies the file in
  one transaction): the granting bindings are first restated with their 2026-11-18
  attributes, then their gates are bound (a gate evaluated at plan time always
  refuses), then they switch to re-evaluation, then the notice is bound, and only then
  are the validator Prompts and the authorization stages unbound. `test_blueprint.py`
  checks every prefix.
- **`hub_policy_gate`.** A plan started under the 2026-11-18 flow keeps its own
  stages after the apply (plans live in the session); its final Prompt still writes a
  marker. Requiring the gate key means such a ceremony ends without a code (measured:
  case 125). The key is internal; the policy ID and the ID-token claims are unchanged.

### Residual risks (decision 6)

A stage whose policy bindings are all disabled or deleted runs
(`PolicyEngine.empty_result`, `policies/engine.py`), and so does a stage binding with
`re_evaluate_policies` off (below the API: its serializer refuses both evaluations
off). An extra always-true binding under engine mode `any`, or the application switched
to `any`, has the same effect. Superusers, a blueprint apply or an RBAC grant can make
these changes. For a gate, an ineligible account gets a session but no code, because
only the login gate writes the marker; for the application binding, any signed-in
account gets a code. The campaign runs each case and records what it gives; the drift
check flags every one (cases 70–109). The follow-up (OD3) is an API check of the proof
claim on training requests.

### Install path (decision 8, corrected)

The worker mounts the whole `blueprints/` directory and applies every non-hidden
`.yaml` there as its own instance (`blueprints/v1/tasks.py:121-143`, `153-185`); it is
not limited to `alkion-lan.yaml`. Backups and rollbacks therefore live outside it.
The rename itself is not a watched event (the watcher filters created/modified), but
creating the hidden `.alkion-lan.yaml.new` is: it schedules a discovery, which in the
isolated run found the renamed file and applied it within seconds, through the one
existing instance and its context, exactly as the explicit Apply does (case 08). So no
ceremony may be open from the moment the hidden file is written. A failed apply that
deletes the instance would be rediscovered with an empty context, which closes
issuance (`check_blueprint_v1_file`); the drift check reports the context.

### Rollback chain

`alkion-lan-20261123-rollback.yaml` = `c693d82` + guarded `state: absent` removal of the
two gate bindings and the notice binding + engine mode reset to `any`; valid only from
the redesigned state. The 2026-11-18 rollback is marked superseded: after the redesign
it would reset the login binding while its gate stays bound, refusing every login.

### Re-qualification triggers

Re-run this campaign before any Authentik upgrade, and after any change to: the
authorize view's order of policy check, `max_age`/`prompt=login` handling and
fulfilment (`authorize.py:525-606`); `FlowPlan.requires_flow_executor`;
`ReevaluateMarker` and plan re-evaluation; `PolicyEngine.empty_result` and binding
evaluation; `SESSION_KEY_GET` and the stored `next`; `django_postgres_cache.add`;
the blueprint watcher and discovery.

### Isolated campaign at the final tree (2026-11-24)

Every command below ran in the infra worktree at `e8aa135` with a clean tree; the
commit that adds this section changes only this README. Case numbers are receipt
file prefixes. Receipts are private, outside the repository (Animus
`.data/hosting/p1-redesign-20261123/t1/campaign-20261124/`); they hold page counts,
outcome classes, booleans and claim names only.

| File | sha256 |
| --- | --- |
| `blueprints/alkion-lan.yaml` | `70662ab7ad25a2a0c989b74d505467bf2fe48b92b075b815e288b7658b6ca1d1` |
| `rollback/alkion-lan-20261123-rollback.yaml` | `25eb7b3b4a05246a7a882070c4d3f927bb1be3e20675f83b7aafdc482fa6e0e6` |
| `ops/check-alkion-lan-drift` | `2a66208b88f67eebe5b3c89f9f8a959ae3010256cdbddf31060fde79b01298ce` |
| `qualification/p1/redesign/actor.py` | `6b5ea37506778af64b08c43f6d47f652b14e6a36885044c5c33b02264069152d` |
| `qualification/p1/redesign/seed.py` | `f520f4db2c0e7c0240d2d59ab1604a6827eca85333977a7323e56fd40657728c` |
| `qualification/p1/redesign/run.sh` | `95d797dcde60b4cbfa685bde1108216fb81abd318726304032f5f6257aa0d952` |

**Checks (`command` → result):**

- `python3 -m pytest -q -p no:cacheprovider stacks/ct-animus/test_blueprint.py stacks/ct-animus/ops/test_check_alkion_lan_drift.py stacks/ct-animus/qualification/p1/redesign/test_redesign.py stacks/ct-animus/qualification/p1/redesign/test_harness.py`
  (a venv with pytest and PyYAML) → 68 passed, 206 subtests passed.
- `python3 stacks/ct-animus/check-config.py --self-test` → five PASS lines, exit 0.
- `git diff --check origin/master..HEAD` → clean.
- `T4_STATE=<new private 0700 dir> stacks/ct-animus/qualification/p1/redesign/run.sh campaign`
  → `{"campaign": "done"}`, exit 0, 2026-11-24 11:15–12:04 UTC, 117 receipts.
  `00-up.json` records commit `e8aa135`, `tree_clean: true`, Auth image ID
  `sha256:a662172cf4530a83982dfdea9ce5e41aebb66b9d7ade9e90cf22394e0724c38a` and the
  six hashes above.
- `run.sh down` → `{"down": true}`; `docker ps -a` and `docker network ls` → no
  `p1g-*` container or network left.
- Receipt hashes: `SHA256SUMS` over all 117 receipts
  `19005693d11b22b3d7c5c6c707c77b8afdf279e7585639670391359d2b5f48c1`; key receipts
  `00-up` `b656a492…26bc`, `21-main` `0fb47aba…dc50`, `22-negatives` `05fa152e…f3fe`,
  `26-single-use` `ec01bdef…a2cd`, `27-refresh` `dafe5514…10edb`,
  `120-across-rollback` `2934dbc9…703e`, `122b-drift-rollback-against-base`
  `71d81ab3…5601`, `125-across-apply` `9ee8b6ec…e2ef`, `128-actor-reforward`
  `db72fb5e…1be9`.

**Apply and install.** The `c693d82` blueprint was discovered with an empty
context (issuance closed), then applied with the owner-commissioning context (02);
`run.sh drift base` → PASS (05). `run.sh install forward` (hidden file, mode 644
kept, worker read the same bytes, `alkion-lan.yaml` the only file; 07), and within
60 s, with no explicit Apply, the watcher had applied it through the one instance
with its context unchanged (08). All 22 applies in the run were valid, without
warnings, successful, with `last_applied_hash` equal to the file's sha512 and one
instance. `run.sh drift forward` → PASS (11: 20 stage bindings, 5 policy bindings,
13 expressions, 1 mapping, 1 brand, 1 instance).

**B1 pages.** One ceremony each. Every code was redeemed with PKCE; every ID token
passed RS256, issuer and audience checks, carried `hub_authentication_policy`
`animus-password-totp-mechanism-v2` and an integer `hub_factor_time` ≤ `auth_time`,
came with a refresh token and named the connecting user; reusing the code at the
token endpoint was refused.

| Connect (`prompt=login` unless noted) | Deployed `c693d82` | Redesign |
| --- | --- | --- |
| No session, the account's first Connect | 7 incl. consent (04) | 2 (21 `other_user_first`, and every `p1g-*` account) |
| No session, later Connect | 6 (04) | 2 (21) |
| Same user's live session, twice | — | 2 / 2 (21) |
| Another user's live session | — | 2 (21) |
| No session, no `prompt` | — | 2 (21) |
| Same user's session older than 300 s | — | 2 (20-slow-stale) |
| Another user's session older than 300 s | — | 2 (20-slow-other-stale) |
| Login from the default authentication flow | — | 2 (22) |
| After the rollback / after re-applying | 6 (124) | 2 (128) |

**B2 negatives, all without a code.**
- No TOTP device: denied after 1 page. Wrong TOTP: refused. Ceremony over 300 s
  after the password, after TOTP or on the page: the notice after 2 pages (20).
  Same user's stale session without `prompt`: denied.
- Superuser or outside the group: the notice after 2 pages; with their own live
  default-flow session and `prompt=login`, a fresh ceremony and then the notice.
- Inactive: refused on the username+password page. Deactivated mid-ceremony after
  the password or after an accepted TOTP (30): executor 400, no session. After TOTP
  the login gate and User Login see the plan's stored user, so the session is
  removed by `AuthenticationMiddleware`, which logs out an inactive user on the next
  request (`core/middleware.py:63-65`).
- Unknown client: 400. Unregistered or near-miss redirect: "Redirect URI Error".
  Plain or missing PKCE: denied, with and without `prompt=login`.
- Restored plans (forged flow tokens, login and full variants; 25): the notice, 0
  pages, no session. Redirected plan (29): the notice, no session. Cookies parked
  after TOTP and replayed after login (22): a new ceremony is required.
- Another flow's login: without `prompt` denied; a direct normal-flow login with
  no authorize request: denied without `prompt`.
- User switch (only reachable by pointing a brand flow at the Alkion flow, which
  the drift check flags; 32, 35): "add user" (33) never reached a login, the
  session stayed with the original account; "switch" (36) showed the notice after
  one page. Drift PASS again after the brand was restored (38).
- `prompt=none`: no session → 400, live session → denied. `prompt=consent`,
  `login consent`, `none login`, `response_mode` `form_post` or `fragment`, and
  `request_uri`: denied on a live session and after a full ceremony bound to that
  request. A `request` object: `request_not_supported` (400) before any page. A POST
  of the bound request: 400; the GET after it gets the code.
- Silent authorization within 300 s (a new request, no `prompt`): denied. An exact
  replay of a fulfilled request, with or without `prompt=login`: denied without
  re-authentication.
- `run.sh seed sessions` (111): none of `t4-notp`, `t4-inactive`, `t4-slow-a`,
  `p1r-slow-d`, `p1r-slow-e`, `p1r-forge`, `p1g-deact`, `p1g-deact-b`, `p1g-redir`
  has an Authentik session. `t4-nogroup` and `t4-admin` have one only from their
  deliberate default-flow logins.

**B3 faults** (each restored by a valid re-apply; the drift check flagged each):

| Fault | Eligible account | Outside-group account | Drift |
| --- | --- | --- | --- |
| Entry gate raises (41) | notice, 0 pages, no session | — | expression differs |
| Login gate raises (45) | notice after 2 pages, no session | — | expression differs |
| Notice raises (49) | code, 2 pages | no code, no session; loops through new ceremonies (7, to the harness's 13-page limit) | expression differs |
| Application policy raises (53) | session, access denied | — | expression differs |
| PKCE policy raises (57) | session, access denied | — | expression differs |
| Login gate negated (61) | notice, no session | session, notice, no code | `negate is True` |
| Entry gate negated (65) | notice, 0 pages, no session | — | `negate is True` |

**Residual cases (decision 6), recorded, not passes** (70–109). Each was made by
model save or delete; `run.sh drift forward` exited 1 naming the change, and PASS
after the restore.

| Case | Eligible | Outside group | Drift reported |
| --- | --- | --- | --- |
| Entry binding disabled | notice, no session | notice, no session | `enabled is False` |
| Login binding disabled | session, notice, no code | session, notice, no code | `enabled is False` |
| Login binding deleted | session, notice, no code | session, notice, no code | declared but not bound |
| Login `re_evaluate_policies` off | session, notice, no code | session, notice, no code | `re_evaluate_policies is False` |
| Login: extra always-true binding, engine `any` | code | session, notice, no code | engine mode `any`; extra binding not declared |
| Application binding disabled | `prompt=login` loops to a second ceremony, no code | default-flow session, no `prompt`: **code** | `enabled is False` |
| Application binding deleted | as above | **code** | declared but not bound |
| Application engine mode `any` | code | default-flow session, no `prompt`: **code** | `policy_engine_mode is 'any'` |

**B4 single use.** One qualified login, its request sent 8 times at once: exactly 1
code, 7 denied, and a 9th request afterwards denied (26). Sequential exact replay:
denied (21).

**B5 rollback.** With two ceremonies parked under the redesign (after the password,
after TOTP), the rollback applied (120): both ended with the notice, no code. The
rollback again (121): valid, no warnings. `run.sh drift rollback` → PASS (122).
`run.sh drift base` → exit 1 with one difference only, the file sha512 against
`last_applied_hash` (122b). Inventory 123 against 06: the normal, enrollment, repair
and authorization bindings, the application bindings, every deployed expression
hash, consent, Prompt fields and identification stages are identical; the unbound
`animus-lan-entry-gate`, `-login-gate` and `-denied-notice` policies remain. Connect
after the rollback: 6 pages, code (124). Re-applying the redesign with two
ceremonies parked under the rolled-back flow (125): each finished its old flow and
was sent to a second ceremony (`hub_policy_gate` missing), no code. Again (126):
valid. `run.sh drift forward` → PASS (127); Connect: 2 pages (128); inventory 129
equals 12.

**B6 neighbours.** Enrollment and repair bindings are identical to the deployed
ones (12 and 129 against 06). ID-token claim names are identical before and after
(04 against 21): `acr amr aud auth_time exp given_name groups
hub_authentication_policy hub_factor_time iat iss jti name nickname nonce
preferred_username sid sub`. Two real refresh exchanges (27): signatures verified,
the same `sub`, `hub_authentication_policy`, `hub_factor_time` and `auth_time`,
`hub_factor_time` ≤ `auth_time`, a rotated refresh token; only `nonce` is absent
from the refreshed ID tokens.

**Argued from source only, with reasons:**
- *Policy subprocesses.* 2026.8.5 runs a binding in-process unless the current
  process is a daemon (`policies/engine.py:164-170`); request handling is not, so
  the gates' `_MainProcess`/daemon refusal is not reachable from a request.
  Unit-tested.
- *Gate timeout.* `timeout` applies only to subprocess evaluation
  (`policies/engine.py:174-176`), which the gates refuse; it is pinned so the drift
  check covers it.
- *A crash outside the expression.* `PolicyProcess.run` turns any exception into
  `PolicyResult(False)` (`policies/process.py:133-145`); the campaign injects
  expression errors (`PolicyException` → `failure_result`) only.
- *A change of session user or login event during one ceremony.* Needs a second
  login in the same browser mid-ceremony; both gates compare the recorded envelope.
  Unit-tested.
- *An unconfirmed or another user's TOTP device in `mfa_devices`.* The validate
  stage records only the verifying device of the pending user; the login gate's
  refusal is unit-tested.
- *The policy's own `request` refusal.* The authorize view rejects a request object
  first (`providers/oauth2/views/authorize.py:160-172`); unit-tested.
- *Partial files.* The importer applies a file in one transaction, so no prefix is
  observable; `test_fail_closed_entry_order` checks every prefix.
- *Hourly discovery and RBAC.* Discovery runs the same apply task through the same
  instance as cases 08 and the explicit Applies. The residual cases write the same
  rows an admin, an apply or an RBAC-granted API call would; `re_evaluate` off is
  possible only below the API.
- *Claim expiry.* Not waited for: the 900 s claim outlives the 300 s proof, which
  both the login gate and the application policy check.

Still to run on CT117 and the phone (T5/T6): B1 on the device, B7 (D8), B8 and B9.
