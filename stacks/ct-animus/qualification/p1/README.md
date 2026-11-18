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

Run 2026-11-18 (receipts private, outside the repository):

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
