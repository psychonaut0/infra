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
ALLOW_SQLITE_DUMP=0
# SQLITE_DB_<name>=/abs/path declared in /etc/backup-dispatch.conf per host.

if [[ -r "$CONF" ]]; then
  # shellcheck disable=SC1090
  source "$CONF"
fi

# --- rsync in server mode ---
# rsync invokes: "rsync --server [flags] . <path>". rrsync expects paths to
# be *relative* to its restricted root, so we translate client-sent absolute
# paths into relative ones, then rewrite SSH_ORIGINAL_COMMAND so rrsync sees
# the translated form.
if [[ "$CMD" == "rsync --server"* ]]; then
  TARGET_PATH="${CMD##* }"
  MATCH_PATH="${TARGET_PATH%/}"   # strip trailing slash for matching
  for P in $ALLOW_RSYNC_PATHS; do
    if [[ "$MATCH_PATH" == "$P" || "$MATCH_PATH" == "$P"/* ]]; then
      # Compute path relative to the matched root
      REL="${TARGET_PATH#$P}"
      REL="${REL#/}"
      [[ -z "$REL" ]] && REL="."
      # Rebuild the command with the translated path as the last arg
      BASE_CMD="${CMD% *}"
      export SSH_ORIGINAL_COMMAND="$BASE_CMD $REL"
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
    exec docker exec immich_postgres pg_dump -U postgres immich
    ;;
  "sqlite-dump "*)
    # sqlite-dump <name>: looks up SQLITE_DB_<NAME> in /etc/backup-dispatch.conf,
    # runs sqlite3 .backup (online backup API — safe against live writers) into
    # a temp file, then streams gzipped to stdout.
    [[ "$ALLOW_SQLITE_DUMP" == "1" ]] || { echo "sqlite-dump not allowed" >&2; exit 1; }
    NAME="${CMD#sqlite-dump }"
    [[ "$NAME" =~ ^[A-Za-z0-9_]+$ ]] || { echo "bad sqlite-dump name" >&2; exit 1; }
    UPNAME="${NAME^^}"
    VAR="SQLITE_DB_${UPNAME}"
    DBPATH="${!VAR:-}"
    [[ -n "$DBPATH" ]] || { echo "no DB configured for $NAME" >&2; exit 1; }
    [[ -f "$DBPATH" ]] || { echo "sqlite db not found: $DBPATH" >&2; exit 1; }
    command -v sqlite3 >/dev/null || { echo "sqlite3 not installed" >&2; exit 1; }
    TMPDB=$(mktemp --suffix=.db)
    trap 'rm -f "$TMPDB"' EXIT
    sqlite3 "$DBPATH" ".backup '$TMPDB'" >&2
    exec gzip -c "$TMPDB"
    ;;
  *)
    echo "Command not permitted: $CMD" >&2
    exit 1
    ;;
esac
