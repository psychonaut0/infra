# Off-site Backup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-ready off-site backup system for the homelab: a `ct-backup` LXC on proxmoxmain that runs restic against Backblaze B2, covering all infrastructure secrets and state per the design spec, with automated scheduling, monitoring, and a verified disaster recovery path.

**Architecture:** Three-layer model — git holds structure, restic holds secrets + state, B2 is the off-site target. A single unprivileged LXC (`ct-backup`, VMID 109, 192.168.3.13) runs a nightly cron that rsyncs small state from every CT and both Proxmox hosts into a local staging dir, then restic ships everything to B2 via the S3-compatible endpoint. Monitoring uses Telegram for immediate failure alerts and Gatus for staleness detection.

**Tech Stack:** Debian 13 LXC, restic, rsync, ssh, cron/systemd timers, Caddy (for status endpoint), Backblaze B2 (S3-compatible). Orchestration via plain shell scripts.

**Reference:** Design spec at `docs/superpowers/specs/2026-04-17-offsite-backup-design.md`.

---

## Task 1: Audit per-CT non-Docker state

**Goal:** Verify the "full-restic / no PBS" design by confirming every CT's important state lives in Docker volumes or bind mounts, not in the CT rootfs outside Docker. Document anything found that needs special handling.

**Files:**
- Modify: `docs/superpowers/specs/2026-04-17-offsite-backup-design.md` (append findings to "What this design does NOT protect" section if anything surfaces)

- [ ] **Step 1: For each CT, list manually-installed apt packages beyond the standard pattern**

Run from blvckmain:
```bash
for CT in ct-dns ct-tunnel ct-nvr ct-media ct-photos ct-files ct-mgmt; do
  echo "=== $CT ==="
  ssh root@$CT "apt-mark showmanual | grep -vxE 'apt|base-files|base-passwd|bash|bsdutils|ca-certificates|coreutils|cron|curl|dash|debconf|debian-archive-keyring|debianutils|diffutils|dpkg|e2fsprogs|findutils|gcc-.*|gpgv|grep|gzip|hostname|init-system-helpers|libc-bin|libc6|locales|login|logrotate|mawk|mount|ncurses-.*|nftables|openssh-client|openssh-server|passwd|perl-base|procps|python.*|rsyslog|sed|sensible-utils|shadow|ssh|sudo|systemd|systemd-sysv|systemd-timesyncd|sysvinit-utils|tar|tzdata|util-linux|xz-utils|docker-.*|containerd.io|vim|wget|portainer-agent'"
done
```

Expected: mostly empty output. If anything appears, note the CT and package.

- [ ] **Step 2: For each CT, check for custom systemd units**

```bash
for CT in ct-dns ct-tunnel ct-nvr ct-media ct-photos ct-files ct-mgmt; do
  echo "=== $CT ==="
  ssh root@$CT "ls /etc/systemd/system/*.service /etc/systemd/system/*.timer 2>/dev/null | grep -v '\.wants/'"
done
```

Expected: empty or only Docker-related units. Flag any custom units.

- [ ] **Step 3: For each CT, check `/root/` for anything stateful**

```bash
for CT in ct-dns ct-tunnel ct-nvr ct-media ct-photos ct-files ct-mgmt; do
  echo "=== $CT ==="
  ssh root@$CT "ls -la /root/ | grep -v '^d\|^total\|\.cache\|\.bash\|\.profile\|\.ssh\|\.viminfo'"
done
```

Expected: no stray files. Flag if anything is found (custom scripts, config, etc.).

- [ ] **Step 4: Record findings**

If any surprises are found, append them to the spec's "What this design does NOT protect" section under a new sub-bullet. If everything is clean, add a line to the spec confirming: "Audit performed YYYY-MM-DD — no non-Docker state found on any CT."

- [ ] **Step 5: Commit spec update**

```bash
cd /home/psy/Documents/personal/ops/infra
git add docs/superpowers/specs/2026-04-17-offsite-backup-design.md
git commit -m "Record CT audit findings in backup design spec"
```

---

## Task 2: Create Backblaze B2 bucket and S3-compatible credentials

**Goal:** Provision the off-site target. Capture credentials and region endpoint.

**Files:** None (external service)

- [ ] **Step 1: Create Backblaze B2 account if needed**

Sign up at https://www.backblaze.com/b2/sign-up.html. Free tier: 10 GB storage, 1 GB/day download, enough to verify setup.

- [ ] **Step 2: Create a private bucket**

Via the B2 web UI:
- Name: `blvck-homelab-backup` (bucket names are globally unique — add a suffix if taken)
- Files: Private
- Object Lock: Disabled (not needed; restic manages retention)
- Default encryption: B2 server-side encryption enabled (defense in depth; restic already encrypts client-side)

Note the bucket's **region code** (e.g., `eu-central-003`, `us-west-004`). Record it — needed for the endpoint URL.

- [ ] **Step 3: Generate an Application Key scoped to the bucket**

In B2 UI → Application Keys → Add a New Application Key:
- Name: `ct-backup-restic`
- Allow access to: `blvck-homelab-backup` only
- Type of access: Read and Write
- Allow List All Bucket Names: No
- File name prefix: (empty)
- Duration: (empty — non-expiring)

Capture the three pieces output by B2:
- `keyID` — this is the S3 `AWS_ACCESS_KEY_ID`
- `applicationKey` — this is the S3 `AWS_SECRET_ACCESS_KEY`
- `S3 Endpoint` — e.g. `s3.eu-central-003.backblazeb2.com`

**Do not paste these anywhere that might sync (shell history, git, etc.).**

- [ ] **Step 4: Verify credentials work via s3cmd or curl**

From blvckmain, use `aws-cli` or `s3cmd` for a quick sanity check:
```bash
aws --endpoint-url=https://s3.<region>.backblazeb2.com s3 ls s3://blvck-homelab-backup/
```
Expected: empty listing, exit code 0. Any error → regenerate credentials.

- [ ] **Step 5: No commit — nothing in git changes for this step**

---

## Task 3: Store disaster recovery secrets offline

**Goal:** Ensure restic repo password and B2 credentials survive total hardware loss.

**Files:** None (external action)

- [ ] **Step 1: Generate the restic repository password**

```bash
openssl rand -base64 32
```

Capture the output. This is what encrypts the restic repository.

- [ ] **Step 2: Store in 1Password / Bitwarden**

Create a new Secure Note titled "Homelab restic + B2 backup":
- Restic repo password: (from step 1)
- B2 keyID: (from Task 2 step 3)
- B2 applicationKey: (from Task 2 step 3)
- Bucket: `blvck-homelab-backup`
- Endpoint: `s3.<region>.backblazeb2.com`
- Repository URL: `s3:s3.<region>.backblazeb2.com/blvck-homelab-backup/backup`

- [ ] **Step 3: Print a paper copy for fireproof storage**

Print the vault entry on paper. Store in the fireproof location (home safe, bank deposit box, or designated family member). This is the ultimate fallback if the password manager is ever inaccessible.

- [ ] **Step 4: No commit — no git state changes**

---

## Task 4: Provision ct-backup LXC on proxmoxmain

**Goal:** Create an unprivileged LXC matching the established CT pattern.

**Files:**
- Create (on proxmoxmain): `/etc/pve/lxc/109.conf` (via `pct create`)

- [ ] **Step 1: Confirm VMID 109 is free**

```bash
ssh root@proxmoxmain 'pct list | grep -w 109 || echo "109 is free"'
```
Expected: `109 is free`

- [ ] **Step 2: Confirm IP 192.168.3.13 is free**

```bash
ping -c 2 -W 1 192.168.3.13
```
Expected: 100% packet loss / destination unreachable.

- [ ] **Step 3: Ensure Debian 13 template is present on proxmoxmain**

```bash
ssh root@proxmoxmain 'pveam list local | grep debian-13'
```
If missing:
```bash
ssh root@proxmoxmain 'pveam update && pveam download local debian-13-standard_13.0-1_amd64.tar.zst'
```

- [ ] **Step 4: Create the LXC**

```bash
ssh root@proxmoxmain 'pct create 109 local:vztmpl/debian-13-standard_13.0-1_amd64.tar.zst \
  --hostname ct-backup \
  --cores 1 \
  --memory 512 \
  --swap 256 \
  --rootfs local-lvm:4 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.3.13/24,gw=192.168.3.1 \
  --nameserver 192.168.3.5 \
  --searchdomain lan \
  --features nesting=0 \
  --unprivileged 1 \
  --onboot 1 \
  --ssh-public-keys /root/.ssh/authorized_keys \
  --start 1'
```

Expected: `Creating filesystem on /dev/pve/vm-109-disk-0 ... Creating SSH host key ... Starting CT 109 ...`

- [ ] **Step 5: Verify the CT is up and reachable**

