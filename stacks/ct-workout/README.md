# ct-workout — workout-tracker backend

Self-hosted backend for the workout-tracker phone app: a Go API + its Postgres,
plus the PowerSync service + its dedicated bucket-storage Postgres.

- **API:** `http://workout.lan` → `ct-workout:8080` (the app's `apiBaseUrl`)
- **PowerSync:** `http://workout-sync.lan` → `ct-workout:8090` (the `POWERSYNC_URL` the API returns to clients)
- Private (LAN + Tailscale), no public exposure.

## Source of truth / image

The Go server image is built + pushed from the **`psychonaut0/workout-tracker`** repo
(`server/Dockerfile`) to `ghcr.io/psychonaut0/workout-tracker-server:sha-<short>`.
This stack only owns the deploy config.

- **Roll forward:** bump the `server.image` tag here, then `infra deploy ct-workout`.
- **Roll back:** pin a previous `:sha-<short>` tag.

Postgres (`postgres:16.4-alpine`) and PowerSync (`journeyapps/powersync-service:1.21.0`)
use upstream public images.

## Secrets (not committed; captured by ct-backup)

On the CT at `/opt/stacks/ct-workout/`:
- `.env` — copy from `.env.example`, fill with real long-random values.
- `secrets/jwt_private_key.pem` — the API's JWT signing key (mode `0600`, owned by
  `SERVER_UID:SERVER_GID`). Generate the same way the app repo does
  (`server/Makefile` `gen-jwt-key`, e.g. `openssl genpkey`).

## One-time replication-role step

Migration `00004` (run by the server on startup) creates `powersync_role` as
`NOLOGIN` with no password and the `powersync` publication. After the app Postgres
+ server are up (migrations applied), grant the role LOGIN + the password from
`.env`, against the app DB:

```bash
docker exec -i workout-tracker-postgres-1 \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "ALTER ROLE powersync_role LOGIN PASSWORD '<PS_REPLICATION_PASSWORD>';"
```

The password must match `PS_REPLICATION_PASSWORD` in `.env`. Verify the publication
exists with `\dRp` (expect `powersync`). Then bring up the `powersync` service.

## Backups

`ct-backup` captures `.env` automatically, plus two Postgres dumps via
`backup-dispatch.sh`: `pg-dump-workout` (app DB) and `pg-dump-powersync`
(bucket-storage DB).
