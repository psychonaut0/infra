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
