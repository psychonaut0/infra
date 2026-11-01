# Animus coherent backup — source tooling, commissioning pending

This is the capture and bounded restore mechanism. Source tests do not
qualify the owner installation, provider repair, old-host fencing, client recovery,
key custody, alerts or an encrypted off-host copy. Nothing here opens recovery.
The legacy workout/PowerSync and other fleet paths are unchanged.

## Capture boundary and inventory

`capture.py` accepts only the configured Compose project, one running container
per API, Authentik server/worker and both PostgreSQL services, no foreign network
peer, no database host port and internal-only database networks. Every provider
volume consumer (including stopped containers on other networks and overlapping
bind mounts) must be an approved owner with the exact mount topology. Rechecks
after writer stop and immediately before each copy retain the hold on uncertainty. Stopped operator
migration containers are excluded. It records each exact image ID/reference, the
private API source revision and actual logical-dump versions; images themselves
are not archived. Keep those exact pinned images/rebuild inputs separately
available. A revision string is operator input, not proof of source provenance.

Before touching writers, the capture checks space (including a runtime reserve),
then persists `capture-hold.json`, disables writer restart policies and stops all
API response owners and Authentik writers with a bounded drain. A SIGKILL/OOM or
remaining client connection fails capture. BOTH custom-format logical database
dumps and BOTH `pg_dumpall --globals-only` files, including roles/password hashes,
come from the resulting quiet boundary. Live PostgreSQL volumes are never copied.
Auth `/data`, `/templates`, `/certs`, explicit private configuration/secrets,
blueprints/init scripts and the capture implementation come from that same hold.
Provider signing/encryption/recovery state lives in its DBs/files and secret key;
all are mandatory. External tablespaces, linked/special files, extra writers and
additional services need a reviewed format extension and currently fail closed.

The manifest lists all required payloads, lengths and SHA-256 values. Partial
outputs cannot pass verification or stream to ct-backup. Failure after holding
leaves the durable hold and restart policies for private operator inspection.
A verified complete **local** capture may resume the exact writers it stopped;
it is still unprotected until the separate B2 snapshot is verified. Nightly
capture performs no recovery generation transition, grant revocation or factor
replacement. Restart policies are containment, not an unbypassable host fence:
manual Docker/Compose commands and other supervisors must respect the hold.

## Independently held generation

Keep `/etc/animus-control/recovery.env` outside the stack and backup sources,
root-owned 0600, with exactly:

```
HUB_RECOVERY_MODE=enforced
HUB_RECOVERY_PROFILE=native-workout-v1
HUB_RECOVERY_GENERATION=<canonical nonzero lowercase UUID>
```

The API consumes its existing environment variable. Deployment must supply this
external control independently, refuse a capture hold before `compose up`, and
never obtain generation authority from an extracted stack `.env` or pointer.
Capture excludes the file, removes generation assignments from saved stack.env,
and retains only `prior_generation` metadata in the manifest. That predecessor is
never boot authority. Restore requires a different freshly held G; automatic
restoration of the external file is unsupported. Restored artifacts stay inactive.

## Reviewed installation, not performed by these files

Install the Python files together at `/usr/local/lib/animus-backup/`, root-owned
and nonwritable by service users; executables need mode 0755. Do not install Python
bytecode or an output/spool directory under the backed-up code tree. Place the
reviewed `capture.json` on ct-animus and `collector.json` on ct-backup under
`/etc/animus-backup/`, root-owned 0600. Replace every example identity/path with
verified inputs. Reserve 1 GiB initially on each runner; observed capacity is not
a guarantee. Staging, previous captures, fleet size and temporary restore space
also consume capacity; the disk guard may close capture rather than fill a disk.

The ct-backup public key keeps its existing forced-command dispatcher. Only on
ct-animus set `ALLOW_ANIMUS_COHERENT=1` in `/etc/backup-dispatch.conf`; leave its
rsync, generic volume export and unrelated dump switches disabled. The only new
command is the exact literal `animus-coherent-capture`, without caller paths or
arguments. Pin the guest SSH host key in ct-backup's known_hosts via an independent
management channel; the collector rejects unknown/changed keys. Never broaden SSH
or firewall access to make a workstation probe work.

