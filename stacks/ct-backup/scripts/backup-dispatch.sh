#!/bin/bash
# Runs on each backup target. Invoked as the forced command for ct-backup's
# SSH key. Routes incoming operations based on SSH_ORIGINAL_COMMAND.
#
# Supports:
#   - rsync in server mode, constrained via rrsync to specific paths
#   - Named subcommands: list-volumes, export-volume <name>, pg-dump-immich
#
# Host-specific allowed operations are picked up from /etc/backup-dispatch.conf.
set -euo pipefail

CMD="${SSH_ORIGINAL_COMMAND:-}"
CONF=/etc/backup-dispatch.conf

# Defaults if host-specific config is absent: deny everything
ALLOW_RSYNC_PATHS=""
ALLOW_EXPORT_VOLUMES=0
ALLOW_PG_DUMP_IMMICH=0

if [[ -r "$CONF" ]]; then
  # shellcheck disable=SC1090
  source "$CONF"
fi

# --- rsync in server mode ---
# rsync always invokes: rsync --server [flags] . <path>
if [[ "$CMD" == "rsync --server"* ]]; then
  TARGET_PATH="${CMD##* }"
  TARGET_PATH="${TARGET_PATH%/}"   # strip trailing slash for matching
  # Accept the request if the path is exactly one of the allowed roots OR a
  # subdirectory of one. Invoke rrsync with the matching root as its scope.
  for P in $ALLOW_RSYNC_PATHS; do
    if [[ "$TARGET_PATH" == "$P" || "$TARGET_PATH" == "$P"/* ]]; then
      exec rrsync -ro "$P"
    fi
  done
  echo "Rsync to $TARGET_PATH is not permitted (allowed: $ALLOW_RSYNC_PATHS)" >&2
  exit 1
fi

# --- Named subcommands ---
case "$CMD" in
  list-volumes)
    [[ "$ALLOW_EXPORT_VOLUMES" == "1" ]] || { echo "list-volumes not allowed" >&2; exit 1; }
    exec docker volume ls -q
    ;;
  "export-volume "*)
    [[ "$ALLOW_EXPORT_VOLUMES" == "1" ]] || { echo "export-volume not allowed" >&2; exit 1; }
    VOL="${CMD#export-volume }"
    [[ "$VOL" =~ ^[a-zA-Z0-9_.-]+$ ]] || { echo "bad volume name" >&2; exit 1; }
    exec docker run --rm -v "$VOL:/src:ro" alpine tar czf - -C /src .
    ;;
  pg-dump-immich)
    [[ "$ALLOW_PG_DUMP_IMMICH" == "1" ]] || { echo "pg-dump-immich not allowed" >&2; exit 1; }
    exec docker exec immich-postgres pg_dump -U postgres immich
    ;;
  *)
    echo "Command not permitted: $CMD" >&2
    exit 1
    ;;
esac
