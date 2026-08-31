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

# The toolchain dirs are derived once and reused for every PATH sink below,
# so there is exactly one place to edit if a tool moves.
CT_DEV_PATH_DIRS="/usr/local/go/bin:/usr/local/node/bin:/usr/local/bun/bin"
CT_DEV_GOPATH="/home/${USER_NAME}/go"

# --- 6a. Login/interactive shells: /etc/profile.d ------------------------
cat > /etc/profile.d/ct-dev-path.sh <<PATHEOF
export PATH="${CT_DEV_PATH_DIRS}:\$HOME/.local/bin:\$PATH"
export GOPATH="\$HOME/go"
PATHEOF
chmod 644 /etc/profile.d/ct-dev-path.sh

# profile.d only sources for login shells. Non-login *interactive* shells
# (e.g. `ssh -t ct-dev`, or an interactive `bash` with no `-l`) need it too,
# so also source it from /etc/bash.bashrc, which Debian bash reads for
# every interactive non-login shell.
if ! grep -qF '/etc/profile.d/ct-dev-path.sh' /etc/bash.bashrc 2>/dev/null; then
  log "wiring ct-dev-path.sh into /etc/bash.bashrc for non-login shells"
  printf '\n# ct-dev: language toolchain PATH (see /etc/profile.d/ct-dev-path.sh)\nif [ -f /etc/profile.d/ct-dev-path.sh ]; then\n  . /etc/profile.d/ct-dev-path.sh\nfi\n' >> /etc/bash.bashrc
else
  log "ct-dev-path.sh already wired into /etc/bash.bashrc, skipping"
fi

# --- 6b. Non-interactive, non-login shells (plain `ssh host cmd`): -------
# /etc/environment is read by pam_env for every SSH session regardless of
# interactivity/login-ness. It is NOT a shell script: plain KEY=value, no
# `export`, no `$VAR` expansion, no partial fragments — so the PATH here
# must be the fully expanded literal, standard system dirs included, or a
# plain `ssh host cmd` loses the base OS PATH entirely.
CT_DEV_ETC_ENV_PATH="${CT_DEV_PATH_DIRS}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ct_dev_desired_path_line="PATH=\"${CT_DEV_ETC_ENV_PATH}\""
ct_dev_desired_gopath_line="GOPATH=\"${CT_DEV_GOPATH}\""
ct_dev_current_path_line=$(grep -m1 '^PATH=' /etc/environment 2>/dev/null || true)
ct_dev_current_gopath_line=$(grep -m1 '^GOPATH=' /etc/environment 2>/dev/null || true)

if [ "$ct_dev_current_path_line" != "$ct_dev_desired_path_line" ] || [ "$ct_dev_current_gopath_line" != "$ct_dev_desired_gopath_line" ]; then
  log "updating /etc/environment PATH/GOPATH"
  ct_dev_tmp_env=$(mktemp)
  { grep -v '^PATH=' /etc/environment 2>/dev/null | grep -v '^GOPATH=' || true; } > "$ct_dev_tmp_env"
  printf '%s\n%s\n' "$ct_dev_desired_path_line" "$ct_dev_desired_gopath_line" >> "$ct_dev_tmp_env"
  # Sanity-check before replacing a file PAM reads on every login: refuse to
  # install anything that doesn't contain our toolchain PATH verbatim.
  ct_dev_check=$(grep -F "$ct_dev_desired_path_line" "$ct_dev_tmp_env" || true)
  if [ -n "$ct_dev_check" ]; then
    install -m 644 "$ct_dev_tmp_env" /etc/environment
  else
    echo "ERROR: refusing to write malformed /etc/environment" >&2
    rm -f "$ct_dev_tmp_env"
    exit 1
  fi
  rm -f "$ct_dev_tmp_env"
else
  log "/etc/environment PATH/GOPATH already up to date, skipping"
fi

# --- 6c. systemd --user manager (dev-task's headless units): -------------
# environment.d is read by the systemd USER manager (per-user, not PAM), so
# it's what a `systemd-run --user` / user unit sees — profile.d and
# /etc/environment do not reach that context.
#
# `systemd-run --user` talks to the per-user D-Bus session bus, which on
# Debian is provided by the separate dbus-user-session package (not pulled
# in by base `dbus`) — without it there is no /run/user/<uid>/bus socket
# and `systemd-run --user` fails with "Failed to connect to user scope bus"
# regardless of environment.d/lingering being correct. `apt-get install` of
# an already-installed package is a no-op, so this is naturally idempotent.
apt-get install -y -qq dbus-user-session
#
# Without lingering, that per-user manager only exists while a login
# session for the user is open, and dies with the last session — so a
# headless dev-task run with nobody logged in would have no user manager
# to talk to at all, regardless of environment.d. Enabling linger makes
# the user manager start at boot and persist with no active session,
# which is required for the "systemd --user unit runs headless" capability
# this task exists to support. `loginctl enable-linger` is itself
# idempotent (writes a marker file, no error/duplicate on re-run); the
# guard below only exists to keep the log quiet on a re-run.
if [ "$(loginctl show-user "$USER_NAME" -p Linger --value 2>/dev/null)" != "yes" ]; then
  log "enabling lingering for ${USER_NAME} (persistent systemd --user manager for headless units)"
  loginctl enable-linger "$USER_NAME"
else
  log "lingering already enabled for ${USER_NAME}, skipping"
fi

CT_DEV_USER_ENV_DIR="/home/${USER_NAME}/.config/environment.d"
CT_DEV_USER_ENV_FILE="${CT_DEV_USER_ENV_DIR}/10-ct-dev.conf"
ct_dev_desired_user_env=$(printf 'PATH=%s:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:%s/.local/bin\nGOPATH=%s\n' \
  "$CT_DEV_PATH_DIRS" "/home/${USER_NAME}" "$CT_DEV_GOPATH")

mkdir -p "$CT_DEV_USER_ENV_DIR"
chown "${USER_NAME}:${USER_NAME}" "/home/${USER_NAME}/.config" "$CT_DEV_USER_ENV_DIR" 2>/dev/null || true

ct_dev_current_user_env=""
[ -f "$CT_DEV_USER_ENV_FILE" ] && ct_dev_current_user_env=$(cat "$CT_DEV_USER_ENV_FILE")

if [ "$ct_dev_current_user_env" != "$ct_dev_desired_user_env" ]; then
  log "writing ${CT_DEV_USER_ENV_FILE} for systemd --user"
  printf '%s' "$ct_dev_desired_user_env" > "$CT_DEV_USER_ENV_FILE"
  chown "${USER_NAME}:${USER_NAME}" "$CT_DEV_USER_ENV_FILE"
  chmod 644 "$CT_DEV_USER_ENV_FILE"
  # Apply immediately (rather than waiting for next login) so this run's
  # own verification step can see it take effect.
  if runuser -u "$USER_NAME" -- bash -c 'XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user daemon-reexec' 2>/dev/null; then
    log "reloaded systemd --user for ${USER_NAME}"
  else
    log "could not reload systemd --user for ${USER_NAME} (no active user session yet) — will take effect at next login"
  fi
else
  log "${CT_DEV_USER_ENV_FILE} already up to date, skipping"
fi
