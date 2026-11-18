#!/usr/bin/env bash
# Isolated synthetic qualification of the 2026-11-18 normal-flow simplification.
#
# Everything runs on one fresh --internal Docker network: no published port,
# no egress, no shared volume, no Docker socket and no contact with any real
# instance. Users, passwords and TOTP keys are synthetic and generated per run
# into the private state directory, which is the only writable host mount.
#
#   T4_STATE=/private/dir ./run.sh up
#   T4_STATE=... ./run.sh seed base|users|forward|rollback|inventory
#   T4_STATE=... ./run.sh actor SET [SET ...]      # SETS: see actor.py SCENARIOS
#   T4_STATE=... ./run.sh down
#   T4_STATE=... ./run.sh campaign   # the full recorded sequence, results in $T4_STATE/results
#
# `up` copies the previous blueprint (git bcd1a2e, the branch base) into the state
# directory so the forward apply runs over the real previous state, as on CT117.
set -euo pipefail
set +x
umask 077

HERE=$(cd "$(dirname "$0")" && pwd)
STACK=$(cd "$HERE/../../.." && pwd)
REPO=$(git -C "$STACK" rev-parse --show-toplevel)
AUTH_IMAGE=${AUTH_IMAGE:-local/animus-auth:2026.8.5-class-isolation-307b0103af3d}
AUTH_IMAGE_ID=${AUTH_IMAGE_ID:-sha256:a662172cf4530a83982dfdea9ce5e41aebb66b9d7ade9e90cf22394e0724c38a}
PG_IMAGE=postgres:17.11-bookworm@sha256:839fbb5a4942a7758dd93e18b42ea5649807491a0545b5eeeb8f0b8bb11857a5
STATE=${T4_STATE:?private state directory required}
[[ -d "$STATE" && ! -L "$STATE" && "$(stat -c %a "$STATE")" == 700 ]] || { echo "T4_STATE must be a private 0700 directory" >&2; exit 2; }
LOG_DRIVER=$([[ "${T4_DEBUG_LOGS:-}" == 1 ]] && echo json-file || echo none)

name() { cat "$STATE/nonce"; }

auth_env() {
  local pg_password secret
  pg_password=$(cat "$STATE/pg-password")
  secret=$(cat "$STATE/secret-key")
  printf '%s\n' "AUTHENTIK_POSTGRESQL__HOST=t4-pg" "AUTHENTIK_POSTGRESQL__NAME=authentik" \
    "AUTHENTIK_POSTGRESQL__USER=authentik" "AUTHENTIK_POSTGRESQL__PASSWORD=$pg_password" \
    "AUTHENTIK_SECRET_KEY=$secret" "AUTHENTIK_LISTEN__HTTP=0.0.0.0:9000" \
    "AUTHENTIK_WEB__BASE_URL=http://auth.lan" "AUTHENTIK_ERROR_REPORTING__ENABLED=false" \
    "AUTHENTIK_DISABLE_UPDATE_CHECK=true" "AUTHENTIK_DISABLE_STARTUP_ANALYTICS=true" > "$STATE/auth.env"
}

seed() {
  docker exec -e T4_PHASE="$1" "$(name)-worker" ak shell -c "exec(open('/t4/seed.py').read())" 2>/dev/null \
    | sed -n 's/^T4RESULT //p'
}

