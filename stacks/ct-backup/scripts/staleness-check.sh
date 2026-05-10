#!/bin/bash
# Runs every 15min on ct-backup. Marks status.json stale (timestamp="")
# when the last successful backup is older than MAX_AGE_SEC. Gatus's
# existing condition `[BODY].timestamp != ""` then fires an alert.
#
# Idempotent: if status.json is already marked stale or missing
# timestamp_unix, exits without changes. The next successful backup.sh
# run rewrites status.json from scratch, clearing the stale flag.
set -euo pipefail

STATUS=/var/lib/backup-status/status.json
MAX_AGE_SEC=$((36 * 3600))  # 36h — covers nightly run + ~12h grace

[[ -f "$STATUS" ]] || exit 0

TS_UNIX=$(jq -r '.timestamp_unix // 0' "$STATUS" 2>/dev/null || echo 0)
[[ "$TS_UNIX" =~ ^[0-9]+$ ]] || exit 0
(( TS_UNIX > 0 )) || exit 0

NOW=$(date +%s)
AGE=$(( NOW - TS_UNIX ))
(( AGE > MAX_AGE_SEC )) || exit 0

# Already marked stale?
CURRENT_TS=$(jq -r '.timestamp // ""' "$STATUS" 2>/dev/null || echo "")
[[ -z "$CURRENT_TS" ]] && exit 0

TMP=$(mktemp)
jq --arg age "$AGE" '. + {stale: true, age_sec: ($age|tonumber)} | .timestamp = ""' \
  "$STATUS" > "$TMP"
mv "$TMP" "$STATUS"
chmod 644 "$STATUS"
echo "[$(date -Iseconds)] marked status.json stale (age=${AGE}s > ${MAX_AGE_SEC}s)" >&2
