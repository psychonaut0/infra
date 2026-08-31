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
# Use `reload`, not `restart`, here: this script is normally executed OVER an
# SSH connection (direct ssh or `pct exec ... bash provision-ct.sh` piped
# through one), and a restart tears down the very session running it. reload
# re-reads sshd_config for new connections without dropping established ones
# — exactly what a config-only change needs; nothing here requires a restart.
# Capture sshd -T's output into a variable before grepping it, rather than
# piping straight into `grep -q`: under `set -o pipefail` (on at the top of
# this script), `grep -q` exits the instant it finds its match, which sends
# SIGPIPE to the still-writing `sshd -T`; pipefail then reports the
# pipeline's status as sshd -T's SIGPIPE exit (141), not grep's 0 — so the
# check fails intermittently even when the value is already correct.
systemctl reload ssh
sshd_t_out=$(sshd -T 2>/dev/null)
if grep -qi '^passwordauthentication no$' <<< "$sshd_t_out"; then
  log "PasswordAuthentication no confirmed via sshd -T"
else
  log "sed on sshd_config did not take effect (a drop-in is overriding it); writing /etc/ssh/sshd_config.d/99-ct-dev.conf"
  echo "PasswordAuthentication no" > /etc/ssh/sshd_config.d/99-ct-dev.conf
  systemctl reload ssh
  sshd_t_out=$(sshd -T 2>/dev/null)
  if grep -qi '^passwordauthentication no$' <<< "$sshd_t_out"; then
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

# --- 6. Language toolchain ------------------------------------------------
# Versions are pinned to satisfy the monorepo's preinstall gates
# (scripts/check-node.sh and scripts/check-go.sh). Debian 13 packages are too
# old for all three, so each comes from an upstream tarball.
GO_VERSION=1.26.0
NODE_VERSION=26.7.0
BUN_VERSION=1.3.13

ARCH=$(dpkg --print-architecture)   # amd64
case "$ARCH" in
  amd64) GO_ARCH=amd64; NODE_ARCH=x64; BUN_ARCH=x64 ;;
  *) echo "unsupported arch $ARCH" >&2; exit 1 ;;
esac

if [ "$(/usr/local/go/bin/go version 2>/dev/null | awk '{print $3}')" != "go${GO_VERSION}" ]; then
  log "installing go ${GO_VERSION}"
  curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-${GO_ARCH}.tar.gz" -o /tmp/go.tgz
  rm -rf /usr/local/go && tar -C /usr/local -xzf /tmp/go.tgz && rm /tmp/go.tgz
else
  log "go ${GO_VERSION} already installed, skipping"
fi

if [ "$(/usr/local/node/bin/node --version 2>/dev/null)" != "v${NODE_VERSION}" ]; then
  log "installing node ${NODE_VERSION}"
  curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz" -o /tmp/node.txz
  rm -rf /usr/local/node && mkdir -p /usr/local/node
  tar -C /usr/local/node --strip-components=1 -xJf /tmp/node.txz && rm /tmp/node.txz
else
  log "node ${NODE_VERSION} already installed, skipping"
fi

if [ "$(/usr/local/bun/bin/bun --version 2>/dev/null)" != "$BUN_VERSION" ]; then
  log "installing bun ${BUN_VERSION}"
  curl -fsSL "https://github.com/oven-sh/bun/releases/download/bun-v${BUN_VERSION}/bun-linux-${BUN_ARCH}.zip" -o /tmp/bun.zip
  rm -rf /usr/local/bun && mkdir -p /usr/local/bun/bin
  unzip -qo /tmp/bun.zip -d /tmp/bunx
  install -m 755 "/tmp/bun-linux-${BUN_ARCH}/bun" /usr/local/bun/bin/bun 2>/dev/null \
    || install -m 755 "/tmp/bunx/bun-linux-${BUN_ARCH}/bun" /usr/local/bun/bin/bun
  rm -rf /tmp/bun.zip /tmp/bunx "/tmp/bun-linux-${BUN_ARCH}"
else
  log "bun ${BUN_VERSION} already installed, skipping"
fi

cat > /etc/profile.d/ct-dev-path.sh <<'PATHEOF'
export PATH="/usr/local/go/bin:/usr/local/node/bin:/usr/local/bun/bin:$HOME/.local/bin:$PATH"
export GOPATH="$HOME/go"
PATHEOF
chmod 644 /etc/profile.d/ct-dev-path.sh

# profile.d only sources for login shells. Non-login interactive shells
# (e.g. plain `ssh ct-dev command`, or a non-login bash) need it too, so
# also drop a symlink into /etc/bash.bashrc.d equivalent: source it from
# /etc/bash.bashrc for non-login bash, since Debian's /etc/bash.bashrc
# already runs for every interactive non-login shell.
if ! grep -qF '/etc/profile.d/ct-dev-path.sh' /etc/bash.bashrc 2>/dev/null; then
  log "wiring ct-dev-path.sh into /etc/bash.bashrc for non-login shells"
  printf '\n# ct-dev: language toolchain PATH (see /etc/profile.d/ct-dev-path.sh)\nif [ -f /etc/profile.d/ct-dev-path.sh ]; then\n  . /etc/profile.d/ct-dev-path.sh\nfi\n' >> /etc/bash.bashrc
else
  log "ct-dev-path.sh already wired into /etc/bash.bashrc, skipping"
fi