case "${1:-}" in
  up)
    [[ ! -e "$STATE/nonce" ]] || { echo "state already in use" >&2; exit 2; }
    [[ "$(docker image inspect --format '{{.Id}} {{.Config.User}}' "$AUTH_IMAGE")" == "$AUTH_IMAGE_ID 1000" ]] \
      || { echo "auth image identity mismatch" >&2; exit 2; }
    docker image inspect "$PG_IMAGE" >/dev/null
    echo "t4-$(python3 -c 'import secrets; print(secrets.token_hex(4))')" > "$STATE/nonce"
    python3 -c 'import secrets; print(secrets.token_hex(32))' > "$STATE/pg-password"
    python3 -c 'import secrets; print(secrets.token_urlsafe(64))' > "$STATE/secret-key"
    git -C "$REPO" show bcd1a2e:stacks/ct-animus/blueprints/alkion-lan.yaml > "$STATE/alkion-lan.base.yaml"
    chmod 0644 "$STATE/alkion-lan.base.yaml"
    auth_env
    n=$(name)
    docker network create --internal "$n" >/dev/null
    printf 'POSTGRES_PASSWORD=%s\nPOSTGRES_USER=authentik\nPOSTGRES_DB=authentik\n' "$(cat "$STATE/pg-password")" > "$STATE/pg.env"
    docker run -d --name "$n-pg" --network "$n" --network-alias t4-pg --log-driver none \
      --env-file "$STATE/pg.env" "$PG_IMAGE" >/dev/null
    for _ in $(seq 60); do docker exec "$n-pg" pg_isready -U authentik -d authentik >/dev/null 2>&1 && break; sleep 2; done
    common=(--network "$n" --user 1000:1000 --log-driver "$LOG_DRIVER" --env-file "$STATE/auth.env" --shm-size 512m
            -v "$HERE:/t4:ro" -v "$STACK/blueprints:/t4-blueprints:ro" -v "$STACK/rollback:/t4-rollback:ro")
    docker run -d --name "$n-worker" "${common[@]}" -v "$STATE:/state" "$AUTH_IMAGE" worker >/dev/null
    docker run -d --name "$n-server" --network-alias auth-server "${common[@]}" "$AUTH_IMAGE" server >/dev/null
    for _ in $(seq 120); do
      [[ "$(seed ready)" == '{"ready": true}' ]] && docker exec "$n-server" ak healthcheck >/dev/null 2>&1 && { echo '{"up": true}'; exit 0; }
      sleep 5
    done
    echo '{"up": false}'; exit 1 ;;
  seed) seed "${2:?phase}" ;;
  actor)
    shift
    docker run --rm --network "$(name)" --user 1000:1000 --log-driver none --entrypoint python \
      -v "$HERE:/t4:ro" -v "$STATE/users.json:/state/users.json:ro" "$AUTH_IMAGE" -I /t4/actor.py "$@" \
      | sed -n 's/^T4ACTOR //p' ;;
  campaign)
    out="$STATE/results"; mkdir -m 700 "$out"
    "$0" up > "$out/00-up.json"
    i=0
    for step in "seed base" "seed users" "actor baseline" "seed forward" "seed forward" "seed rollback" \
                "seed rollback" "seed forward"; do
      i=$((i + 1)); "$0" $step > "$out/$(printf %02d $i)-${step// /-}.json"
    done
    for set in slow-ceremony slow-stale slow-other-stale; do "$0" actor "$set" > "$out/20-$set.json" & done
    "$0" actor main negatives > "$out/21-main-negatives.json"
    wait
    "$0" seed rollback > "$out/30-seed-rollback.json"
    "$0" actor rollback > "$out/31-actor-rollback.json"
    "$0" seed forward > "$out/32-seed-forward.json"
    "$0" actor reforward > "$out/33-actor-reforward.json"
    "$0" seed inventory > "$out/34-inventory.json"
    echo '{"campaign": "done"}' ;;
  down)
    n=$(name)
    docker rm -f -v "$n-server" "$n-worker" "$n-pg" >/dev/null 2>&1 || true
    docker network rm "$n" >/dev/null 2>&1 || true
    rm -f "$STATE/nonce" "$STATE/pg-password" "$STATE/secret-key" "$STATE/auth.env" "$STATE/pg.env" "$STATE/users.json"
    echo '{"down": true}' ;;
  *) echo "usage: T4_STATE=dir $0 up|seed PHASE|actor SET...|down" >&2; exit 2 ;;
esac
