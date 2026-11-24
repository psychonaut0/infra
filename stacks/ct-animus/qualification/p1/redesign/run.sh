#!/usr/bin/env bash
# Isolated synthetic qualification of the 2026-11-23 P1 sign-in redesign: the deployed
# blueprint (../../../blueprints/alkion-lan.yaml), its rollback
# (../../../rollback/alkion-lan-20261123-rollback.yaml) and the read-only drift check
# (../../../ops/check-alkion-lan-drift). Derived from ../flow-simplify/run.sh.
#
# Everything runs on one fresh --internal Docker network: no published port,
# no egress, no shared volume, no Docker socket and no contact with any real
# instance. Users, passwords and TOTP keys are synthetic and generated per run
# into the private state directory, which is the only writable host mount.
#
#   T4_STATE=/private/dir ./run.sh up
#   T4_STATE=... ./run.sh install base|forward|rollback   # hidden file + rename, as on CT117
#   T4_STATE=... ./run.sh apply base|forward|rollback     # install, then the explicit Apply task
#   T4_STATE=... ./run.sh seed PHASE                      # see seed.py
#   T4_STATE=... ./run.sh drift base|forward|rollback     # the drift check against that file
#   T4_STATE=... ./run.sh actor SET [SET ...]             # SETS: see actor.py SCENARIOS
#   T4_STATE=... ./run.sh parked SET COMMAND...           # SET parks on /signal/go around COMMAND
#   T4_STATE=... ./run.sh down
#   T4_STATE=... ./run.sh campaign   # the full recorded sequence, results in $T4_STATE/results
#
# The worker mounts $T4_STATE/bp read-only at /blueprints/ct-animus, the CT117 path, so
# the instance, its context, the watcher and the explicit Apply are Authentik's own.
# `up` copies the blueprint deployed before the redesign (git c693d82) as "base".
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
DRIFT="$STACK/ops/check-alkion-lan-drift"

name() { cat "$STATE/nonce"; }

source_file() {
  case "$1" in
    base) echo "$STATE/alkion-lan.base.yaml" ;;
    forward) echo "$STACK/blueprints/alkion-lan.yaml" ;;
    rollback) echo "$STACK/rollback/alkion-lan-20261123-rollback.yaml" ;;
    *) echo "unknown blueprint $1" >&2; exit 2 ;;
  esac
}

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

provenance() {
  # Exact inputs of this run: commit, clean tree, and the blueprint, rollback, drift and harness files.
  python3 - "$REPO" "$STACK" "$AUTH_IMAGE_ID" <<'PY'
import hashlib, json, subprocess, sys
repo, stack, image = sys.argv[1:4]
git = lambda *args: subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout.strip()
digest = lambda path: hashlib.sha256(open(stack + "/" + path, "rb").read()).hexdigest()
files = {"blueprint_sha256": "blueprints/alkion-lan.yaml", "rollback_sha256": "rollback/alkion-lan-20261123-rollback.yaml",
         "drift_sha256": "ops/check-alkion-lan-drift", "actor_sha256": "qualification/p1/redesign/actor.py",
         "seed_sha256": "qualification/p1/redesign/seed.py", "run_sha256": "qualification/p1/redesign/run.sh"}
print(json.dumps({"up": True, "commit": git("rev-parse", "HEAD"),
                  "tree_clean": git("status", "--porcelain", "--", stack) == "", "auth_image_id": image,
                  **{key: digest(path) for key, path in files.items()}}, sort_keys=True))
PY
}

seed() {
  docker exec -e T4_PHASE="$1" "$(name)-worker" ak shell -c "exec(open('/t4/seed.py').read())" 2>/dev/null \
    | sed -n 's/^T4RESULT //p'
}

