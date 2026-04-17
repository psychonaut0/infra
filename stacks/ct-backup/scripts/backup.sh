#!/bin/bash
# Nightly backup entrypoint (run by systemd timer or manually).
# Orchestrates pre-backup staging, restic backup, status file update, and
# Telegram alerting on failure.
set -euo pipefail

# --- Config ---
# shellcheck source=/dev/null
source /etc/restic/b2.env
# shellcheck source=/dev/null
source /etc/restic/telegram.env

STATUS_DIR=/var/lib/backup-status
LOGDIR=/var/log/backup
install -d -m 755 "$STATUS_DIR" "$LOGDIR"

TS=$(date +%Y%m%d-%H%M%S)
LOG="$LOGDIR/backup-$TS.log"
START=$(date +%s)

# --- Telegram failure trap ---
telegram_fail() {
  local exit_code=$?
  local tail_text
  tail_text=$(tail -30 "$LOG" 2>/dev/null | tr '\n' ' ' | cut -c1-1200)
  curl -s -X POST \
    --data-urlencode "chat_id=$TELEGRAM_CHAT_ID" \
    --data-urlencode "text=Homelab backup FAILED (exit $exit_code) at $(hostname).
Last output:
$tail_text" \
    "https://api.telegram.org/bot$TELEGRAM_TOKEN/sendMessage" > /dev/null || true
  exit $exit_code
}
trap telegram_fail ERR

# --- Run ---
{
  echo "=== Backup started $(date -Iseconds) ==="

  /usr/local/bin/pre-backup.sh

  echo ""
  echo "=== Running restic backup ==="
  restic backup \
    /backup-sources/ \
    /var/backup-staging/ \
    --tag nightly \
    --host ct-backup \
    --cleanup-cache

  echo ""
  echo "=== Backup complete $(date -Iseconds) ==="
} 2>&1 | tee -a "$LOG"

# --- Success: write status.json for Gatus ---
DURATION=$(( $(date +%s) - START ))
SNAPS=$(restic snapshots --json 2>/dev/null | jq 'length' 2>/dev/null || echo 0)
cat > "$STATUS_DIR/status.json" <<EOF
{
  "timestamp": "$(date -Iseconds)",
  "duration_sec": $DURATION,
  "snapshots": $SNAPS
}
EOF

echo "=== status.json updated ==="
