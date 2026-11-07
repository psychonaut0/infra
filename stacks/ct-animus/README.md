# ct-animus — LAN synthetic commissioning

API and identity only, in a new unprivileged Debian 13 CT. **Personal-data opening
is CLOSED.** This descriptor and its checks do not provision or qualify a host.
Use temporary HTTP `.lan` origins through the existing stock central Caddy and
Pi-hole; existing Tailscale subnet routes can carry remote traffic. HTTP sends
credentials/data unencrypted on the LAN. Use synthetic accounts and bytes only.
Stable HTTPS names and any public ingress are deferred to a later phase.

No source checkout or public image registry is needed on the CT. No Atlas,
summary worker, purge, Redis, Docker socket, tunnel or legacy workload is included.
See the [scoped plan](../../docs/superpowers/plans/2026-10-30-ct-animus-private-hosting.md)
and [new CT checklist](../../docs/new-ct-checklist.md).

## Pins and application compatibility

Verified against upstream on 2026-10-30:

| Input | Pin and reason |
| --- | --- |
| Authentik | Operator input `AUTH_IMAGE` plus `AUTH_IMAGE_ID`: a privately loaded `local/animus-auth:<tag>` image recorded by image ID, like the API, and never pulled. The upstream base is `2026.8.5`, OCI index `sha256:602227a86455d6772e3d16b7d852713c4f864f40df5df894abd1285bb53979a5`; which reviewed build to load is an operator decision outside this repository. |
| PostgreSQL | `17.11-bookworm`, OCI index `sha256:839fbb5a4942a7758dd93e18b42ea5649807491a0545b5eeeb8f0b8bb11857a5`; supported patch on existing major 17, replaces rehearsal `17.6`. |
| API | Operator supplies a private `local/animus-api:sha-<revision>` tag, exact local image ID and archive checksum from the qualified non-root production build. |

