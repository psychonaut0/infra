# Guarded lifecycle and fleet monitoring

Source tooling only; installation and real-data opening require separate reviewed
operator evidence. All CT commands below assume the root management session.
Keep account records, actual command packets and evidence in private custody.

## Installation prerequisites

Install the reviewed `bundle.py`, `lifecycle.py`, `compose.py`, `capture.py`,
`restore.py`, `restic-copy.py` and `monitor.py` together in
`/usr/local/lib/animus-backup/`, root-owned0755; no test files, bytecode or spool.
Install `animus-stack.service` in `/etc/systemd/system/`; retain the existing
ingress guard and its Docker dependency. Before enabling the unit, verify API,
Auth server and worker all have `restart: "no"`, including existing containers.
Databases retain `unless-stopped`. Never use `infra restart/deploy ct-animus`,
Portainer restart or raw Compose to start response owners past this guard.

The separately reviewed private backend adapter must be installed root-owned0700
at `/usr/local/libexec/animus-recovery-check`. It accepts an expected canonical G
plus newline on stdin and the exact application database container ID as
`--container ID --project NAME`, each bound to the reviewed inventory. Its only success response is JSON
`{"matched":true,"state":"closed"}` or `{"matched":true,"state":"open"}`.
Missing/malformed/mismatched/query-failed status blocks startup. The public infra
repository contains no backend storage contract or adapter implementation.
Bind its reviewed source hash in the private installation packet and retain it
independently so a recovery does not depend on an automatically restored script.

Independent root-private control lives under `/etc/animus-control/`, outside
automatic backup restoration. `recovery.env` has the exact three assignments
described in [backup](../backup/README.md). `startup-hold` prevents every supported
response-owner start. `stop-intent` records a stop that overlapped capture or an
uncertain lifecycle failure; capture never clears it. Do not restore these files
from stack.env or take predecessor G as boot authority.

Install `/etc/animus-control/operations` as UID/GID65532 mode0700 for the operator
container's private bind mount at `/run/animus-operations`. Keep independent
root-private originals of declarations/receipts outside that directory. The
operator service carries the same exact trust/G and read-only strong-policy file
as the API but its migration DB role; it does not listen or automatically adopt/open
control. Validate both mounts with `check-config.py` before commissioning. Retain
the actual privately qualified policy/hash with the private configuration inventory;
file/mount checks do not parse its proof contents or replace supported private
API/CLI strong-auth qualification. Recovery control still has exactly three keys.

## Initial adoption and routine commands

Before installing/enabling the unit, stop response owners and retain startup-hold.
After private configuration/image validation, only these ordinary operator
Compose forms are supported:

```sh
/usr/local/lib/animus-backup/compose.py up -d app-postgres auth-postgres
/usr/local/lib/animus-backup/compose.py --profile operator run --rm migrate
```

The second form runs schema migration only. Typed recovery commands can follow
`migrate` as arguments, using reviewed files under `/run/animus-operations`, for
example `db recovery-prepare --generation G --operation-file FILE --record FILE`.
First adoption needs independently reviewed never-opened/unchanged-DB evidence
and its sealed declaration. No script here creates that declaration or replaces
an owner. A matching closed DB is valid boot authority and leaves personal routes
closed. Provider bootstrap and typed repair/open remain separately reviewed.

Only after the corresponding review releases startup-hold:

```sh
systemctl daemon-reload
systemctl enable --now animus-stack.service
```

Start stops any preexisting response owners first, validates holds/control/DB
status, starts only databases, then force-recreates response owners with current
environment. Failure drains owners and persists stop-intent. No automatic process
restart loop bypasses the gate. A crash alerts; the operator investigates before:

```sh
systemctl stop animus-stack.service
/usr/local/lib/animus-backup/compose.py operator-resume
systemctl start animus-stack.service
```

`operator-resume` clears only stop-intent after all gates pass; it does not start
response owners or clear startup/capture holds. Stop obtains the capture lock
immediately when free. Under contention it persists intent before waiting, so
capture cannot later undo the completed stop. Failed/uncertain drain also latches
intent. A normal clean stop creates no latch and allows the next guarded boot.

Qualify actual Docker restart and guest reboot with current, missing/stale G and
holds before enabling CT auto-start. A hypervisor reboot is a separate event;
no host-reboot qualification follows from the guest test.

## Backup and monitoring installation

Use [coherent backup](../backup/README.md), existing forced backup SSH key and
independently pinned host key. Only `ALLOW_ANIMUS_COHERENT=1` is enabled in the
guest dispatcher; it also permits the fixed read-only `animus-operational-status`
command. Keep generic rsync, volume exports and unrelated dump commands disabled.

On ct-backup install the same Python set, private collector config, updated
backup/pre-backup/staleness hooks, and `animus-monitor.service/timer`. After the
reviewed activation marker exists:

```sh
systemctl daemon-reload
systemctl enable --now animus-monitor.timer
```

Existing nightly03:00 backup plus <=10min jitter performs coherent collection and
separate B2 protection. Existing15min staleness checks invalidate old backup and
operational receipts. A fleet backup timestamp cannot substitute for Animus.
Install the fixed `backup-check.sh` wrapper with its updated existing unit: the
private fleet credential file uses shell export syntax and must be sourced by
that wrapper. This does not itself run a repository check or change retention.

Gatus additions use its existing Telegram credentials and Caddy status server.
They distinguish API route/process liveness, canonical provider metadata, DB and
provider service health, matching recovery authority, source/receiver disk,
collection freshness, recent authenticated read and verified off-host protection.
No new CT listener, provider account, grant or token-refresh automation is added.
Root and spool/staging storage require >=1GiB free and <=85% utilization.
Ordinary faults alert after3x1min failures; a stopped collector can take <=18min
because the independent stale check runs every15min. Backup age limit is24h.

## Daily authenticated-read duty

The single owner renews a bounded actual canonical authenticated read at least
daily using the reviewed private commissioning procedure. Its FIRST step is:

```sh
/usr/local/lib/animus-backup/monitor.py read-begin
```

That removes previous source evidence before attempting the read. Failed or
cancelled renewal leaves it absent. BEFORE attempting the actual read, run the
existing ct-backup `animus-monitor.service` once and verify the public recent-read
bit is false; failed renewal cannot retain a previous green receipt. Only a
successful independently reviewed read may install root-owned0600
`/etc/animus-control/authenticated-read.json` with exactly:

```json
{"version":1,"success":true,"checked_unix":0,"expires_unix":0,"api_container_id":"FULL_ID","api_started_at":"EXACT_STARTED_AT","generation":"G","evidence_sha256":"SHA256"}
```

Substitute actual receipt times; expiry must be within24h of the actual read.
No tokens/account IDs/response bodies enter the receipt or status server.
Expiry, API restart/recreation or G change makes the readiness bit false.
Intentional closed recovery is separately visible and cannot satisfy this bit.
This is recent evidence with <=24h+alert-delay detection, not continuous login
qualification. Prove actual-read, failed-read, expiry, failure and resolved alert
receipts before relying on this monitoring policy.
