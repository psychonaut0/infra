#!/bin/bash
# Provisions the inside of ct-dev. Idempotent: safe to re-run.
# Usage: run INSIDE ct-dev as root.
set -euo pipefail

USER_NAME=psy
USER_UID=1000
REPO_PARENT="/home/${USER_NAME}/Documents/work/travelware"

log() { echo "[ct-dev] $*" >&2; }
as_user() { runuser -u "$USER_NAME" -- "$@"; }

export DEBIAN_FRONTEND=noninteractive

# --- 1. Base packages -----------------------------------------------------
apt-get update -qq
apt-get install -y -qq \
  ca-certificates curl wget gnupg git git-lfs jq ripgrep unzip zip \
  build-essential pkg-config sudo tmux locales less python3 python3-venv pipx \
  openssh-server

# --- 2. Locale + timezone -------------------------------------------------
sed -i 's/^# *en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen
locale-gen >/dev/null
timedatectl set-timezone Europe/Rome 2>/dev/null || ln -sf /usr/share/zoneinfo/Europe/Rome /etc/localtime

# --- 3. User --------------------------------------------------------------
# UID 1000 and the exact home path are load-bearing: the git includeIf work
# identity and Claude Code's project-directory encoding both key off the path.
if ! id "$USER_NAME" >/dev/null 2>&1; then
  log "creating user $USER_NAME"
  useradd -m -u "$USER_UID" -s /bin/bash "$USER_NAME"
fi
install -d -m 755 -o "$USER_NAME" -g "$USER_NAME" "$REPO_PARENT"

# Validate the generated sudoers content in a scratch file before installing
# it: a syntax error in a live sudoers.d file breaks sudo for the whole
# container, and this script is the disaster-recovery artifact a later edit
# to this section could otherwise poison silently.
sudoers_tmp=$(mktemp)
printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$USER_NAME" > "$sudoers_tmp"
if ! visudo -c -f "$sudoers_tmp" >/dev/null; then
  log "FAIL: generated sudoers content is invalid"
  rm -f "$sudoers_tmp"
  exit 1
fi
install -m 440 -o root -g root "$sudoers_tmp" /etc/sudoers.d/90-"$USER_NAME"
rm -f "$sudoers_tmp"

# --- 4. SSH ---------------------------------------------------------------
install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "/home/$USER_NAME/.ssh"
if [ -f /root/ct-dev-authorized_keys ]; then
  install -m 600 -o "$USER_NAME" -g "$USER_NAME" \
    /root/ct-dev-authorized_keys "/home/$USER_NAME/.ssh/authorized_keys"
fi

# Guard: never restart sshd with password auth still enabled unless the key
# is actually in place with correct ownership/mode — losing authorized_keys
# here would be a lockout (pct exec from proxmoxmain remains unaffected, but
# direct SSH would die).
if [ ! -s "/home/$USER_NAME/.ssh/authorized_keys" ]; then
  log "ERROR: /home/$USER_NAME/.ssh/authorized_keys is missing or empty; refusing to disable password auth"
  exit 1
fi

sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config

# Debian 13 sshd_config typically has `Include /etc/ssh/sshd_config.d/*.conf`
# near the top, and a drop-in there can silently override the sed above.
# Verify the EFFECTIVE value rather than trusting the sed; if a drop-in wins,
# write our own drop-in instead so it sorts after the others (99- prefix).
systemctl restart ssh
if sshd -T 2>/dev/null | grep -qi '^passwordauthentication no$'; then
  log "PasswordAuthentication no confirmed via sshd -T"
else
  log "sed on sshd_config did not take effect (a drop-in is overriding it); writing /etc/ssh/sshd_config.d/99-ct-dev.conf"
  echo "PasswordAuthentication no" > /etc/ssh/sshd_config.d/99-ct-dev.conf
  systemctl restart ssh
  if sshd -T 2>/dev/null | grep -qi '^passwordauthentication no$'; then
    log "PasswordAuthentication no confirmed via sshd -T (drop-in)"
  else
    log "ERROR: PasswordAuthentication still enabled after drop-in; refusing to proceed silently"
    exit 1
  fi
fi

# --- 5. Tailscale ---------------------------------------------------------
# Requires the /dev/net/tun passthrough configured by provision-host.sh.
# NOTE: ct-dev sits on 192.168.3.0/24, which Main-Gateway advertises. Do NOT
# use --accept-routes here: accepting a route for your own subnet black-holes
# local traffic (the same trap documented for BLVCKFlow).
if ! command -v tailscale >/dev/null 2>&1; then
  log "installing tailscale"
  curl -fsSL https://tailscale.com/install.sh | sh
fi
systemctl enable --now tailscaled