install_blueprint() {
  # The CT117 install path (spec decision 8): a hidden file in the same directory with
  # the live file's mode, checked as read by the worker, then renamed into place. The
  # watcher sees created/modified events only, so the rename itself applies nothing.
  local src bp live mode host worker
  src=$(source_file "$1"); bp="$STATE/bp"; live="$bp/alkion-lan.yaml"
  mode=644; [[ -f "$live" ]] && mode=$(stat -c %a "$live")
  install -m "$mode" "$src" "$bp/.alkion-lan.yaml.new"
  host=$(sha512sum < "$bp/.alkion-lan.yaml.new" | cut -d' ' -f1)
  worker=$(docker exec "$(name)-worker" sha512sum /blueprints/ct-animus/.alkion-lan.yaml.new | cut -d' ' -f1)
  mv -f "$bp/.alkion-lan.yaml.new" "$live"
  printf '{"installed": "%s", "mode": "%s", "worker_reads_same_bytes": %s, "only_file": %s}\n' "$1" "$(stat -c %a "$live")" \
    "$([[ "$host" == "$worker" ]] && echo true || echo false)" \
    "$([[ "$(ls -A "$bp")" == alkion-lan.yaml ]] && echo true || echo false)"
}

drift() {
  # Read-only drift check of the live instance against one blueprint file; one JSON line.
  local file output status
  file=$(source_file "$1")
  set +e
  output=$(python3 -I "$DRIFT" --db-container "$(name)-pg" --blueprint "$file" --json)
  status=$?
  set -e
  python3 -c 'import json, sys; d = json.loads(sys.argv[1] or "{}"); d["exit"] = int(sys.argv[2]); d["against"] = sys.argv[3]; print(json.dumps(d, sort_keys=True))' \
    "$output" "$status" "$1"
}