Sources: [Authentik release notes](https://docs.goauthentik.io/releases/2026.8/),
[official versioned Compose](https://goauthentik.io/version/2026.8/lifecycle/container/compose.yml),
[Authentik configuration](https://docs.goauthentik.io/install-config/configuration/),
[PostgreSQL release notes](https://www.postgresql.org/docs/release/),
[Docker Official PostgreSQL image](https://hub.docker.com/_/postgres).
Authentik supports PostgreSQL 14–18 and no longer needs Redis. The official
Compose mounts a Docker socket for managed outposts; none are needed here, so
this stack omits it and runs both Auth processes as UID 1000.

The API must explicitly support an exact, opt-in LAN-HTTP OIDC trust policy.
The required new switch is `HUB_OIDC_PRIVATE_HTTP_ORIGIN=http://auth.lan`
(bound to `LOGIN_FQDN` by Compose); empty/unset disables private HTTP trust.
The earlier API accepted plain HTTP only on loopback; this deployment cannot
work around that by inventing a different issuer. A registry example does not
prove compatibility. Until a reviewed compatible image/configuration is supplied,
leave the `commissioning` profile stopped. The static commissioning blueprint
selects public client/application `alkion-lan`, its exact development callback and
standard provider fields. Default flows, PKCE enforcement, recovery and any
production registration remain unqualified; no admission follows from this file.

## Host reservation, offline creation and containment

Proposed envelope: 2 vCPU, 6144 MiB RAM, 1024 MiB swap, 32 GiB SSD rootfs,
with `memory.high=5 GiB` below the 6 GiB hard cap. Re-measure actual available
memory/storage plus concurrent guest peaks; configured guest limits and inventory
documents do not establish capacity. Confirm unused VMID through cluster inventory.
Address reservation/collision review gates network connection, not offline creation.
An absent neighbor entry or unanswered ping does not reserve an address.

`provision-host.sh` runs on the intended Proxmox node as root, supplied exported
`TARGET_NODE`, `VMID`, `CT_IPV4`, `CT_GATEWAY`, `PRIVATE_DNS_IPV4`, `PROXY_IPV4`,
`BACKUP_IPV4`, `ADMIN_CIDR`, `TEMPLATE` and `SSH_PUBLIC_KEY_FILE` values.
All addresses must be private IPv4; administration is a deliberately selected
CIDR at least `/24` in specificity, preferably a workstation `/32`. The template
must be an already cached Debian 13 amd64 image. The script refuses any existing
VMID and never enables or modifies cluster/host firewalls.

```sh
./provision-host.sh --preflight
# Only after resource and disconnected-bootstrap safety review:
RESOURCE_PEAK_REVIEWED=yes ISOLATED_BOOTSTRAP_REVIEWED=yes \
./provision-host.sh --create-stopped
```

Creation leaves the guest **stopped, onboot=0, net0 link_down=1**. Only the parent
operator may create/start/connect it. A console-only bootstrap masks all network
listeners and container runtimes before any network connection. After address
reservation the guest may connect for package egress, install its own nftables
guard, and enable key-only SSH only after exact live-rule verification. Docker
remains masked until the guard's positive/negative source probes pass.

Follow the full [guest network runbook](network/README.md), including restart,
reboot and failed-guard tests. The guard runs before Docker's DNAT for IPv4 and
explicitly denies uplink IPv6. It owns only a guest-local table and has persistent
systemd dependencies for SSH/Docker. It must work inside the unprivileged LXC;
missing kernel capability keeps deployment blocked, with no fallback to a weaker
INPUT-only firewall or shared fleet firewall activation.

Install Docker from the established signed Debian repository **while runtime
units remain masked**. CA roots, curl, jq, Python 3 and **rsync** are installed
by bootstrap. Install the infra CLI using the existing LAN mirror. Keep source
and build tools off the CT. No TUN or local Tailscale daemon is required.

## Private delivery and secrets

1. On the build workstation, create the existing qualified production API image
   with only allowlisted build inputs, tag it with its exact revision and record
   `docker image inspect` ID/user (`65532:65532`), config/schema revision and source
   revision privately. Export with `docker image save`, checksum the archive,
   transfer over SSH and verify the checksum before `docker image load` on the CT.
   Re-check the loaded image ID/user. Do not use `docker export`, a registry push
   or a source checkout on the guest. Retain the prior archive/identity for recovery.
   Deliver the reviewed Auth image the same way: `docker image load`, tag it
   `local/animus-auth:<tag>`, and record its ID as `AUTH_IMAGE_ID`. Changing it
   changes the built-in Blueprints, so the flow-fence generation check refuses
   boot until a new fence record matches it (see [fence](fence/README.md)).
2. Copy this deployment directory to `/opt/stacks/ct-animus`. Copy `.env.example`
   privately to `.env`, mode 0600. Replace every placeholder; use separate
   `openssl rand -hex 32` outputs for the four DB credentials and
   `openssl rand -hex 64` for the Auth secret. Never capture these outputs in
   evidence/logs. The API accepts a URL, so these passwords intentionally need no
   URL escaping. `.env` is not shell code: do not source it or export its contents.
3. Create a private `secrets/` directory and exact OIDC registry from the example
   only after provider registration is agreed. On the CT, mode 0600 and owner
   `65532:65532` for the registry; parent directories must be traversable by that
   UID (for example 0750 with group 65532). The registry mount is read-only.
   Keep the provider's real signing keys and bootstrap/recovery credentials private.
   Separately deliver the actual privately qualified strong-auth policy as
   `STRONG_AUTH_POLICY_FILE`, also owner `65532:65532` and mode 0600. API and
   operator CLI must bind that SAME file read-only at
   `/run/animus/strong-auth-policy.json` and select it with
   `HUB_STRONG_AUTH_POLICY_FILE`. No API trust-policy content or production
   qualification receipt is supplied by this repository; actual policy/hash/
   not-before remain UNSET until private final P1 acceptance.
4. Use `python3 check-config.py` for secret-safe validation; once the images are
   loaded, use `python3 check-config.py --inspect-image`, which checks both recorded
   image IDs with `docker image inspect` (and the API's non-root user). These never print the
   rendered Compose environment. Do not paste `/usr/local/lib/animus-backup/compose.py config`, container
   inspect output, bearer tokens or provider challenge logs into public records.
   The strong-policy checks validate only regular/private file ownership and
   matching read-only mount/environment structure in BOTH services. They do not
   parse policy contents or qualify authentication. Supported private API/CLI
   policy validation and actual recent strong-owner positive/negative evidence
   remain required before grants or opening; DNS/liveness preflight cannot replace them.

Compose explicitly selects environment values rather than passing all of `.env`
to every container. Docker administration still grants access to process secrets;
restrict it to trusted operators. The two databases have independent volumes and
bootstrap secrets. DB network traffic stays on an internal Docker bridge without
TLS and no host-published database port.

## Synthetic startup, migrations and roles

All supported operator Compose commands below use the guarded launcher installed
from [backup/](backup/README.md). It refuses a durable capture hold and requires
independently held enforced recovery control at `/etc/animus-control/recovery.env`.
Install the launcher before commissioning; never restore that control from a stack
backup. Direct Docker/Compose commands remain administrator authority and bypass
the supported hold contract.

Do not use an unrestricted first `/usr/local/lib/animus-backup/compose.py up -d`: it would publish login
before an operator password was established. Keep `auth-server` stopped until the
one-time hashed bootstrap below has succeeded and been removed. First prepare
Auth volume permissions and start only the databases, using the pinned image as
a one-shot root tool:

```sh
/usr/local/lib/animus-backup/compose.py run --rm --no-deps --user 0 --entrypoint /bin/sh auth-worker \
  -c 'chown -R 1000:1000 /data /templates /certs'
/usr/local/lib/animus-backup/compose.py up -d app-postgres auth-postgres
/usr/local/lib/animus-backup/compose.py --profile operator run --rm migrate
```

Fresh-volume initialization creates `animus_runtime` as a non-superuser with
public-schema DML/sequence access through migration-role default privileges;
it cannot create roles/databases or schema objects. `animus_admin` is the separate
cluster-superuser/migration owner, used only by the explicit one-shot migration
container. Authentik owns its own DB and automatic schema migrations under a
non-superuser role; its cluster bootstrap password never reaches server/worker.
Init scripts run only on **empty** volumes. They neither upgrade existing clusters
nor repair restored roles. Changing `.env` does not rotate existing DB passwords.

## One-time operator bootstrap before opening login

Use the supported [Authentik hashed automated installation](https://docs.goauthentik.io/install-config/automated-install)
on a **fresh** Auth database. This creates only Authentik's default `akadmin`
operator account with a new opaque password; it is not a personal identity and is
never automatically bound to the application owner. Do not supply a bootstrap API
token/email, create users in the blueprint or seed application grants. The base
Compose file contains no bootstrap variables, and its validator forbids them.
If an unknown initial setup has already run, keep login stopped and investigate
through the console rather than resetting credentials automatically.

Run privately in the deployment directory with shell tracing disabled. These
commands generate files, never print the raw password or hash, and use stdin for
the supported `hash_password` command (not argv or a plaintext environment value):

```sh
set -euo pipefail
set +x
umask 077
python3 - <<'PY'
import secrets
from pathlib import Path
# secrets/ already exists for the private registry; never replace an old password.
with Path("secrets/initial-akadmin-password").open("x") as output:
    output.write(secrets.token_urlsafe(48) + "\n")
PY
/usr/local/lib/animus-backup/compose.py run --rm --no-deps -T auth-worker hash_password \
  < secrets/initial-akadmin-password > secrets/initial-akadmin-password.hash
python3 - <<'PY'
import json
from pathlib import Path
value = Path("secrets/initial-akadmin-password.hash").read_text().strip()
if len(value.splitlines()) != 1 or not value.startswith("pbkdf2_sha256$") or len(value.split("$")) != 4:
    raise SystemExit("Unexpected hash output; keep auth-server stopped")
# Keep Django's dollar separators literal using the supported quoted env-file form.
with Path("secrets/initial-akadmin.env").open("x") as output:
    output.write("ANIMUS_INITIAL_ADMIN_HASH='" + value + "'\n")
# JSON is valid YAML. The hash is read only for this explicit one-time invocation.
override = {"services": {"auth-worker": {"environment": {
    "AUTHENTIK_BOOTSTRAP_PASSWORD_HASH": "${ANIMUS_INITIAL_ADMIN_HASH:?private initial hash required}"}}}}
with Path("auth-bootstrap.local.yaml").open("x") as output:
    json.dump(override, output)
PY
/usr/local/lib/animus-backup/compose.py --env-file .env --env-file secrets/initial-akadmin.env \
  -f docker-compose.yml -f auth-bootstrap.local.yaml \
  up -d --wait --wait-timeout 180 auth-worker
```

Keep the final network-facing server stopped. Start one owned temporary server
without `--service-ports`; Compose `run` does not publish the service's port mapping.
The guest guard also denies direct access to its Docker address. Check closure
through the supported [public flow executor](https://api.goauthentik.io/flow-executor/),
using a fresh anonymous request to loopback inside that server with the canonical
Host. The probe prints only HTTP status and a whitelisted component, never response
headers, cookies, URLs, error messages, password/hash or challenge fields. The exact
2026.8.5 response remains a runtime check: only documented access denial passes;
any other component/unavailable response keeps final login stopped for inspection.

```sh
animus_setup_probe=$(/usr/local/lib/animus-backup/compose.py run -d --no-deps auth-server)
[[ "$animus_setup_probe" =~ ^[0-9a-f]{64}$ ]]
trap 'docker rm -f "$animus_setup_probe" >/dev/null 2>&1 || true' EXIT
[[ -z "$(docker port "$animus_setup_probe")" ]]
# Wait/retry this healthcheck until it succeeds before the anonymous HTTP probe.
docker exec "$animus_setup_probe" ak healthcheck >/dev/null
check_initial_setup_closed() {
  docker exec -i "$animus_setup_probe" python - <<'PY'
import json, sys, urllib.error, urllib.request
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
request = urllib.request.Request(
    "http://127.0.0.1:9000/api/v3/flows/executor/initial-setup/",
    headers={"Host": "auth.lan", "Accept": "application/json"})
status, component = 0, None
try:
    try:
        response = opener.open(request, timeout=15)
    except urllib.error.HTTPError as error:
        response = error
    status = response.code
    document = json.loads(response.read(1048576))
    component = document.get("component") if isinstance(document, dict) else None
except (OSError, ValueError):
    component = None
allowed = {"ak-stage-access-denied", "ak-stage-prompt", "ak-stage-identification",
           "ak-stage-password", "xak-flow-redirect"}
print(json.dumps({"http_status": status, "component": component if component in allowed else "unrecognized"}))
sys.exit(0 if component == "ak-stage-access-denied" and status < 500 else 1)
PY
}
check_initial_setup_closed
# Recreate the worker with BASE configuration only; the hash leaves its environment.
/usr/local/lib/animus-backup/compose.py up -d --no-deps --force-recreate --wait --wait-timeout 180 auth-worker
rm auth-bootstrap.local.yaml secrets/initial-akadmin.env secrets/initial-akadmin-password.hash
animus_worker_id=$(/usr/local/lib/animus-backup/compose.py ps -q auth-worker)
docker inspect --format '{{range .Config.Env}}{{println (index (split . "=") 0)}}{{end}}' \
  "$animus_worker_id" | python3 -c \
  'import sys; sys.exit(any(key.startswith("AUTHENTIK_BOOTSTRAP") for key in sys.stdin.read().splitlines()))'
check_initial_setup_closed
python3 check-config.py --inspect-image
docker rm -f "$animus_setup_probe" >/dev/null
trap - EXIT
# Only now may the reviewed, network-facing login process start.
/usr/local/lib/animus-backup/compose.py up -d --wait --wait-timeout 180 auth-server
```

This gate proves initial-setup denial, not successful operator login or factor
recovery. Do not query provider database tables to infer account/provider state.
Anonymous discovery/JWKS may verify issuer, endpoint and RSA/RS256 metadata; use
supported configuration/APIs for other provider fields. Do not add the final LAN
route or open admission while any of these required runtime gates is unresolved.

The temporary override is mode 0600 and ignored by `*.local.*`; never commit it,
render it to shared output, export container inspect environments or persist its
bootstrap variable in `.env`/base Compose. Keep the initial password file private
until the operator has transferred it into private credential custody; do not
paste it into logs or assistant output. The unpublished-server checks must pass
before final login starts; inspect only environment key names to confirm bootstrap
removal. If any check fails, keep `auth-server` stopped; no owner or user admission
is authorized by bootstrap.
Confirm self-enrollment is unavailable before any ordinary-user commissioning.

## LAN provider and API startup

The worker mounts only `blueprints/alkion-lan.yaml` read-only. The closed
provider configuration preserves the existing provider/application name,
client ID/slug `alkion-lan`, strict callback
`dev.animusstudio.alkion.dev:/oauth2redirect`, per-provider issuer, UUID subject,
ten-minute access tokens and existing RSA signing-certificate reference.

Application-specific `animus-lan-*` flows require password plus TOTP through
mandatory entry/final Prompt validators. Missing TOTP denies; submitted fields
never supply proof/time. Separate controlled enrollment and repair declare
non-admin eligibility groups without seeding memberships. Repair requires the same account password plus
one unused recovery code, validates replacement TOTP/ten new codes and logs out;
it creates no normal-login proof. Provider controls alone do not retire factors,
revoke all credentials or invalidate every old partial plan: the reviewed
full-hold/drain/operator envelope and its actual qualification remain required.
The global default administrator authentication flow is unchanged.

The internal trusted login-event key is `hub_policy_id`; only the signed public
claim remains `hub_authentication_policy`, with `hub_factor_time`. Original-grant
subject/event/authentication-time association must hold; refresh retains the
original conservative bound or loses proof and cannot acquire a later login's
strength. The retained experimental policy ID is not an activated API trust
policy. `STRONG_AUTH_POLICY_FILE` qualification receipt/ID/not-before remain UNSET.
The custom openid mapping uses a direct blueprint reference; profile/offline_access
use unambiguous managed mappings, preserving the native requested scopes.

Application issuance is unconditionally denied by the closure policy. This
blueprint seeds no user, membership, password, secret, Hub owner or grant. Empty
groups are not owner commissioning. Production factor/recovery/partial-plan,
perimeter, device and final policy qualification must finish before a separately
reviewed open configuration, policy receipt, owner/admission or real-data opening.
No synthetic fixture identities or private qualification evidence belong here.

After blueprint application, verify the actual provider fields and RSA/RS256 key
from the running instance, not just a successful worker healthcheck. The exact API
registry must select `http://auth.lan/application/o/alkion-lan/`, singleton
client/audience `alkion-lan`, matching discovery/JWKS and same-origin UserInfo.
Verify discovery through the canonical host from clients and application containers.
Do not replace the issuer with a container name or loopback URL.

The explicit application policy requires the registered client/callback and
S256 PKCE; `client_type: public` alone is not that requirement. No unsupported
`require_pkce` field is added. Production/device wrong/missing verifier, wrong
callback, missing/plain challenge and no-grant/wrong-space denials remain required;
synthetic source/runtime evidence does not qualify the production installation.

After the supported bootstrap, verified provider and compatible API configuration:

```sh
python3 check-config.py --inspect-image
/usr/local/lib/animus-backup/compose.py --profile commissioning up -d api
```

No owner bind, grants, source/writer/reader registration, ingestion or real data is
automatically created. An authenticated identity without application admission
must still be denied; all disabled summary/purge capabilities remain off.

No API shell/curl/health subcommand exists in the scratch image: the API's Compose
healthcheck is explicitly disabled. This is an exception to the new-CT checklist;
external HTTP `/healthz` and authenticated persisted reads are required. `/healthz`
is only process liveness. DB readiness, issuer/UserInfo reachability, admission,
no-grant/wrong-space denial, real-phone recovery and persisted reads are distinct.
Authentik logs are disabled because failed challenges can contain credentials;
use its secret-safe event views and external health checks for diagnosis.

## Stock Caddy, DNS and private reachability

Use the existing CLI for each temporary `.lan` name, with private operator values:

```sh
infra dns add "$API_LAN_HOST" "http://${CT_IPV4}:8095"
infra dns add "$LOGIN_LAN_HOST" "http://${CT_IPV4}:9000"
infra dns ls
```

The names in the API trust registry, Authentik base URL and clients must match
exactly. Central Caddy forwards only to those two allowlisted ports; the Authentik
trusted-proxy setting is only the central Caddy IP `/32`. Review existing Caddy
forwarding behavior and prove spoofed Host/proto/forwarding headers do not change
the issuer or authorize a client. Do not broaden global `trusted_proxies` to make
this work. Existing family service rules remain shared and require regression checks.
There is no HTTPS listener/module, DNS-01 token, Cloudflare or public tunnel change.

Containers receive the private resolver explicitly. Verify it resolves the same
`.lan` issuer as client devices. Tailscale route acceptance does not configure DNS:
on a workstation with `accept-dns=false`, explicitly install the two temporary
hosts entries pointing to central Caddy, or configure the local resolver to route
`.lan` to the private DNS server over the subnet route. On a systemd-resolved
client, use an explicitly selected interface's route-only `~lan` DNS configuration;
if it is not active, configure the actual OS resolver instead. Confirm phone DNS
on both Wi-Fi and remote tailnet; do not assume MagicDNS or an IP route provides it.
Record how to undo temporary hosts/resolver entries before the later HTTPS move.

Run `check-private.sh` with exported `API_FQDN`, `LOGIN_FQDN`, `PROXY_IPV4`,
`OIDC_SLUG` separately from LAN and remote clients. It uses the actual resolver,
not a forced IP override. Container discovery/UserInfo also needs a separate
probe using the same Docker network/resolver, since the scratch API has no shell.
Prove central Caddy reaches both upstreams, an unrelated LAN client cannot reach
8095/9000 directly, only intended admin/backup sources reach SSH, and no database
port is reachable. Verify off-tailnet clients cannot reach the private origins.

## Backup, restore and completion gates

There is **no backup protection claim** for this new stack. Before any real-data
opening, implement a coherent quiesced boundary across both DBs, globals/roles,
application config/image identities, provider signing/encryption/recovery material,
Auth data/templates/certs and all future originals/files. Do not raw-rsync named
volumes or hot PostgreSQL data directories. Review ct-backup's staging headroom
before adding bundles; avoid duplicated archives and whole Docker image stores.
Complete dispatcher/forced-command integration, encrypted off-host verification,
independent decryption-key custody and an isolated restore with revocation/stale
client/old-host fence checks. Plain `pg_dump` does not preserve cluster roles.

Restore to an isolated, fenced guest under the same pinned versions, with writer
and external admission paths closed. Restore reviewed globals/ownership, then
databases and persistent files/secrets from one complete checksummed bundle;
do not let fresh init scripts invent replacement role passwords or provider keys.
Perform forward migrations deliberately. A DB downgrade/image rollback is not a
recovery plan after schema changes; restore a compatible full bundle in isolation.
Keep restoration closed if any identity, receipt, generation or client reconciliation
prerequisite is missing. No restore automation is supplied by this configuration.

Checklist exceptions/pending work: API external health probes as above; Portainer
agent deferred (no management Docker socket or port 9001); onboot remains disabled
until restart/reboot qualification. Fleet release, deployed monitoring/alerts and
coherent backups still require both repository and live integration. Only enable
onboot/restart acceptance after the operator has verified them. No real-data opening
can follow from green Compose parsing or synthetic tests alone.
