# ct-animus LAN commissioning

Status: configuration implementation; no provisioning or real-data opening.

## Outcome and scope

Prepare a portable API-and-login-only stack in a new unprivileged Debian 13 CT.
Phase 1 uses temporary HTTP `.lan` names through stock central Caddy, reachable
on the trusted LAN and through existing Tailscale subnet routes. HTTP is
unencrypted on the LAN and between services; synthetic commissioning only.
No Atlas, public ingress, Cloudflare changes, custom Caddy image/module, registry
publication, legacy workload mutation, summary jobs or purge enablement.

Host/address values and private image identities are operator inputs. Keep source
checkouts, actual identities, tokens and application credentials off this public
repository. Real-data opening remains CLOSED until separate identity, admission,
coherent backup and isolated restore qualification and an owner opening decision.

## Acceptance

- Stack has API, separate application/Auth PostgreSQL and Authentik server/worker;
  supported patch images are digest-pinned; no Redis, host source build or Docker socket.
- Databases publish no host ports. API/login bind only the reserved private CT IP.
  A guest-local nftables pre-DNAT guard permits only central Caddy upstream access
  and explicit administration/backup SSH sources. No host/shared firewall is changed.
- API is a privately loaded, checksummed non-root image. Runtime DB credentials
  are separate from migration credentials; Authentik is not a DB superuser.
- Default startup excludes API and application migrations. Deliberate commissioning
  uses an exact OIDC registry and a compatible explicitly reviewed LAN-HTTP API
  configuration; no issuer substitution or automatic application identities/grants.
  A fresh IdP's opaque operator password is initialized through an explicit private
  temporary hashed bootstrap before login listens, then removed from worker config.
- Stock Caddy and the existing `.lan` DNS CLI provide temporary LAN names.
  Clients and containers resolve the same issuer. Remote client DNS configuration
  is explicit; accepting subnet routes does not automatically configure DNS.
- Local Compose policy and shell syntax checks pass. Host/firewall/container
  runtime, phone login, persisted reads, reboot, monitoring and restore acceptance
  remain separately recorded UNRUN until actually exercised.

## Tasks

1. Prepare `stacks/ct-animus/` deployment, placeholder examples, fresh-volume role
   initialization, read-only preflight and explicit create-stopped, link-down
   provisioning. Add console-only listener lock, guest guard and persistent
   SSH/Docker startup verification before connecting application ports.
2. Validate synthetic configuration without printing secrets; reject unsafe bind,
   public image and reused-role credentials. Test kernel pre-DNAT source policy,
   IPv6/direct-route denial, atomic failure and drift in disposable namespaces.
   Check shell syntax and whitespace.
3. Independently review the exact configuration. Complete fleet registration,
   backup/monitoring integration and live deployment under separate owner direction;
   use [new CT checklist](../../new-ct-checklist.md), including every live half.
4. Commission only synthetic identities/data. Qualify LAN/remote DNS, trust,
   firewall denies, admission/recovery, coherent off-host backup and isolated
   restoration before proposing any real-data opening.
5. Add only the static LAN public-client/application blueprint (`alkion-lan`), exact
   development callback and read-only worker mount. Document one-time supported
   operator initialization with network-facing login stopped; no user, grant or
   application owner seed. Default flows and PKCE enforcement remain unqualified.

## Checks and current limits

`python3 stacks/ct-animus/check-config.py --self-test` → PASS: Compose render and
positive policy checks; wildcard bind/public image/reused DB-role secret rejection.
`bash -n stacks/ct-animus/{provision-host,bootstrap-ct,check-private}.sh stacks/ct-animus/db/*.sh`
→ PASS. New-file whitespace check → PASS.
`unshare --user --map-root-user --net python3 stacks/ct-animus/network/check-guard.py`
→ PASS: real kernel rules with simulated Docker DNAT; correct source allow/deny,
IPv6/direct-route/DB denial, reply egress, atomic failure preservation, drift
rejection and independent NAT lifecycle. No host namespace rules were modified.

Isolated Docker DB qualification, 2026-10-30: exact init scripts and pinned
PostgreSQL with the existing API production-image baseline → PASS. Migration
completed; SELECT, INSERT, UPDATE and DELETE were each verified across all 113
migrated public tables; runtime/Auth roles are non-superusers; runtime schema DDL
was denied; tables survived a DB restart. Unique synthetic resources were removed.
This establishes no provider, authenticated-read or production recovery readiness.

Host preflight dependency correction, 2026-10-30: the first live preflight stopped
before mutations because `jq` was absent. Host JSON parsing now uses existing
Python 3, with bounded missing-tool diagnostics; no host package was installed.
`bash -n` and 11 fake-CLI preflight cases → PASS, covering fresh/existing VMIDs,
malformed JSON, exact/below memory/storage floors and missing-tool rejection.
The parser correction preceded the subsequent reviewed new-guest operations.

Live guest containment qualification is recorded privately: intended-source matrix,
missing-guard Docker startup denial and valid-guard daemon restart → PASS. No
application container or credential was initialized by that qualification.

LAN provider preparation → PASS for Compose policy, standard-only blueprint models
and exact client/callback/issuer/subject/signing references, read-only mount
allowlist, base-bootstrap and writable-mount rejection, temporary hashed-bootstrap
canonical serialization and runbook shell syntax. These are configuration checks;
Create-only local Engine sentinel → PASS: the quoted private env-file hash reached
container configuration exactly; network disabled, process never started and owned
test container removed. Actual first-setup closure/provider state/S256 negative
cases remain separately qualified before use.

Compose checks parse configuration only; the isolated kernel harness tests the
guard without exercising an actual LXC, systemd boot or Docker daemon. Those
configuration checks do not mutate hosts, DNS, shared Caddy, accounts, registries
or backups. API LAN-HTTP compatibility,
loaded image verification, non-root Auth volume ownership, DB initialization and
health checks require actual runtime qualification. Configuration is not evidence
of backup coverage or real-data readiness.