After deployment review, an explicit `/etc/animus-backup/enabled` marker activates
the three narrow ct-backup hooks. Animus is intentionally absent from the generic
fleet CT lists. Failure to capture Animus does not prevent the existing fleet
snapshot; the subsequent Animus protect step fails the overall job and its
existing failure alert. Only `restic-copy.py protect` writes a green Animus status,
after a unique B2 snapshot and reading **every** payload back by full snapshot ID
and checksum. No `latest` selector is accepted. A local repo rehearsal is never
accepted by `protect` as off-host protection. Existing fleet success cannot refresh
Animus state. `animus.json` has `.protected == true` only after verified completion;
its timestamp is the source capture start, not upload time. Its age limit is 24h.
The existing 15min freshness timer checks it separately. Configure a separate
Gatus check on `http://backup.lan/animus.json` requiring `[BODY].protected == true`;
alert receipt remains an independent live acceptance gate. Missing/unconfigured
status must fail that monitor. No deployed alert is claimed by this source.

## Supported bounded restore

1. Fence the old host, ingress, database routes/credentials and boot control;
   independently retain evidence. Keep the replacement ingress, API, provider
   writers/jobs and proxy absent. Generate/retain fresh G outside the backup.
2. Select an exact 64-character restic snapshot ID and independently retained
   manifest SHA-256. `restore.py fetch --snapshot ID --manifest-sha256 SHA
   --source-path /var/backup-staging/animus --destination EMPTY_NEW_PATH` restores
   only that bundle into a new private directory and verifies every checksum and
   archive member before any database import. A wrong ID/hash or incomplete
   inventory fails. Secrets remain in this private artifact directory.
3. Provision ONLY two disposable PostgreSQL containers on a new internal network,
   with the manifest's exact image IDs, no published ports, no other peers and a
   different `animus-restore-*` Compose namespace. Initialize each with the sole
   non-system role `animus_restore_operator` and no application DBs. Do not run the
   normal init scripts: the logical bundle supplies roles/password hashes/DBs.
   Target JSON has `project` and `databases.app/auth`, each with `container` (full
   inspected ID) and `admin: "animus_restore_operator"`.
4. Root-private old-host fence JSON binds `generation`,
   `backup_manifest_sha256`, `old_host_fenced: true`, and `evidence_sha256` to actual
   independent evidence. A boolean does not manufacture proof; the operator and
   independent review own its truth. `restore.py import-databases --bundle PATH
   --manifest-sha256 SHA --target JSON --external-control FRESH_ENV
   --old-host-fence JSON` verifies BOTH blank targets before restoring globals
   followed by DBs. A partial failure requires discarding both disposable targets
   and starting empty; never retry over partially restored state.
5. `restore.py stage-files --bundle PATH --manifest-sha256 SHA --destination
   EMPTY_NEW_PATH` stages provider files and private config into empty inactive
   destinations. Run as root to preserve recorded UID/GID; an unprivileged rehearsal
   explicitly records ownership as unpreserved. Do not boot extracted
   configuration, restore an old external control file or start Authentik early.
   Wire provider mounts only after independently reviewing the staged ownership, pinned
   images and external fresh control. Importing DBs is not a complete recovery claim.
6. Use the separately reviewed typed `db recovery-prepare` with fresh G and the
   exact manifest, staying closed. Repair the SAME provider principal, revoke old
   sessions/access/refresh credentials, replace factors/recovery codes and verify
   normal strong login. Reconcile off-host restrictions, deletion/writer fences,
   grants, current-G reader registrations and old/offline clients/receipts before
   typed reviewed open. Never use production SQL to bypass these controls. This
   tool has no open command, provider reset or implicit owner bootstrap.

The mechanism supports only an empty isolated target with pinned versions.
Restoring over a live host, whole-image archives, hot PG copies, raw rsync, an
unfenced prior host, stale G, missing repair evidence and automatic reopen are
unsupported. Owner manual independent key custody and real-phone acceptance stay
pending. The synthetic campaign below proves only its explicitly observed scope.

## Checks and campaign gate

`python3 stacks/ct-animus/backup/test_backup.py` uses deterministic fakes only.
Run it through private `scripts/run-check`. The next checkpoint is independent
source review, followed by explicit coordinator permission for the prepared
namespace-only local synthetic campaign. No script here has been deployed and no
B2/upload/Telegram/production DB action is part of source verification.
