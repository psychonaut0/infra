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
| Authentik | `2026.8.5`, OCI index `sha256:602227a86455d6772e3d16b7d852713c4f864f40df5df894abd1285bb53979a5`; current patch, replaces older rehearsal `2026.8.3`. |
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
leave the `commissioning` profile stopped. Exact client ID, callback list, PKCE
policy, provider slug and production registration remain operator-qualified inputs;
no default Authentik flow/provider is represented as accepted.

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
4. Use `python3 check-config.py` for secret-safe validation; once the image is
   loaded, use `python3 check-config.py --inspect-image`. These never print the
   rendered Compose environment. Do not paste `docker compose config`, container
   inspect output, bearer tokens or provider challenge logs into public records.

Compose explicitly selects environment values rather than passing all of `.env`
to every container. Docker administration still grants access to process secrets;
restrict it to trusted operators. The two databases have independent volumes and
bootstrap secrets. DB network traffic stays on an internal Docker bridge without
TLS and no host-published database port.

## Synthetic startup, migrations and roles

Default `docker compose up -d` starts only the two databases and Auth processes.
First prepare Auth volume permissions before starting the long-lived non-root
processes, using the pinned image as a one-shot root tool:

```sh
docker compose run --rm --no-deps --user 0 --entrypoint /bin/sh auth-worker \
  -c 'chown -R 1000:1000 /data /templates /certs'
docker compose up -d app-postgres auth-postgres auth-server auth-worker
docker compose --profile operator run --rm migrate
```

Fresh-volume initialization creates `animus_runtime` as a non-superuser with
public-schema DML/sequence access through migration-role default privileges;
it cannot create roles/databases or schema objects. `animus_admin` is the separate
cluster-superuser/migration owner, used only by the explicit one-shot migration
container. Authentik owns its own DB and automatic schema migrations under a
non-superuser role; its cluster bootstrap password never reaches server/worker.
Init scripts run only on **empty** volumes. They neither upgrade existing clusters
nor repair restored roles. Changing `.env` does not rotate existing DB passwords.

Set up Authentik through its initial-setup flow only after Caddy/DNS/firewall review.
Use fresh synthetic operator credentials with password, TOTP and recovery codes;
passkeys require the later HTTPS phase. Disable self-enrollment and qualify
the exact provider: RS256, PKCE S256, exact allowed redirect URIs, singleton
client/audience, correct issuer/JWKS/UserInfo. Do not blindly accept default flows.
Do not use automated bootstrap tokens/password hashes or owner/grant provisioning.
After registering the provider and validating a compatible API configuration:

```sh
python3 check-config.py --inspect-image
docker compose --profile commissioning up -d api
```

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