case "${1:-}" in
  up)
    [[ ! -e "$STATE/nonce" ]] || { echo "state already in use" >&2; exit 2; }
    [[ "$(docker image inspect --format '{{.Id}} {{.Config.User}}' "$AUTH_IMAGE")" == "$AUTH_IMAGE_ID 1000" ]] \
      || { echo "auth image identity mismatch" >&2; exit 2; }
    docker image inspect "$PG_IMAGE" >/dev/null
    echo "p1g-$(python3 -c 'import secrets; print(secrets.token_hex(4))')" > "$STATE/nonce"
    python3 -c 'import secrets; print(secrets.token_hex(32))' > "$STATE/pg-password"
    python3 -c 'import secrets; print(secrets.token_urlsafe(64))' > "$STATE/secret-key"
    git -C "$REPO" show c693d82:stacks/ct-animus/blueprints/alkion-lan.yaml > "$STATE/alkion-lan.base.yaml"
    chmod 0644 "$STATE/alkion-lan.base.yaml"
    install -d -m 0755 "$STATE/bp" "$STATE/signal"
    auth_env
    n=$(name)
    docker network create --internal "$n" >/dev/null
    printf 'POSTGRES_PASSWORD=%s\nPOSTGRES_USER=authentik\nPOSTGRES_DB=authentik\n' "$(cat "$STATE/pg-password")" > "$STATE/pg.env"
    docker run -d --name "$n-pg" --network "$n" --network-alias t4-pg --log-driver none \
      --env-file "$STATE/pg.env" "$PG_IMAGE" >/dev/null
    for _ in $(seq 60); do docker exec "$n-pg" pg_isready -U authentik -d authentik >/dev/null 2>&1 && break; sleep 2; done
    common=(--network "$n" --user 1000:1000 --log-driver "$LOG_DRIVER" --env-file "$STATE/auth.env" --shm-size 512m
            -v "$HERE:/t4:ro")
    docker run -d --name "$n-worker" "${common[@]}" -v "$STATE:/state" -v "$STATE/bp:/blueprints/ct-animus:ro" "$AUTH_IMAGE" worker >/dev/null
    docker run -d --name "$n-server" --network-alias auth-server "${common[@]}" "$AUTH_IMAGE" server >/dev/null
    for _ in $(seq 120); do
      [[ "$(seed ready)" == '{"ready": true}' ]] && docker exec "$n-server" ak healthcheck >/dev/null 2>&1 && { provenance; exit 0; }
      sleep 5
    done
    echo '{"up": false}'; exit 1 ;;
  install) install_blueprint "${2:?blueprint}" ;;
  apply) install_blueprint "${2:?blueprint}" > "$STATE/install.json"; seed apply | python3 -c \
    'import json, sys; d = json.loads(sys.stdin.read() or "{}"); d["install"] = json.load(open(sys.argv[1])); print(json.dumps(d, sort_keys=True))' \
    "$STATE/install.json"; rm -f "$STATE/install.json" ;;
  seed) seed "${2:?phase}" ;;
  drift) drift "${2:?blueprint}" ;;
  actor)
    shift
    inputs=(-v "$STATE/users.json:/state/users.json:ro" -v "$STATE/signal:/signal:ro")
    for f in forged-login forged-full; do if [[ -f "$STATE/$f" ]]; then inputs+=(-v "$STATE/$f:/state/$f:ro"); fi; done
    docker run --rm --network "$(name)" --user 1000:1000 --log-driver none --entrypoint python \
      -v "$HERE:/t4:ro" "${inputs[@]}" "$AUTH_IMAGE" -I /t4/actor.py "$@" \
      | sed -n 's/^T4ACTOR //p' ;;
  parked)
    # One actor set parks mid-ceremony until /signal/go exists; meanwhile one command runs.
    set_name=${2:?set}; shift 2
    rm -f "$STATE/signal/go"
    "$0" actor "$set_name" > "$STATE/parked.json" &
    pid=$!
    sleep 90  # long enough to reach the parking point, including one TOTP time step
    "$@" > "$STATE/parked-action.json"
    touch "$STATE/signal/go"
    wait "$pid"
    python3 -c 'import json, sys; print(json.dumps({"action": json.loads(open(sys.argv[1]).read() or "{}"), "actor": json.loads(open(sys.argv[2]).read() or "{}")}, sort_keys=True))' \
      "$STATE/parked-action.json" "$STATE/parked.json"
    rm -f "$STATE/parked.json" "$STATE/parked-action.json" "$STATE/signal/go" ;;
  campaign)
    out="$STATE/results"; mkdir -m 700 "$out"
    "$0" up > "$out/00-up.json"
    i=0
    # The deployed state first, exactly as on CT117: instance discovered with an empty
    # context, then the owner-commissioning context, then the drift baseline.
    for step in "install base" "seed instance" "seed users" "actor deployed" "drift base" "seed inventory" \
                "install forward" "seed rename-probe" "seed apply" "apply forward" "drift forward" "seed inventory"; do
      i=$((i + 1)); "$0" $step > "$out/$(printf %02d $i)-${step// /-}.json"
    done
    for set in slow-password slow-after-totp slow-on-page slow-stale slow-other-stale; do
      "$0" actor "$set" > "$out/20-$set.json" &
    done
    "$0" actor main > "$out/21-main.json"
    "$0" actor negatives > "$out/22-negatives.json"
    "$0" seed forge:login > "$out/23-forge-login.json"
    "$0" seed forge:full > "$out/24-forge-full.json"
    "$0" actor forged > "$out/25-actor-forged.json"
    "$0" actor single-use > "$out/26-single-use.json"
    "$0" actor refresh > "$out/27-refresh.json"
    "$0" seed redirector > "$out/28-seed-redirector.json"
    "$0" actor redirected > "$out/29-redirected.json"
    "$0" parked deactivate-mid "$0" seed deactivate:p1g-deact,p1g-deact-b > "$out/30-deactivated-mid-ceremony.json"
    wait
    # User switching: point the brand at the Alkion flow (a misconfiguration the drift
    # check must flag), run the switch, restore and check again.
    "$0" seed brand:switch-add > "$out/31-seed-brand-switch-add.json"
    "$0" drift forward > "$out/32-drift-brand-switch-add.json"
    "$0" actor user-switch-add > "$out/33-user-switch-add.json"
    "$0" seed brand:switch-target > "$out/34-seed-brand-switch-target.json"
    "$0" drift forward > "$out/35-drift-brand-switch-target.json"
    "$0" actor user-switch-target > "$out/36-user-switch-target.json"
    "$0" seed brand:restore > "$out/37-seed-brand-restore.json"
    "$0" drift forward > "$out/38-drift-brand-restored.json"
    n=40
    for fault in inject:animus-lan-entry-gate inject:animus-lan-login-gate inject:animus-lan-denied-notice \
                 inject:animus-lan-application inject:animus-lan-pkce negate:animus-lan-login-gate \
                 negate:animus-lan-entry-gate; do
      tag=${fault//:/-}
      "$0" seed "$fault" > "$out/$n-seed-$tag.json"
      "$0" actor "fault-${fault%%:*}-${fault#*:animus-lan-}" > "$out/$((n + 1))-actor-$tag.json"
      "$0" drift forward > "$out/$((n + 2))-drift-$tag.json"
      "$0" seed apply > "$out/$((n + 3))-restore-$tag.json"
      n=$((n + 4))
    done
    # Recorded residual cases (spec decision 6): each must be flagged by the drift check.
    n=70
    for residual in entry-disabled login-disabled login-deleted login-reeval-off login-any-extra \
                    application-disabled application-deleted application-any; do
      "$0" seed "residual:$residual" > "$out/$n-seed-residual-$residual.json"
      "$0" actor "residual-$residual" > "$out/$((n + 1))-actor-residual-$residual.json"
      "$0" drift forward > "$out/$((n + 2))-drift-residual-$residual.json"
      "$0" seed "unresidual:$residual" > "$out/$((n + 3))-restore-residual-$residual.json"
      "$0" drift forward > "$out/$((n + 4))-drift-restored-$residual.json"
      n=$((n + 5))
    done
    "$0" actor restored > "$out/110-actor-restored.json"
    "$0" seed sessions > "$out/111-sessions.json"
    # Rollback chain (B5), with a ceremony open across each apply.
    "$0" parked across-rollback "$0" apply rollback > "$out/120-across-rollback.json"
    "$0" apply rollback > "$out/121-apply-rollback-again.json"
    "$0" drift rollback > "$out/122-drift-rollback.json"
    # Exactly the deployed state: against the c693d82 file the only expected difference
    # is the file hash (the instance last applied the rollback file).
    "$0" drift base > "$out/122b-drift-rollback-against-base.json"
    "$0" seed inventory > "$out/123-inventory-rollback.json"
    "$0" actor rollback > "$out/124-actor-rollback.json"
    "$0" parked across-apply "$0" apply forward > "$out/125-across-apply.json"
    "$0" apply forward > "$out/126-apply-forward-again.json"
    "$0" drift forward > "$out/127-drift-reforward.json"
    "$0" actor reforward > "$out/128-actor-reforward.json"
    "$0" seed inventory > "$out/129-inventory-reforward.json"
    echo '{"campaign": "done"}' ;;
  down)
    n=$(name)
    docker rm -f -v "$n-server" "$n-worker" "$n-pg" >/dev/null 2>&1 || true
    docker network rm "$n" >/dev/null 2>&1 || true
    rm -f "$STATE/nonce" "$STATE/pg-password" "$STATE/secret-key" "$STATE/auth.env" "$STATE/pg.env" "$STATE/users.json" "$STATE/forged-login" "$STATE/forged-full"
    echo '{"down": true}' ;;
  *) echo "usage: T4_STATE=dir $0 up|install|apply|seed|drift|actor|parked|campaign|down" >&2; exit 2 ;;
esac
