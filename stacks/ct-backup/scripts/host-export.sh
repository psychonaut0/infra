#!/bin/bash
# Runs on each Proxmox host (proxmoxmain, proxmoxnode) via a local systemd
# timer. Populates /var/backup-export/ with small host-level state for
# ct-backup to rsync into its own staging dir.
set -euo pipefail

EXPORT=/var/backup-export
rm -rf "$EXPORT"
install -d -m 755 "$EXPORT"

# Host-level config files
cp -a /etc/fstab               "$EXPORT/fstab"
cp -a /etc/hosts               "$EXPORT/hosts"
cp -a /etc/network/interfaces  "$EXPORT/interfaces"  2>/dev/null || true
install -d -m 700 "$EXPORT/root-ssh"
cp -a /root/.ssh/.             "$EXPORT/root-ssh/"   2>/dev/null || true

# Custom systemd units (non-package-managed top-level files only)
install -d "$EXPORT/systemd-system"
find /etc/systemd/system -maxdepth 1 -type f \( -name "*.service" -o -name "*.timer" \) \
  -exec cp -a {} "$EXPORT/systemd-system/" \;

# Root crontab
crontab -l > "$EXPORT/root-crontab" 2>/dev/null || : > "$EXPORT/root-crontab"

# Node-scoped PVE config (each node's local overrides under /etc/pve/local/)
install -d "$EXPORT/pve-local"
cp -a /etc/pve/local/. "$EXPORT/pve-local/" 2>/dev/null || true

echo "Export complete at $(date -Iseconds)"