```bash
ssh root@ct-backup 'hostnamectl'
```
Expected: `Static hostname: ct-backup`, Operating System: `Debian GNU/Linux 13 (Trixie)`.

- [ ] **Step 6: Add ct-backup to the local /etc/hosts if needed**

If `ssh root@ct-backup` didn't work in step 5 (no DNS yet for this host), add to blvckmain's `/etc/hosts` (and optionally to Pi-hole's custom-dns.list in Task 18):
```
192.168.3.13    ct-backup ct-backup.lan
```

- [ ] **Step 7: Commit nothing — CT creation is operational state, not repo content (yet)**

---

## Task 5: Install base tools on ct-backup

**Goal:** Install restic, rsync, openssh-client, cron, caddy, and basic utilities.

**Files:** None in repo (all changes inside ct-backup)

- [ ] **Step 1: Update package index and upgrade**

```bash
ssh root@ct-backup 'apt-get update && apt-get upgrade -y'
```
Expected: successful upgrade with no errors.

- [ ] **Step 2: Install required packages**

```bash
ssh root@ct-backup 'apt-get install -y \
  restic \
  rsync \
  openssh-client \
  cron \
  caddy \
  curl \
  ca-certificates \
  jq \
  postgresql-client'
```

Expected: all install cleanly. `postgresql-client` is needed for pg_dump (client-side — actual dump runs on ct-photos via docker exec but we want the local client for restore drills).

- [ ] **Step 3: Verify restic version is >= 0.16**

```bash
ssh root@ct-backup 'restic version'
```
Expected: `restic 0.16.x` or newer. Newer is better (S3 transport fixes).

- [ ] **Step 4: Verify caddy is disabled for now (we'll configure it later)**

```bash
ssh root@ct-backup 'systemctl stop caddy && systemctl disable caddy'
```

- [ ] **Step 5: Commit nothing — still operational**

---

## Task 6: Generate ct-backup's SSH key and deploy dispatcher to every target

**Goal:** Create a dedicated ed25519 keypair for ct-backup and deploy a **single dispatcher script** on each target that routes incoming SSH commands (rsync and named subcommands) based on `SSH_ORIGINAL_COMMAND`. This avoids the classic trap where multiple `authorized_keys` entries with the same key all collapse to the first entry's forced command.

**Files:**
- Create (in repo): `stacks/ct-backup/scripts/backup-dispatch.sh` — the shared dispatcher installed on every target

- [ ] **Step 1: Generate keypair on ct-backup**

```bash
ssh root@ct-backup 'ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N "" -C "ct-backup@$(hostname)"'
ssh root@ct-backup 'cat /root/.ssh/id_ed25519.pub'
```

Capture the printed public key — it's referenced as `$KEY` below.

- [ ] **Step 2: Write the shared dispatcher script in the repo**

Create `/home/psy/Documents/personal/ops/infra/stacks/ct-backup/scripts/backup-dispatch.sh`:

```bash
#!/bin/bash
# Runs on each backup target. Invoked as the forced-command for ct-backup's
# SSH key. Routes incoming operations based on SSH_ORIGINAL_COMMAND.
#
# Supports:
#   - rsync in server mode, constrained via rrsync to specific paths
#   - Named subcommands: list-volumes, export-volume <name>, pg-dump-immich
#
# Host-specific allowed paths are picked up from /etc/backup-dispatch.conf.
set -euo pipefail

CMD="${SSH_ORIGINAL_COMMAND:-}"
CONF=/etc/backup-dispatch.conf

# Defaults if host-specific config absent (no paths allowed)
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
    [[ "$ALLOW_EXPORT_VOLUMES" == "1" ]] || { echo "list-volumes not allowed"; exit 1; }
    exec docker volume ls -q
    ;;
  "export-volume "*)
    [[ "$ALLOW_EXPORT_VOLUMES" == "1" ]] || { echo "export-volume not allowed"; exit 1; }
    VOL="${CMD#export-volume }"
    [[ "$VOL" =~ ^[a-zA-Z0-9_.-]+$ ]] || { echo "bad volume name"; exit 1; }
    exec docker run --rm -v "$VOL:/src:ro" alpine tar czf - -C /src .
    ;;
  pg-dump-immich)
    [[ "$ALLOW_PG_DUMP_IMMICH" == "1" ]] || { echo "pg-dump-immich not allowed"; exit 1; }
    exec docker exec immich-postgres pg_dump -U postgres immich
    ;;
  *)
    echo "Command not permitted: $CMD" >&2
    exit 1
    ;;
esac
```

Make executable in the repo:
```bash
chmod +x /home/psy/Documents/personal/ops/infra/stacks/ct-backup/scripts/backup-dispatch.sh
```

- [ ] **Step 3: Install rrsync on every target (if not already present)**

`rrsync` ships with the `rsync` package but often as a sample script that isn't on `$PATH`. Symlink it:

```bash
for H in proxmoxmain proxmoxnode ct-dns ct-tunnel ct-nvr ct-media ct-photos ct-files ct-mgmt; do
  ssh root@$H '[[ -x /usr/bin/rrsync ]] || ln -sf /usr/share/doc/rsync/scripts/rrsync /usr/bin/rrsync; which rrsync'
done
```
Expected: each line prints `/usr/bin/rrsync`.

- [ ] **Step 4: Deploy dispatcher + per-host config to proxmoxmain**

```bash
scp /home/psy/Documents/personal/ops/infra/stacks/ct-backup/scripts/backup-dispatch.sh \
  root@proxmoxmain:/usr/local/bin/backup-dispatch.sh

ssh root@proxmoxmain 'chmod 755 /usr/local/bin/backup-dispatch.sh; \
  mkdir -p /var/backup-export; \
  cat > /etc/backup-dispatch.conf <<EOF
ALLOW_RSYNC_PATHS="/etc/pve /var/backup-export"
ALLOW_EXPORT_VOLUMES=0
ALLOW_PG_DUMP_IMMICH=0
EOF'
```

- [ ] **Step 5: Deploy dispatcher + per-host config to proxmoxnode**

```bash
scp /home/psy/Documents/personal/ops/infra/stacks/ct-backup/scripts/backup-dispatch.sh \
  root@proxmoxnode:/usr/local/bin/backup-dispatch.sh

ssh root@proxmoxnode 'chmod 755 /usr/local/bin/backup-dispatch.sh; \
  mkdir -p /var/backup-export; \
  cat > /etc/backup-dispatch.conf <<EOF
ALLOW_RSYNC_PATHS="/etc/pve /var/backup-export"
ALLOW_EXPORT_VOLUMES=0
ALLOW_PG_DUMP_IMMICH=0
EOF'
```

- [ ] **Step 6: Deploy dispatcher + config to CTs that hold named volumes (ct-dns, ct-tunnel, ct-mgmt, ct-files)**

```bash
for CT in ct-dns ct-tunnel ct-mgmt ct-files; do
  scp /home/psy/Documents/personal/ops/infra/stacks/ct-backup/scripts/backup-dispatch.sh \
    root@$CT:/usr/local/bin/backup-dispatch.sh
  ssh root@$CT 'chmod 755 /usr/local/bin/backup-dispatch.sh; \
    cat > /etc/backup-dispatch.conf <<EOF
ALLOW_RSYNC_PATHS="/opt/stacks"
ALLOW_EXPORT_VOLUMES=1
ALLOW_PG_DUMP_IMMICH=0
EOF'
done
```

- [ ] **Step 7: Deploy dispatcher + config to CTs without named volumes but with stacks (ct-nvr, ct-media)**

```bash
for CT in ct-nvr ct-media; do
  scp /home/psy/Documents/personal/ops/infra/stacks/ct-backup/scripts/backup-dispatch.sh \
    root@$CT:/usr/local/bin/backup-dispatch.sh
  ssh root@$CT 'chmod 755 /usr/local/bin/backup-dispatch.sh; \
    cat > /etc/backup-dispatch.conf <<EOF
ALLOW_RSYNC_PATHS="/opt/stacks"
ALLOW_EXPORT_VOLUMES=0
ALLOW_PG_DUMP_IMMICH=0
EOF'
done
```

- [ ] **Step 8: Deploy dispatcher + config to ct-photos (special: pg_dump allowed)**

```bash
scp /home/psy/Documents/personal/ops/infra/stacks/ct-backup/scripts/backup-dispatch.sh \
  root@ct-photos:/usr/local/bin/backup-dispatch.sh
ssh root@ct-photos 'chmod 755 /usr/local/bin/backup-dispatch.sh; \
  cat > /etc/backup-dispatch.conf <<EOF
ALLOW_RSYNC_PATHS="/opt/stacks"
ALLOW_EXPORT_VOLUMES=0
ALLOW_PG_DUMP_IMMICH=1
EOF'
```

- [ ] **Step 9: Add ct-backup's SSH public key to each target's authorized_keys — ONE entry per host**

On ct-backup, capture the key once:
```bash
KEY=$(ssh root@ct-backup 'cat /root/.ssh/id_ed25519.pub')
echo "$KEY"
```

Then deploy:
```bash
for H in proxmoxmain proxmoxnode ct-dns ct-tunnel ct-nvr ct-media ct-photos ct-files ct-mgmt; do
  ssh root@$H "echo 'command=\"/usr/local/bin/backup-dispatch.sh\",restrict $KEY ct-backup-dispatch' >> /root/.ssh/authorized_keys"
done
```

- [ ] **Step 10: Verify SSH reach from ct-backup to all targets**

```bash
# Named subcommand on a CT with volumes:
ssh root@ct-backup 'ssh -o StrictHostKeyChecking=accept-new root@192.168.3.5 list-volumes'
```
Expected: `pihole-data`, `pihole-dns` (or whatever ct-dns's named volumes are).

```bash
# Disallowed command should be rejected:
ssh root@ct-backup 'ssh root@192.168.3.5 whoami'
```
Expected: `Command not permitted: whoami` on stderr, exit code 1.

```bash
# Disallowed rsync path:
ssh root@ct-backup 'rsync root@192.168.3.5:/root/ /tmp/ 2>&1 | head -5'
```
Expected: `Rsync to /root is not permitted`.

```bash
# Allowed rsync path (dry-run):
ssh root@ct-backup 'rsync -an root@192.168.3.5:/opt/stacks/ /tmp/ 2>&1 | head -5'
```
Expected: rsync begins listing file tree (or completes silently) — no permission error.

- [ ] **Step 11: Commit the dispatcher script**

```bash
cd /home/psy/Documents/personal/ops/infra
git add stacks/ct-backup/scripts/backup-dispatch.sh
git commit -m "Add SSH dispatcher for ct-backup operations"
```

---

## Task 7: Configure host-to-ct-backup bind mounts for bulk data sources

**Goal:** Expose the bind-mount source paths (Immich, samba/psy, *arr configs, Jellyfin config, Frigate config) read-only into ct-backup.

**Files:**
- Modify (on proxmoxmain): `/etc/pve/lxc/109.conf`

- [ ] **Step 1: Stop ct-backup before editing its config**

```bash
ssh root@proxmoxmain 'pct stop 109'
```

- [ ] **Step 2: Add bind mounts**

```bash
ssh root@proxmoxmain 'cat >> /etc/pve/lxc/109.conf <<EOF
mp0: /mnt/cloud/volumes/mediaserver/immich,mp=/backup-sources/immich,ro=1
mp1: /mnt/cloud/volumes/samba/data/psy,mp=/backup-sources/samba-psy,ro=1
mp2: /mnt/cloud/volumes/samba/config,mp=/backup-sources/samba-config,ro=1
mp3: /mnt/cloud/volumes/mediaserver/jellyfin/config,mp=/backup-sources/jellyfin-config,ro=1
mp4: /mnt/cloud/volumes/mediaserver/sonarr/config,mp=/backup-sources/arr-configs/sonarr,ro=1
mp5: /mnt/cloud/volumes/mediaserver/radarr/config,mp=/backup-sources/arr-configs/radarr,ro=1
mp6: /mnt/cloud/volumes/mediaserver/prowlarr/config,mp=/backup-sources/arr-configs/prowlarr,ro=1
mp7: /mnt/cloud/volumes/mediaserver/deluge/config,mp=/backup-sources/arr-configs/deluge,ro=1
mp8: /mnt/cloud/volumes/mediaserver/flaresolverr/config,mp=/backup-sources/arr-configs/flaresolverr,ro=1
mp9: /mnt/nvr-data/config,mp=/backup-sources/frigate-config,ro=1
EOF'
```

- [ ] **Step 3: Start ct-backup and verify mounts**

```bash
ssh root@proxmoxmain 'pct start 109'
sleep 5
ssh root@ct-backup 'ls /backup-sources/'
```
Expected: `arr-configs  frigate-config  immich  jellyfin-config  samba-config  samba-psy`

- [ ] **Step 4: Verify read-only enforcement**

```bash
ssh root@ct-backup 'touch /backup-sources/immich/TEST 2>&1'
```
Expected: `touch: cannot touch ... Read-only file system`.

- [ ] **Step 5: Commit nothing — config lives in /etc/pve, captured by Task 14's rsync**

---

## Task 8: Initialize restic repository on B2

**Goal:** Create the restic repo at the B2 bucket, verify round-trip works.

**Files:**
- Create (on ct-backup): `/etc/restic/password`
- Create (on ct-backup): `/etc/restic/b2.env`

- [ ] **Step 1: Create restic config directory**

```bash
ssh root@ct-backup 'install -d -m 700 /etc/restic'
```

- [ ] **Step 2: Store the repo password file**

```bash
ssh root@ct-backup 'umask 077; cat > /etc/restic/password'
# Paste the password from Task 3 step 1, then Ctrl-D
```

Verify:
```bash
ssh root@ct-backup 'ls -la /etc/restic/password'
```
Expected: `-rw------- 1 root root 45 ...`

- [ ] **Step 3: Store B2 credentials as an env-file**

```bash
ssh root@ct-backup 'umask 077; cat > /etc/restic/b2.env <<EOF
export AWS_ACCESS_KEY_ID=<keyID from Task 2>
export AWS_SECRET_ACCESS_KEY=<applicationKey from Task 2>
export RESTIC_REPOSITORY=s3:s3.<region>.backblazeb2.com/blvck-homelab-backup/backup
export RESTIC_PASSWORD_FILE=/etc/restic/password
EOF'
```

- [ ] **Step 4: Initialize the repo**

```bash
ssh root@ct-backup 'source /etc/restic/b2.env && restic init'
```
Expected: `created restic repository <id> at s3:...`

- [ ] **Step 5: Verify round-trip with a small test backup**

```bash
ssh root@ct-backup 'source /etc/restic/b2.env && \
  echo "hello world $(date)" > /tmp/hello.txt && \
  restic backup /tmp/hello.txt --tag test-init && \
  restic snapshots'
```
Expected: snapshot listed with tag `test-init`.

- [ ] **Step 6: Forget the test snapshot**

```bash
ssh root@ct-backup 'source /etc/restic/b2.env && \
  restic forget --tag test-init --prune'
```

- [ ] **Step 7: Commit nothing — credentials are secret, never in git**

---

## Task 9: Write the pre-backup collection script

**Goal:** Orchestrate all the rsync pulls, Docker volume exports, pg_dump, and host-config collection into `/var/backup-staging/`.

**Files:**
- Create (in repo): `stacks/ct-backup/scripts/pre-backup.sh`
- Create (in repo): `stacks/ct-backup/scripts/host-export.sh` (runs on each Proxmox host to populate `/var/backup-export/`)

- [ ] **Step 1: Create the stack directory structure**

```bash
mkdir -p /home/psy/Documents/personal/ops/infra/stacks/ct-backup/scripts
```

- [ ] **Step 2: Write the host-side export script**

Create `/home/psy/Documents/personal/ops/infra/stacks/ct-backup/scripts/host-export.sh`:

```bash
#!/bin/bash
# Runs on each Proxmox host (proxmoxmain, proxmoxnode) via a local systemd timer.
# Populates /var/backup-export/ with small host-level state for ct-backup to rsync.
set -euo pipefail

EXPORT=/var/backup-export
rm -rf "$EXPORT"
install -d -m 755 "$EXPORT"

# Host-level config files
cp -a /etc/fstab              "$EXPORT/fstab"
cp -a /etc/network/interfaces "$EXPORT/interfaces" 2>/dev/null || true
cp -a /etc/hosts              "$EXPORT/hosts"
cp -a /root/.ssh              "$EXPORT/root-ssh"

# Custom systemd units (non-package-managed)
install -d "$EXPORT/systemd-system"
find /etc/systemd/system -maxdepth 1 -type f \( -name "*.service" -o -name "*.timer" \) \
  -exec cp -a {} "$EXPORT/systemd-system/" \;

# Root crontab
crontab -l > "$EXPORT/root-crontab" 2>/dev/null || echo "" > "$EXPORT/root-crontab"

# Node-scoped PVE config (each node's local overrides)
install -d "$EXPORT/pve-local"
cp -a /etc/pve/local/* "$EXPORT/pve-local/" 2>/dev/null || true

echo "Export complete at $(date -Iseconds)"
```

Make executable in the repo copy:
```bash
chmod +x /home/psy/Documents/personal/ops/infra/stacks/ct-backup/scripts/host-export.sh
```

- [ ] **Step 3: Write the pre-backup orchestrator**

Create `/home/psy/Documents/personal/ops/infra/stacks/ct-backup/scripts/pre-backup.sh`:

```bash
#!/bin/bash
# Runs on ct-backup. Populates /var/backup-staging/ with all non-bind-mount data:
#   - .env files from each CT's /opt/stacks/
#   - Docker named volume exports from each CT
#   - Immich Postgres dump from ct-photos
#   - /etc/pve from both Proxmox nodes
#   - Host-level config from both Proxmox nodes
set -euo pipefail

STAGING=/var/backup-staging
LOG=/var/log/backup/pre-backup.log

# Per-target IPs
declare -A CT_IPS=(
  [ct-dns]=192.168.3.5
  [ct-tunnel]=192.168.3.6
  [ct-nvr]=192.168.3.7
  [ct-media]=192.168.3.8
  [ct-photos]=192.168.3.9
  [ct-files]=192.168.3.11
  [ct-mgmt]=192.168.3.12
)
PROXMOXMAIN_IP=192.168.3.2
PROXMOXNODE_IP=192.168.3.3

# Fresh staging
rm -rf "$STAGING"
install -d -m 700 "$STAGING"/{env,volumes,pve-main,pve-node,host-cfg-main,host-cfg-node}

log() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG"; }

# --- 1. Rsync .env files from each CT's /opt/stacks ---
for CT in "${!CT_IPS[@]}"; do
  IP="${CT_IPS[$CT]}"
  log "Pulling .env files from $CT ($IP)..."
  install -d "$STAGING/env/$CT"
  # rrsync restricts us to /opt/stacks; fetch only .env files (any depth)
  rsync -a --include='*/' --include='.env' --include='**/.env' --exclude='*' \
    "root@$IP:/opt/stacks/" "$STAGING/env/$CT/" 2>&1 | tee -a "$LOG" \
    || log "WARN: .env sync failed for $CT"
done

# --- 2. Export Docker named volumes from CTs that have them ---
# CTs where ALLOW_EXPORT_VOLUMES=1 on the dispatcher:
#   ct-dns (pihole-data, pihole-dns)
#   ct-mgmt (portainer-data, caddy-data, caddy-config)
#   ct-files (filebrowser-db)
#   ct-tunnel (cloudflared state if any)
for CT in ct-dns ct-mgmt ct-files ct-tunnel; do
  IP="${CT_IPS[$CT]}"
  log "Listing volumes on $CT..."
  install -d "$STAGING/volumes/$CT"
  VOLS=$(ssh "root@$IP" list-volumes)
  for VOL in $VOLS; do
    log "  Exporting volume $VOL from $CT..."
    ssh "root@$IP" "export-volume $VOL" > "$STAGING/volumes/$CT/$VOL.tar.gz"
  done
done

# --- 3. Immich Postgres dump ---
log "Dumping Immich Postgres from ct-photos..."
ssh "root@${CT_IPS[ct-photos]}" pg-dump-immich | gzip > "$STAGING/immich-postgres.sql.gz"

# --- 4. /etc/pve from both Proxmox hosts ---
log "Pulling /etc/pve from proxmoxmain..."
rsync -a "root@$PROXMOXMAIN_IP:/etc/pve/" "$STAGING/pve-main/" 2>&1 | tee -a "$LOG"

log "Pulling /etc/pve from proxmoxnode..."
rsync -a "root@$PROXMOXNODE_IP:/etc/pve/" "$STAGING/pve-node/" 2>&1 | tee -a "$LOG"

# --- 5. Host-level config from both nodes ---
# Depends on host-export.sh having populated /var/backup-export/ via the
# host-side systemd timer scheduled 15 minutes earlier.
log "Pulling host config from proxmoxmain..."
rsync -a "root@$PROXMOXMAIN_IP:/var/backup-export/" "$STAGING/host-cfg-main/" 2>&1 | tee -a "$LOG"

log "Pulling host config from proxmoxnode..."
rsync -a "root@$PROXMOXNODE_IP:/var/backup-export/" "$STAGING/host-cfg-node/" 2>&1 | tee -a "$LOG"

log "Pre-backup complete. Staging size: $(du -sh "$STAGING" | cut -f1)"
```

Mark executable:
```bash
chmod +x /home/psy/Documents/personal/ops/infra/stacks/ct-backup/scripts/pre-backup.sh
```

- [ ] **Step 4: Deploy scripts to ct-backup and the two Proxmox hosts**

```bash
# pre-backup.sh → ct-backup
scp /home/psy/Documents/personal/ops/infra/stacks/ct-backup/scripts/pre-backup.sh \
  root@ct-backup:/usr/local/bin/pre-backup.sh
ssh root@ct-backup 'chmod 755 /usr/local/bin/pre-backup.sh'

# host-export.sh → both Proxmox hosts
scp /home/psy/Documents/personal/ops/infra/stacks/ct-backup/scripts/host-export.sh \
  root@proxmoxmain:/usr/local/bin/host-export.sh
scp /home/psy/Documents/personal/ops/infra/stacks/ct-backup/scripts/host-export.sh \
  root@proxmoxnode:/usr/local/bin/host-export.sh
ssh root@proxmoxmain 'chmod 755 /usr/local/bin/host-export.sh'
ssh root@proxmoxnode 'chmod 755 /usr/local/bin/host-export.sh'
```

- [ ] **Step 5: Run host-export.sh once manually on each host**

```bash
ssh root@proxmoxmain 'host-export.sh'
ssh root@proxmoxnode 'host-export.sh'
ssh root@proxmoxmain 'ls /var/backup-export/'
```
Expected: `fstab  hosts  interfaces  pve-local  root-crontab  root-ssh  systemd-system`.

- [ ] **Step 6: Run pre-backup.sh on ct-backup and verify staging populated**

```bash
ssh root@ct-backup 'install -d /var/log/backup && /usr/local/bin/pre-backup.sh'
ssh root@ct-backup 'ls -la /var/backup-staging/'
```
Expected: subdirectories `env/`, `volumes/`, `pve-main/`, `pve-node/`, `host-cfg-main/`, `host-cfg-node/`, and file `immich-postgres.sql.gz`.

- [ ] **Step 7: Commit scripts to repo**

```bash
cd /home/psy/Documents/personal/ops/infra
git add stacks/ct-backup/scripts/
git commit -m "Add pre-backup and host-export orchestration scripts"
```

---

## Task 10: Write the backup wrapper with Telegram failure trap

**Goal:** Single entry point that runs pre-backup + restic + status.json update + Telegram trap.

**Files:**
- Create (in repo): `stacks/ct-backup/scripts/backup.sh`
- Create (on ct-backup): `/etc/restic/telegram.env`

- [ ] **Step 1: Create telegram.env on ct-backup with the same bot as Gatus**

Look up Telegram creds from `stacks/ct-mgmt/gatus/.env` (TELEGRAM_TOKEN, TELEGRAM_CHAT_ID).

```bash
ssh root@ct-backup 'umask 077; cat > /etc/restic/telegram.env <<EOF
export TELEGRAM_TOKEN=<from gatus .env>
export TELEGRAM_CHAT_ID=<from gatus .env>
EOF'
```

- [ ] **Step 2: Write backup.sh in the repo**

Create `/home/psy/Documents/personal/ops/infra/stacks/ct-backup/scripts/backup.sh`:

```bash
#!/bin/bash
set -euo pipefail

# --- Config ---
source /etc/restic/b2.env
source /etc/restic/telegram.env
STATUS_DIR=/var/lib/backup-status
LOG=/var/log/backup/backup-$(date +%Y%m%d-%H%M%S).log
install -d -m 755 "$STATUS_DIR"
install -d /var/log/backup

# --- Telegram failure trap ---
telegram_fail() {
  local exit_code=$?
  local last_err=$(tail -30 "$LOG" 2>/dev/null | sed 's/"/\\"/g' | tr '\n' ' ' | cut -c1-1200)
  curl -s -X POST \
    --data-urlencode "chat_id=$TELEGRAM_CHAT_ID" \
    --data-urlencode "text=🚨 Homelab backup FAILED (exit $exit_code) at $(hostname)
Last output:
$last_err" \
    "https://api.telegram.org/bot$TELEGRAM_TOKEN/sendMessage" > /dev/null || true
  exit $exit_code
}
trap telegram_fail ERR

# --- Run ---
START=$(date +%s)
{
  echo "=== Backup started $(date -Iseconds) ==="
  /usr/local/bin/pre-backup.sh

  echo "=== Running restic backup ==="
  restic backup \
    /backup-sources/ \
    /var/backup-staging/ \
    --tag nightly \
    --host ct-backup \
    --cleanup-cache

  echo "=== Backup complete $(date -Iseconds) ==="
} 2>&1 | tee -a "$LOG"

# --- Success: write status.json for Gatus ---
DURATION=$(( $(date +%s) - START ))
cat > "$STATUS_DIR/status.json" <<EOF
{
  "timestamp": "$(date -Iseconds)",
  "duration_sec": $DURATION,
  "snapshots": $(restic snapshots --json 2>/dev/null | jq 'length')
}
EOF

echo "=== status.json updated ==="
```

Mark executable and commit:
```bash
chmod +x /home/psy/Documents/personal/ops/infra/stacks/ct-backup/scripts/backup.sh
```

- [ ] **Step 3: Deploy backup.sh to ct-backup**

```bash
scp /home/psy/Documents/personal/ops/infra/stacks/ct-backup/scripts/backup.sh \
  root@ct-backup:/usr/local/bin/backup.sh
ssh root@ct-backup 'chmod 755 /usr/local/bin/backup.sh'
```

- [ ] **Step 4: Commit the script**

```bash
cd /home/psy/Documents/personal/ops/infra
git add stacks/ct-backup/scripts/backup.sh
git commit -m "Add backup.sh wrapper with Telegram failure trap"
```

---

## Task 11: Run first full backup manually and verify

**Goal:** Seed the repo with the first real snapshot. Confirm size, duration, and that everything we expected is captured.

**Files:** None in repo

- [ ] **Step 1: Disable any scheduled runs while we test manually**

(They aren't set up yet — skip.)

- [ ] **Step 2: Run the full backup**

```bash
ssh root@ct-backup '/usr/local/bin/backup.sh'
```

This will take 2–3 hours. Monitor by tailing log from blvckmain:
```bash
ssh root@ct-backup 'tail -f /var/log/backup/backup-*.log'
```

Expected: completes with `=== status.json updated ===`. Exit code 0.

- [ ] **Step 3: Check snapshot**

```bash
ssh root@ct-backup 'source /etc/restic/b2.env && restic snapshots'
```
Expected: one snapshot tagged `nightly`, roughly ~125 GB source size, compressed/deduped upload much less.

- [ ] **Step 4: Sanity-check the snapshot contents**

```bash
ssh root@ct-backup 'source /etc/restic/b2.env && restic ls latest | head -100'
```
Expected: see paths under `/backup-sources/immich/`, `/backup-sources/samba-psy/`, `/backup-sources/arr-configs/`, `/var/backup-staging/env/ct-*/`, etc.

- [ ] **Step 5: Verify status.json**

```bash
ssh root@ct-backup 'cat /var/lib/backup-status/status.json'
```
Expected: valid JSON with recent timestamp.

- [ ] **Step 6: Commit nothing — operational check only**

---

## Task 12: Serve status.json via Caddy on ct-backup

**Goal:** Expose `/var/lib/backup-status/status.json` on port 80 so Gatus can poll it.

**Files:**
- Create (on ct-backup): `/etc/caddy/Caddyfile`

- [ ] **Step 1: Write the Caddyfile**

```bash
ssh root@ct-backup 'cat > /etc/caddy/Caddyfile <<EOF
:80 {
    root * /var/lib/backup-status
    file_server browse
    log {
        output file /var/log/caddy/access.log
    }
}
EOF'
```

- [ ] **Step 2: Enable and start Caddy**

```bash
ssh root@ct-backup 'systemctl enable caddy && systemctl start caddy'
```

- [ ] **Step 3: Verify it responds**

```bash
curl http://192.168.3.13/status.json
```
Expected: the JSON object written by the last backup.

- [ ] **Step 4: Commit nothing — Caddyfile lives on ct-backup, captured by its bind mount setup (none needed) or by host-export on proxmoxmain (partially). For repo completeness, also add a copy to the stack dir**

```bash
mkdir -p /home/psy/Documents/personal/ops/infra/stacks/ct-backup
cp <Caddyfile content> /home/psy/Documents/personal/ops/infra/stacks/ct-backup/Caddyfile
```

Actually simpler — write the Caddyfile directly in the repo:
```bash
cat > /home/psy/Documents/personal/ops/infra/stacks/ct-backup/Caddyfile <<EOF
:80 {
    root * /var/lib/backup-status
    file_server browse
    log {
        output file /var/log/caddy/access.log
    }
}
EOF
```

Commit:
```bash
cd /home/psy/Documents/personal/ops/infra
git add stacks/ct-backup/Caddyfile
git commit -m "Add ct-backup Caddyfile for status endpoint"
```

---

## Task 13: Install systemd timers for nightly, weekly prune, monthly check

**Goal:** Automate the schedule per the design (nightly 03:00, weekly prune Sun 04:00, monthly check 1st @ 05:00).

**Files:**
- Create (in repo): `stacks/ct-backup/systemd/backup.service`
- Create (in repo): `stacks/ct-backup/systemd/backup.timer`
- Create (in repo): `stacks/ct-backup/systemd/backup-prune.service`
- Create (in repo): `stacks/ct-backup/systemd/backup-prune.timer`
- Create (in repo): `stacks/ct-backup/systemd/backup-check.service`
- Create (in repo): `stacks/ct-backup/systemd/backup-check.timer`

- [ ] **Step 1: Write backup.service and backup.timer**

`stacks/ct-backup/systemd/backup.service`:
```ini
[Unit]
Description=Nightly restic backup
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/backup.sh
```

`stacks/ct-backup/systemd/backup.timer`:
```ini
[Unit]
Description=Nightly backup (03:00)

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true
RandomizedDelaySec=10min

[Install]
WantedBy=timers.target
```

- [ ] **Step 2: Write backup-prune.service/timer**

`stacks/ct-backup/systemd/backup-prune.service`:
```ini
[Unit]
Description=Weekly restic prune
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/restic/b2.env
ExecStart=/usr/bin/restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --keep-yearly 2 --prune
```

`stacks/ct-backup/systemd/backup-prune.timer`:
```ini
[Unit]
Description=Weekly prune (Sunday 04:00)

[Timer]
OnCalendar=Sun *-*-* 04:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Write backup-check.service/timer**

`stacks/ct-backup/systemd/backup-check.service`:
```ini
[Unit]
Description=Monthly restic integrity check
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
EnvironmentFile=/etc/restic/b2.env
ExecStart=/usr/bin/restic check --read-data-subset 5%
```

`stacks/ct-backup/systemd/backup-check.timer`:
```ini
[Unit]
Description=Monthly check (1st of month, 05:00)

[Timer]
OnCalendar=*-*-01 05:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: Deploy units to ct-backup and enable**

```bash
for UNIT in backup.service backup.timer backup-prune.service backup-prune.timer backup-check.service backup-check.timer; do
  scp /home/psy/Documents/personal/ops/infra/stacks/ct-backup/systemd/$UNIT \
    root@ct-backup:/etc/systemd/system/$UNIT
done

ssh root@ct-backup 'systemctl daemon-reload && \
  systemctl enable --now backup.timer backup-prune.timer backup-check.timer'
```

- [ ] **Step 5: Verify timers**

```bash
ssh root@ct-backup 'systemctl list-timers backup*'
```
Expected: three timers listed with future run times.

- [ ] **Step 6: Install host-export timer on both Proxmox hosts**

Create `stacks/ct-backup/systemd/host-export.service` and `host-export.timer` in the repo:

`host-export.service`:
```ini
[Unit]
Description=Export host config for ct-backup

[Service]
Type=oneshot
ExecStart=/usr/local/bin/host-export.sh
```

`host-export.timer`:
```ini
[Unit]
Description=Pre-backup host export (02:45 daily)

[Timer]
OnCalendar=*-*-* 02:45:00
Persistent=true

[Install]
WantedBy=timers.target
```

Deploy to both hosts:
```bash
for HOST in proxmoxmain proxmoxnode; do
  scp /home/psy/Documents/personal/ops/infra/stacks/ct-backup/systemd/host-export.service \
    root@$HOST:/etc/systemd/system/host-export.service
  scp /home/psy/Documents/personal/ops/infra/stacks/ct-backup/systemd/host-export.timer \
    root@$HOST:/etc/systemd/system/host-export.timer
  ssh root@$HOST 'systemctl daemon-reload && systemctl enable --now host-export.timer'
done
```

- [ ] **Step 7: Commit systemd unit files**

```bash
cd /home/psy/Documents/personal/ops/infra
git add stacks/ct-backup/systemd/
git commit -m "Add systemd timers for backup schedule (nightly/weekly/monthly)"
```

---

## Task 14: Add Gatus freshness endpoint

**Goal:** Wire the backup freshness check into the existing Gatus config so you get Telegram alerts if backups silently stop.

**Files:**
- Modify: `stacks/ct-mgmt/gatus/config.yaml`

- [ ] **Step 1: Read current Gatus config to find the right insertion point**

```bash
cat /home/psy/Documents/personal/ops/infra/stacks/ct-mgmt/gatus/config.yaml | head -50
```

- [ ] **Step 2: Add endpoint under the appropriate tier**

Backup freshness is *important* tier (not critical — a single missed night isn't catastrophic). Add this endpoint under the important section:

```yaml
  - name: Backup freshness
    group: Important
    url: http://192.168.3.13/status.json
    interval: 15m
    conditions:
      - '[STATUS] == 200'
      - '[CONNECTED] == true'
      - '[BODY].timestamp > 2026-01-01T00:00:00Z'  # sentinel: just ensures the field exists
    alerts:
      - type: telegram
        failure-threshold: 2
        success-threshold: 2
        send-on-resolved: true
```

Note: Gatus doesn't support relative time comparisons natively in conditions. A staleness check needs a Gatus version with `[BODY].timestamp` extraction — confirm the version in use supports JSON path comparisons. If not, fall back to checking the JSON parses and status is 200; the ~hourly backup job failing will produce a trap-based Telegram alert anyway.

If the Gatus version in use doesn't support the timestamp check, use this simpler form:
```yaml
  - name: Backup freshness
    group: Important
    url: http://192.168.3.13/status.json
    interval: 15m
    conditions:
      - '[STATUS] == 200'
      - '[CONNECTED] == true'
      - '[BODY].timestamp != ""'
```
(Staleness is still caught because Caddy will 404 if status.json stops being written — but only after a restart or disk clear. In practice the Telegram trap in backup.sh handles this category.)

- [ ] **Step 3: Deploy Gatus config**

```bash
scp /home/psy/Documents/personal/ops/infra/stacks/ct-mgmt/gatus/config.yaml \
  root@ct-mgmt:/opt/stacks/ct-mgmt/gatus/config.yaml
ssh root@ct-mgmt 'cd /opt/stacks/ct-mgmt && docker compose restart gatus'
```

- [ ] **Step 4: Verify Gatus picks up the new endpoint**

Open https://status.lan in a browser. Look for "Backup freshness" under the Important group, showing green.

- [ ] **Step 5: Commit the Gatus config change**

```bash
cd /home/psy/Documents/personal/ops/infra
git add stacks/ct-mgmt/gatus/config.yaml
git commit -m "Add Gatus endpoint for backup freshness"
```

---

## Task 15: Add Caddy reverse-proxy entry for backup.lan (optional polish)

**Goal:** Serve the status endpoint under a friendly URL consistent with the rest of the homelab naming convention.

**Files:**
- Modify: `stacks/ct-mgmt/Caddyfile`
- Modify: `stacks/ct-dns/custom-dns.list`

- [ ] **Step 1: Add Caddy vhost entry**

Append to `stacks/ct-mgmt/Caddyfile`:
```caddyfile
backup.lan {
    reverse_proxy 192.168.3.13:80
}
```

- [ ] **Step 2: Add DNS entry in Pi-hole config**

Append to `stacks/ct-dns/custom-dns.list`:
```
192.168.3.12 backup.lan
```

- [ ] **Step 3: Deploy changes**

```bash
scp /home/psy/Documents/personal/ops/infra/stacks/ct-mgmt/Caddyfile \
  root@ct-mgmt:/opt/stacks/ct-mgmt/Caddyfile
scp /home/psy/Documents/personal/ops/infra/stacks/ct-dns/custom-dns.list \
  root@ct-dns:/opt/stacks/ct-dns/custom-dns.list
ssh root@ct-mgmt 'cd /opt/stacks/ct-mgmt && docker compose restart caddy'
ssh root@ct-dns 'cd /opt/stacks/ct-dns && docker compose restart pihole'
```

- [ ] **Step 4: Verify**

```bash
curl http://backup.lan/status.json
```
Expected: valid JSON.

- [ ] **Step 5: Update the Gatus endpoint URL to use backup.lan**

Edit `stacks/ct-mgmt/gatus/config.yaml` — change `http://192.168.3.13/status.json` to `http://backup.lan/status.json`. Redeploy Gatus.

- [ ] **Step 6: Commit**

```bash
cd /home/psy/Documents/personal/ops/infra
git add stacks/ct-mgmt/Caddyfile stacks/ct-dns/custom-dns.list stacks/ct-mgmt/gatus/config.yaml
git commit -m "Expose backup.lan via Caddy + DNS"
```

---

## Task 16: Update CLAUDE.md inventory with ct-backup

**Goal:** Document ct-backup in the repo's canonical infra inventory.

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add ct-backup section under the CT inventory**

Insert this block after ct-mgmt's entry (the alphabetical/logical ordering doesn't matter much, but grouping with other mgmt CTs is cleanest):

```markdown
### ct-backup (LXC — VMID 109 on proxmoxmain)
- **IP:** 192.168.3.13
- **User:** root
- **OS:** Debian 13 (Trixie), unprivileged LXC
- **SSH:** Port 22, key-based auth (no password)
- **Resources:** 1 vCPU, 512MB RAM, 256MB swap, 4GB disk
- **Role:** Off-site backup runner. Executes restic nightly against Backblaze B2, pulling .env files + Docker named volumes + Immich Postgres dump + /etc/pve + host config from all other CTs and both Proxmox nodes, plus bind-mounted bulk data (Immich, samba/psy, *arr configs, Jellyfin config, Frigate config).
- **Stack:** `/opt/stacks/ct-backup/` (local copy: `stacks/ct-backup/`) — no Docker; scripts + systemd units installed natively.
- **Ports:** 80 (Caddy status endpoint at `backup.lan`)
- **Config notes:** Unprivileged. Bind mounts for bulk data sources are read-only. SSH key in `/root/.ssh/id_ed25519` with forced-command restrictions on every target. Secrets in `/etc/restic/` (repo password, B2 creds, Telegram creds). No Docker daemon.
```

- [ ] **Step 2: Update the Network Layout diagram**

In the `blvckmain` section of the Network Layout, add:
```
  └── ssh ct-backup      → 192.168.3.13:22   (root, key auth)
```

- [ ] **Step 3: Commit**

```bash
cd /home/psy/Documents/personal/ops/infra
git add CLAUDE.md
git commit -m "Document ct-backup in infrastructure inventory"
```

---

## Task 17: Write scripts/bootstrap-ct.sh

**Goal:** Idempotent CT provisioner that codifies the standard pattern: Debian 13 + Docker + portainer-agent + deployed stack. Used both for new CT creation and DR.

**Files:**
- Create (in repo): `scripts/bootstrap-ct.sh`

- [ ] **Step 1: Create scripts directory**

```bash
mkdir -p /home/psy/Documents/personal/ops/infra/scripts
```

- [ ] **Step 2: Write bootstrap-ct.sh**

```bash
#!/bin/bash
# Bootstraps an LXC from its restored /etc/pve/lxc/<id>.conf definition:
# installs Docker + portainer-agent, copies the stack from the repo, restores
# Docker named volumes from a backup staging dir, and brings the stack up.
#
# Usage: bootstrap-ct.sh <ct-name> [--restore-volumes-from <dir>]
#
# Assumes:
#   - Run on proxmoxmain (the Proxmox host)
#   - /etc/pve/lxc/<id>.conf already in place (restored from backup)
#   - Repo checked out at /root/infra with stacks/ct-<name>/ populated
#   - .env files already in stacks/ct-<name>/ (restored from backup staging)
#   - Restored volume tarballs optionally available at <dir>/<ct-name>/<volname>.tar.gz

set -euo pipefail

CT_NAME="${1:?Usage: bootstrap-ct.sh <ct-name> [--restore-volumes-from <dir>]}"
RESTORE_DIR=""
if [[ "${2:-}" == "--restore-volumes-from" ]]; then
  RESTORE_DIR="${3:?--restore-volumes-from requires a directory}"
fi

REPO=/root/infra
STACK_DIR="$REPO/stacks/$CT_NAME"
if [[ ! -d "$STACK_DIR" ]]; then
  echo "ERROR: stack dir $STACK_DIR does not exist"
  exit 1
fi

# Find VMID from config files in /etc/pve/lxc/
VMID=$(grep -l "^hostname: $CT_NAME$" /etc/pve/lxc/*.conf 2>/dev/null | head -1 | xargs -I{} basename {} .conf)
if [[ -z "$VMID" ]]; then
  echo "ERROR: no /etc/pve/lxc/*.conf found with hostname $CT_NAME"
  exit 1
fi

echo "Bootstrapping $CT_NAME (VMID $VMID)..."

# Start CT if not running
if ! pct status "$VMID" | grep -q running; then
  pct start "$VMID"
  sleep 5
fi

# Install Docker + portainer-agent
pct exec "$VMID" -- bash -c '
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release; echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
'

# Copy stack files into CT
pct exec "$VMID" -- mkdir -p /opt/stacks
tar -C "$REPO/stacks" -cf - "$CT_NAME" | pct exec "$VMID" -- tar -C /opt/stacks -xf -

# Start portainer-agent (defined in the stack's compose)
pct exec "$VMID" -- bash -c "cd /opt/stacks/$CT_NAME && docker compose up -d portainer-agent"

# Restore Docker named volumes if a restore dir was given
if [[ -n "$RESTORE_DIR" && -d "$RESTORE_DIR/$CT_NAME" ]]; then
  for TARBALL in "$RESTORE_DIR/$CT_NAME"/*.tar.gz; do
    [[ -f "$TARBALL" ]] || continue
    VOL=$(basename "$TARBALL" .tar.gz)
    echo "Restoring volume $VOL into $CT_NAME..."
    pct exec "$VMID" -- docker volume create "$VOL" > /dev/null
    cat "$TARBALL" | pct exec "$VMID" -- docker run --rm -i -v "$VOL":/dst alpine tar xzf - -C /dst
  done
fi

# Bring up the full stack
pct exec "$VMID" -- bash -c "cd /opt/stacks/$CT_NAME && docker compose up -d"

echo "✅ $CT_NAME bootstrapped."
```

Mark executable:
```bash
chmod +x /home/psy/Documents/personal/ops/infra/scripts/bootstrap-ct.sh
```

- [ ] **Step 3: Smoke-test with a dry-run on an existing CT (ct-tunnel is small and easy)**

Since the CTs already exist and are running, we can't test "create from scratch". Instead, verify the script parses and doesn't error on invalid input:
```bash
/home/psy/Documents/personal/ops/infra/scripts/bootstrap-ct.sh
```
Expected: usage message printed to stderr, exit 1.

The real test is Task 20 (DR drill).

- [ ] **Step 4: Commit**

```bash
cd /home/psy/Documents/personal/ops/infra
git add scripts/bootstrap-ct.sh
git commit -m "Add scripts/bootstrap-ct.sh — idempotent CT provisioner"
```

---

## Task 18: Write docs/recovery.md

**Goal:** Step-by-step DR runbook for total proxmoxmain loss.

**Files:**
- Create (in repo): `docs/recovery.md`

- [ ] **Step 1: Write the runbook**

Create `/home/psy/Documents/personal/ops/infra/docs/recovery.md`:

```markdown
# Disaster Recovery Runbook

Target: rebuild the homelab from scratch on new hardware in ≤1 weekend.

## Prerequisites (have on hand)

- 1Password / Bitwarden access — contains restic repo password + B2 credentials
- Paper copy of the same (fireproof backup location)
- GitHub access to `psychonaut0/infra` (or a local clone from any surviving device)
- SSH keys for Tailscale enrollment
- The physical replacement hardware

## Phase 1 — Hypervisor install

1. Install Proxmox VE on replacement hardware. Use the same hostname (`proxmoxmain`).
2. Join the Proxmox cluster if proxmoxnode is still alive:
   `pvecm add <proxmoxnode-ip>`
3. Install Tailscale and enroll the node. Advertise subnet routes if needed.
4. Install restic:
   `apt-get install -y restic rsync jq`

## Phase 2 — Recover from B2

1. Set env for restic:
   ```bash
   export AWS_ACCESS_KEY_ID=<from vault>
   export AWS_SECRET_ACCESS_KEY=<from vault>
   export RESTIC_REPOSITORY=s3:s3.<region>.backblazeb2.com/blvck-homelab-backup/backup
   export RESTIC_PASSWORD_FILE=/tmp/resticpass
   echo -n '<password from vault>' > $RESTIC_PASSWORD_FILE
   chmod 600 $RESTIC_PASSWORD_FILE
   ```
2. Verify repo reachable:
   `restic snapshots`
3. Restore the latest snapshot to `/recovery`:
   ```bash
   mkdir -p /recovery
   restic restore latest --target /recovery
   ```
   ~2–3 hours over home downlink for ~125 GB.

## Phase 3 — Recreate bulk storage

1. Partition the new HDDs to match `docs/hardware.md`:
   - sda (4 TB) → single ext4 partition → mount at `/mnt/cloud-2`
   - sdb (1 TB) → single ext4 partition → mount at `/mnt/cloud-1`
   - sdc (456 GB) → LVM physical volume → VG `nvr-data` → thin pool `nvr-data` → thin LV `vm-100-disk-0` (400 GB, ext4 inside) → kpartx-activated partition mounted at `/mnt/nvr-data`
2. Write `/etc/fstab` entries:
   ```
   /dev/sda1  /mnt/cloud-2  ext4  defaults  0 2
   /dev/sdb1  /mnt/cloud-1  ext4  defaults  0 2
   /mnt/cloud-1:/mnt/cloud-2  /mnt/cloud  fuse.mergerfs  allow_other,fsname=cloud,category.create=mfs,use_ino,dropcacheonclose=true,cache.files=partial  0 0
   ```
3. Install mergerfs: `apt-get install -y mergerfs`
4. `mount -a` and verify `/mnt/cloud` shows ~4.5 TB combined.
5. Install the systemd unit for nvr-data activation (from the restored host-cfg-main/systemd-system/mnt-nvr-data.service).
   ```bash
   cp /recovery/var/backup-staging/host-cfg-main/systemd-system/mnt-nvr-data.service \
     /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable --now mnt-nvr-data.service
   ```

## Phase 4 — Restore host config

```bash
cp /recovery/var/backup-staging/host-cfg-main/fstab      /etc/fstab     # already done but verify
cp /recovery/var/backup-staging/host-cfg-main/hosts      /etc/hosts
cp -r /recovery/var/backup-staging/host-cfg-main/root-ssh  /root/.ssh
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys
crontab /recovery/var/backup-staging/host-cfg-main/root-crontab
```

## Phase 5 — Restore Proxmox cluster config

```bash
# /etc/pve is a fuse filesystem backed by /var/lib/pve-cluster. Restoring
# /etc/pve/lxc/<id>.conf files lets us re-declare CT definitions.
for CONF in /recovery/var/backup-staging/pve-main/lxc/*.conf; do
  cp "$CONF" /etc/pve/lxc/
done
```

(If the cluster is entirely new, you may need to manually create storage pool definitions in `/etc/pve/storage.cfg` — reference `docs/hardware.md` for pool names.)

## Phase 6 — Restore bind-mount data

```bash
# Immich
rsync -a /recovery/backup-sources/immich/ /mnt/cloud/volumes/mediaserver/immich/

# Samba personal
rsync -a /recovery/backup-sources/samba-psy/ /mnt/cloud/volumes/samba/data/psy/
rsync -a /recovery/backup-sources/samba-config/ /mnt/cloud/volumes/samba/config/

# *arr configs
for ARR in sonarr radarr prowlarr deluge flaresolverr; do
  rsync -a "/recovery/backup-sources/arr-configs/$ARR/" "/mnt/cloud/volumes/mediaserver/$ARR/config/"
done

# Jellyfin config
rsync -a /recovery/backup-sources/jellyfin-config/ /mnt/cloud/volumes/mediaserver/jellyfin/config/

# Frigate config
rsync -a /recovery/backup-sources/frigate-config/ /mnt/nvr-data/config/
```

## Phase 7 — Restore Immich Postgres

The Immich Postgres runs inside ct-photos after that CT is bootstrapped (Phase 8). Defer the Postgres restore until ct-photos is up:

```bash
# After ct-photos is running:
gunzip -c /recovery/var/backup-staging/immich-postgres.sql.gz \
  | pct exec 106 -- docker exec -i immich-postgres psql -U postgres immich
```

## Phase 8 — Clone repo and bootstrap CTs

```bash
cd /root
git clone git@github.com:psychonaut0/infra.git
cd infra

# Restore .env files into the repo's stacks dir (gitignored, so not in git)
for CT in ct-dns ct-tunnel ct-nvr ct-media ct-photos ct-files ct-mgmt; do
  cp -r /recovery/var/backup-staging/env/$CT/opt/stacks/$CT/* stacks/$CT/ 2>/dev/null || true
done

# Bootstrap each CT — order matters: dns first (for name resolution), then mgmt, then others
./scripts/bootstrap-ct.sh ct-dns    --restore-volumes-from /recovery/var/backup-staging/volumes
./scripts/bootstrap-ct.sh ct-mgmt   --restore-volumes-from /recovery/var/backup-staging/volumes
./scripts/bootstrap-ct.sh ct-tunnel --restore-volumes-from /recovery/var/backup-staging/volumes
./scripts/bootstrap-ct.sh ct-files  --restore-volumes-from /recovery/var/backup-staging/volumes
./scripts/bootstrap-ct.sh ct-media
./scripts/bootstrap-ct.sh ct-photos
./scripts/bootstrap-ct.sh ct-nvr
```

## Phase 9 — Recreate ct-backup

```bash
# Manually create ct-backup LXC (config exists at /etc/pve/lxc/109.conf already)
pct start 109

# Inside ct-backup, reinstall base tools + secrets
ssh root@ct-backup 'apt-get update && apt-get install -y restic rsync openssh-client cron caddy curl jq postgresql-client'

# Restore /etc/restic/ contents manually from vault (three files):
# /etc/restic/password, /etc/restic/b2.env, /etc/restic/telegram.env

# Redeploy scripts + systemd units from repo
scp stacks/ct-backup/scripts/*.sh root@ct-backup:/usr/local/bin/
scp stacks/ct-backup/systemd/*    root@ct-backup:/etc/systemd/system/
ssh root@ct-backup 'chmod 755 /usr/local/bin/*.sh && systemctl daemon-reload && systemctl enable --now backup.timer backup-prune.timer backup-check.timer'

# Redeploy Caddyfile for status endpoint
scp stacks/ct-backup/Caddyfile root@ct-backup:/etc/caddy/Caddyfile
ssh root@ct-backup 'systemctl enable --now caddy'
```

## Phase 10 — Verification

1. Open https://status.lan — all services green (or recovering).
2. Open https://immich.lan — photos visible.
3. Open https://jellyfin.lan — accounts + libraries present (libraries show empty media folders, expected).
4. Open https://sonarr.lan etc. — quality profiles + indexers present.
5. Open https://pihole.lan — custom DNS list + admin password restored.
6. Run a manual backup to confirm the new ct-backup can reach B2:
   `ssh root@ct-backup /usr/local/bin/backup.sh`

## Estimated timing

| Phase | Time |
|---|---|
| 1–2 (install + pull snapshot) | 3h (mostly download) |
| 3 (storage) | 1h |
| 4–5 (host + PVE config) | 30m |
| 6 (bulk data restore) | 2–3h |
| 7 (Postgres restore, after ct-photos up) | 10m |
| 8 (bootstrap CTs) | 1h |
| 9 (ct-backup) | 30m |
| 10 (verification) | 30m |
| **Total hands-on** | **~5–7h** |
```

- [ ] **Step 2: Commit**

```bash
cd /home/psy/Documents/personal/ops/infra
git add docs/recovery.md
git commit -m "Add disaster recovery runbook"
```

---

## Task 19: Update MEMORY.md with completed backup system

**Goal:** Save a project memory that the backup system is live, so future conversations know the current state.

**Files:**
- Create: `memory/project_backup_system.md`
- Modify: `memory/MEMORY.md`

- [ ] **Step 1: Write memory file**

Create `/home/psy/.claude/projects/-home-psy-Documents-personal-ops-infra/memory/project_backup_system.md`:
```markdown
---
name: Off-site backup system live
description: ct-backup LXC (VMID 109, 192.168.3.13) runs nightly restic to Backblaze B2; three-layer model (git/restic/B2) covers all non-expendable state
type: project
---

Off-site backup system live as of YYYY-MM-DD. Architecture per `docs/superpowers/specs/2026-04-17-offsite-backup-design.md`.

**Key facts:**
- ct-backup LXC on proxmoxmain: unprivileged, 1 vCPU / 512 MB / 4 GB, IP 192.168.3.13
- restic → Backblaze B2 bucket `blvck-homelab-backup` via S3-compatible endpoint (portable)
- Nightly 03:00, weekly prune Sun 04:00, monthly partial check 1st @ 05:00
- Retention: 7d / 4w / 12m / 2y
- Monitoring: Telegram trap on fail + Gatus endpoint `http://backup.lan/status.json`
- DR path documented at `docs/recovery.md` — target ≤1 weekend hands-on
- Scope excludes: media library files, Frigate recordings, samba/data/family/*, HAOS (until container migration)

**How to apply:**
- When advising on infra changes, remember backups exist — tell user when a change might affect backup scope (e.g., adding a new CT, new Docker volume, new bind mount).
- Before risky operations, suggest running `ssh root@ct-backup /usr/local/bin/backup.sh` manually to snapshot current state.
- When HA container migration happens, remember to add its paths to ct-backup's scope.
```

- [ ] **Step 2: Add pointer in MEMORY.md**

Append to `/home/psy/.claude/projects/-home-psy-Documents-personal-ops-infra/memory/MEMORY.md`:
```
- [Backup system live](project_backup_system.md) — ct-backup LXC + restic + B2 off-site, nightly; covers all non-expendable state
```

- [ ] **Step 3: Nothing to commit in this repo — memory lives outside**

---

## Task 20: Disaster recovery drill

**Goal:** Prove the system works by doing a partial restore to a scratch location. This is the most important verification step — without it, the backup exists only on paper.

**Files:** None

- [ ] **Step 1: Create a scratch directory on ct-backup**

```bash
ssh root@ct-backup 'mkdir -p /tmp/restore-drill && cd /tmp/restore-drill'
```

- [ ] **Step 2: Restore a small, easily-verified item — the Pi-hole volumes**

```bash
ssh root@ct-backup 'source /etc/restic/b2.env && \
  restic restore latest --target /tmp/restore-drill \
    --include /var/backup-staging/volumes/ct-dns/'
```

- [ ] **Step 3: Verify file integrity**

```bash
ssh root@ct-backup 'ls -la /tmp/restore-drill/var/backup-staging/volumes/ct-dns/'
```
Expected: `pihole-data.tar.gz`, `pihole-dns.tar.gz` (or whatever named volumes exist).

- [ ] **Step 4: Round-trip a named volume**

Pick a safe CT volume — `filebrowser-db` on ct-files is a good candidate since filebrowser is not critical:

```bash
# Restore the tarball from ct-backup to a scratch location:
ssh root@ct-backup 'source /etc/restic/b2.env && \
  restic dump latest /var/backup-staging/volumes/ct-files/filebrowser-db.tar.gz \
    > /tmp/filebrowser-db.tar.gz'

# Copy to a temp location on ct-files:
scp root@ct-backup:/tmp/filebrowser-db.tar.gz /tmp/
scp /tmp/filebrowser-db.tar.gz root@ct-files:/tmp/

# Inspect contents (don't overwrite the live volume):
ssh root@ct-files 'tar tzf /tmp/filebrowser-db.tar.gz | head -20'
```
Expected: see the filebrowser SQLite DB file and any other state.

- [ ] **Step 5: Verify Immich Postgres dump restores cleanly**

This tests the most complex piece — a live DB dump. Restore to a scratch Postgres:

```bash
# On ct-backup, extract the dump:
ssh root@ct-backup 'source /etc/restic/b2.env && \
  restic dump latest /var/backup-staging/immich-postgres.sql.gz > /tmp/immich-dump.sql.gz'

# Inspect:
ssh root@ct-backup 'gunzip -c /tmp/immich-dump.sql.gz | head -50'
```
Expected: Postgres dump header + `CREATE TABLE` statements.

- [ ] **Step 6: Restore the /etc/pve contents and compare to live**

```bash
ssh root@ct-backup 'source /etc/restic/b2.env && \
  restic restore latest --target /tmp/restore-drill \
    --include /var/backup-staging/pve-main/'

# Compare:
ssh root@ct-backup 'diff -r /tmp/restore-drill/var/backup-staging/pve-main/ <(ssh root@proxmoxmain "cat /etc/pve/lxc/*.conf") | head'
```
Any diff indicates staleness between last backup and current live state — expected and OK as long as the structure matches.

- [ ] **Step 7: Clean up scratch**

```bash
ssh root@ct-backup 'rm -rf /tmp/restore-drill /tmp/immich-dump.sql.gz /tmp/filebrowser-db.tar.gz'
ssh root@ct-files 'rm /tmp/filebrowser-db.tar.gz'
```

- [ ] **Step 8: Record the drill date in the memory file**

Update `memory/project_backup_system.md` with a line like:
```
**Last DR drill:** YYYY-MM-DD — filebrowser-db volume + Immich pg_dump + /etc/pve all round-tripped cleanly.
```

- [ ] **Step 9: Commit nothing in this repo — memory file is outside. Schedule the next drill on your calendar (e.g., quarterly).**

---

## Post-Implementation Checklist

After all 20 tasks complete, verify:

- [ ] `restic snapshots` shows at least one nightly snapshot per retention tier
- [ ] `systemctl list-timers backup*` on ct-backup shows three active timers with future runs
- [ ] Gatus dashboard (https://status.lan) shows "Backup freshness" endpoint green
- [ ] Telegram bot has received no unexpected alerts in 48 hours
- [ ] `docs/recovery.md` is checked into git
- [ ] `scripts/bootstrap-ct.sh` is checked into git
- [ ] Memory files updated with completion date
- [ ] Paper copy of DR credentials is in fireproof storage (verified physically)
- [ ] Next quarterly DR drill scheduled

## What This Plan Does NOT Implement

Deliberately out of scope per the spec:

- Unified `infra` CLI with `backup`/`restore` commands — separate future project (see `project_infra_cli_idea.md` memory)
- HA container migration + HA data in backup scope — separate future project
- Mergerfs RAID / HDD redundancy — different concern
- Proxmox Backup Server — evaluated and rejected in favor of full-restic model
