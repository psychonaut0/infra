# ct-files Web Drive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace FileBrowser on ct-files with two copyparty containers — one per uid — giving `psy` and `family` a Drive-like web UI over their existing Samba trees, reachable publicly via the Cloudflare Tunnel, while Samba keeps full read-write access to the same plain files.

**Architecture:** Two `copyparty/ac` containers on ct-files. Each runs *as* the uid owning its tree (1000 / 1001), serves exactly one volume at the webroot with exactly one account, and has no root volume — so no user can address a path above their own tree. copyparty builds listings from live `os.scandir` per request and has no filesystem watching, so out-of-band Samba writes appear instantly. Public traffic goes cloudflared → copyparty directly; LAN traffic goes Caddy → copyparty. The two paths are deliberately not chained.

**Tech Stack:** `copyparty/ac` Docker image (Pillow + FFmpeg edition), Docker Compose, Caddy (existing, on ct-mgmt), cloudflared (existing, on ct-tunnel), Gatus (existing), restic via ct-backup (existing).

**Spec:** `docs/superpowers/specs/2026-07-28-ct-files-web-drive-design.md`

## Global Constraints

- **Never run copyparty as root.** ct-files is a privileged LXC, so container uid 0 is host uid 0. Each container runs as an explicit non-root `user:`.
- **uid must be pinned before accounts are created.** The argon2 salt is stored in `/cfg` and is uid-sensitive; changing a container's `user:` after accounts exist silently invalidates every password hash (upstream issue #1421, reported from a Debian 13 Proxmox LXC).
- **`ah-alg: argon2` is mandatory.** The default is `none` = plaintext passwords in the config file.
- **`xff-src` must be the two specific proxy IPs — never `lan`.** copyparty stays directly reachable on the LAN, so a `lan`-wide trust lets any LAN host forge `cf-connecting-ip` to evade a ban or ban a third party.
- **`rproxy` must be set.** It defaults to `9999999` (deliberately out of bounds), so real-IP detection fails behind any proxy until configured.
- **SQLite must not live on the mergerfs pool.** `dbpath` goes on the CT rootfs (upstream issue #919: "database disk image is malformed" on a FUSE mount).
- **FTP and `dk`/dirkeys stay off.** Both are default-off; simply never enable them. The 2026-07-27 advisory is an FTP volume-jail escape, and `dk` bypasses sibling-volume permission filtering.
- **Dedup stays off.** `--dedup`/`--hardlink`/`--reflink` are all default-off. Hardlinks across mergerfs branches are unsafe.
- **Never point `dbpath` or `hist` inside `/opt/stacks/`.** Task 14 adds ct-files to ct-backup's full-`/opt/stacks` rsync; regenerable cache there would be shipped to B2 nightly.
- **Samba is not modified.** No changes to `smb.conf`, shares, masks, or recycle behaviour at any point.
- Host paths, fixed: `psy` tree = `/mnt/cloud/volumes/samba/data/psy` (uid/gid 1000), `family` tree = `/mnt/cloud/volumes/samba/data/family` (uid/gid 1001).
- Ports, fixed: psy = host `3923`, family = host `3924`. Both listen on `3923` inside their container.

---

## File Structure

**Create (repo):**
- `stacks/ct-files/copyparty/psy/copyparty.conf.example` — committed template for the psy instance, placeholder hash.
- `stacks/ct-files/copyparty/family/copyparty.conf.example` — same, for family.
- `stacks/ct-files/.gitignore` — ignore the real `copyparty/*/copyparty.conf`.
- `stacks/ct-files/README.md` — deploy notes, hash-regeneration runbook, the salt trap.

**Modify (repo):**
- `stacks/ct-files/docker-compose.yml` — add two copyparty services; later remove `filebrowser`.
- `stacks/ct-mgmt/Caddyfile` — add `drive.lan` + `family.lan` with the header override; remove `files.lan`.
- `stacks/ct-mgmt/gatus/config.yaml` — replace the FileBrowser check with two checks.
- `stacks/ct-mgmt/dashboard-src/src/services.js` — replace the FileBrowser tile with two tiles.
- `stacks/ct-backup/scripts/pre-backup.sh` — add ct-files to the full-`/opt/stacks` rsync list.
- `CLAUDE.md` — ct-files role, ports, resources; Services section.
- `docs/hardware.md` — ct-files storage row.

**Create (on ct-files, not in repo):**
- `/opt/stacks/ct-files/copyparty/psy/copyparty.conf` — owned `1000:1000`, mode 0600. Also receives the argon2 salt and `sessions.db` at runtime.
- `/opt/stacks/ct-files/copyparty/family/copyparty.conf` — owned `1001:1001`, mode 0600.
- `/var/lib/copyparty/psy/` — owned `1000:1000`. Holds `dbpath` + `hist`. Regenerable, deliberately outside `/opt/stacks` so it is never backed up.
- `/var/lib/copyparty/family/` — owned `1001:1001`. Same.

**Manual ops (no commit):**
- `pct resize` / `pct set` on proxmoxmain (Task 2).
- Two Cloudflare Zero Trust dashboard ingress rules (Task 11) — produces **no repo diff**.
- Deploy `pre-backup.sh` to `/usr/local/bin/` on ct-backup and run a real cycle (Task 14).

---

## Task 1: Pre-flight checks

**Goal:** Verify every precondition. If any check fails, stop and resolve before continuing.

**Files:** None (verification only)

- [ ] **Step 1: local-lvm has room for a +12G rootfs resize**

```bash
ssh proxmoxmain 'pvesm status | grep local-lvm'
```
Expected: `Avail` ≥ **20 GB**. The resize needs 12 GB; leave headroom.

- [ ] **Step 2: proxmoxmain has RAM for a +1024MB bump**

```bash
ssh proxmoxmain 'free -h'
```
Expected: `available` ≥ **3 GB**.

- [ ] **Step 3: proxmoxmain has spare cores**

```bash
ssh proxmoxmain 'nproc && uptime'
```
Expected: `nproc` = 12, load average well under 12. ct-files goes 1 → 2 cores.

- [ ] **Step 4: ports 3923 and 3924 are free on ct-files**

```bash
ssh ct-files 'ss -tlnp | grep -E ":(3923|3924) " || echo "both free"'
```
Expected: `both free`.

- [ ] **Step 5: confirm the two trees and their ownership**

```bash
ssh ct-files 'stat -c "%n %u:%g %a" /mnt/cloud/volumes/samba/data/psy /mnt/cloud/volumes/samba/data/family'
```
Expected:
```
/mnt/cloud/volumes/samba/data/psy 1000:1000 775
/mnt/cloud/volumes/samba/data/family 1001:1001 770
```
If the uids differ, **stop** — the whole design keys on 1000/1001.

- [ ] **Step 6: cloudflared is up and remote-managed**

```bash
ssh ct-tunnel 'docker ps --format "{{.Names}}\t{{.Status}}" | grep cloudflared'
```
Expected: `cloudflared	Up <duration>`. Ingress rules live in the Cloudflare Zero Trust dashboard (`TUNNEL_TOKEN`), not a local file.

- [ ] **Step 7: Caddy is up on ct-mgmt**

```bash
ssh ct-mgmt 'docker ps --format "{{.Names}}\t{{.Status}}" | grep caddy'
```
Expected: `caddy	Up <duration>`.

- [ ] **Step 8: `infra` CLI is available**

```bash
infra --version && infra ls | head -5
```
Expected: a version string and a service listing.

- [ ] **Step 9: record the current FileBrowser state for rollback**

```bash
ssh ct-files 'docker ps --format "{{.Names}}\t{{.Image}}\t{{.Status}}"'
```
Expected: `samba`, `filebrowser`, `portainer-agent` all `Up`. Note this — Task 15 removes `filebrowser` and this is the pre-change baseline.

---

## Task 2: Resize rootfs and bump resources on ct-files

**Goal:** 4 GB → 16 GB rootfs, 1 → 2 cores, 1024 → 2048 MB RAM. The disk is a hard blocker (1.9 GB free cannot hold two indexes plus a thumbnail cache); CPU and RAM prevent thumbnailing from starving `smbd`.

**Files:** None in repo (PVE config change on proxmoxmain)

- [ ] **Step 1: Record the current state**

```bash
ssh proxmoxmain 'pct config 107 | grep -E "^(cores|memory|rootfs|swap)"'
```
Expected:
```
cores: 1
memory: 1024
rootfs: local-lvm:vm-107-disk-0,size=4G
swap: 512
```

- [ ] **Step 2: Grow the rootfs (online, no reboot needed)**

```bash
ssh proxmoxmain 'pct resize 107 rootfs +12G'
```
Expected: `Size of logical volume pve/vm-107-disk-0 changed ...` then a resize2fs line ending in `now <N> (4k) blocks long`.

- [ ] **Step 3: Confirm the filesystem sees the new space**

```bash
ssh ct-files 'df -h /'
```
Expected: `Size` ≈ **16G**, `Avail` ≈ **14G**.

- [ ] **Step 4: Bump cores and memory**

```bash
ssh proxmoxmain 'pct set 107 -cores 2 -memory 2048'
```
Expected: no output.

- [ ] **Step 5: Reboot the CT to apply the core count**

```bash
ssh proxmoxmain 'pct reboot 107'
sleep 25
ssh ct-files 'nproc && free -m | head -2'
```
Expected: `2`, and `Mem: total` ≈ **2048**.

- [ ] **Step 6: Confirm Samba came back**

```bash
ssh ct-files 'docker ps --format "{{.Names}}\t{{.Status}}"'
```
Expected: `samba`, `filebrowser`, `portainer-agent` all `Up`. If Samba is unhealthy, resolve before continuing.

- [ ] **Step 7: Verify copyparty will see 2 cores, not the host's 12**

```bash
ssh ct-files 'python3 -c "import os; print(len(os.sched_getaffinity(0)))"'
```
Expected: `2`. copyparty derives its worker counts from `sched_getaffinity`, so this confirms it will auto-tune to the cgroup rather than to proxmoxmain's 12 threads.

---

## Task 3: Create host directories with correct ownership

**Goal:** Two config dirs (which double as each container's `/cfg`, so they must be writable by the container uid for the salt and `sessions.db`) and two cache dirs outside `/opt/stacks`.

**Files:** None in repo (directories on ct-files)

- [ ] **Step 1: Create the config directories**

```bash
ssh ct-files 'install -d -o 1000 -g 1000 -m 0700 /opt/stacks/ct-files/copyparty/psy
install -d -o 1001 -g 1001 -m 0700 /opt/stacks/ct-files/copyparty/family'
```
Expected: no output.

- [ ] **Step 2: Create the cache directories**

```bash
ssh ct-files 'install -d -o 1000 -g 1000 -m 0700 /var/lib/copyparty/psy
install -d -o 1001 -g 1001 -m 0700 /var/lib/copyparty/family'
```
Expected: no output. These hold `dbpath` and `hist`. They live outside `/opt/stacks` on purpose — Task 14 adds `/opt/stacks/ct-files` to the nightly backup rsync, and thumbnails are regenerable.

- [ ] **Step 3: Verify ownership**

```bash
ssh ct-files 'stat -c "%n %u:%g %a" /opt/stacks/ct-files/copyparty/psy /opt/stacks/ct-files/copyparty/family /var/lib/copyparty/psy /var/lib/copyparty/family'
```
Expected:
```
/opt/stacks/ct-files/copyparty/psy 1000:1000 700
/opt/stacks/ct-files/copyparty/family 1001:1001 700
/var/lib/copyparty/psy 1000:1000 700
/var/lib/copyparty/family 1001:1001 700
```

---

## Task 4: Write the config templates and gitignore

**Goal:** Commit `.example` templates carrying every flag, and gitignore the real configs (which will hold argon2 hashes — this repo is sanitized for public release and already gitignores the Mosquitto `passwd` file).

**Files:**
- Create: `stacks/ct-files/copyparty/psy/copyparty.conf.example`
- Create: `stacks/ct-files/copyparty/family/copyparty.conf.example`
- Create: `stacks/ct-files/.gitignore`

- [ ] **Step 1: Write the psy template**

Create `stacks/ct-files/copyparty/psy/copyparty.conf.example`:

```
# copyparty — psy instance. Runs as uid 1000, serves the psy Samba tree.
# Deployed to /opt/stacks/ct-files/copyparty/psy/copyparty.conf on ct-files.
# This directory is also the container's /cfg, so it receives the argon2 salt
# and sessions.db at runtime. Do NOT change the container's `user:` after
# accounts exist — the salt is uid-sensitive and every hash silently breaks.

[global]
  # --- listen ---
  p: 3923

  # --- indexing ---
  # e2dsa enables the filesystem scan + index; e2ts indexes media tags.
  # Listings do NOT come from this index (they are live os.scandir per
  # request), so Samba writes show up instantly regardless. The index is
  # only used for search.
  e2dsa
  e2ts
  # Index name/size/mtime only — never content-hash. Without this the first
  # scan reads all 51 GB through FUSE on one core. Full search still works;
  # only search-by-content-hash and dedup are lost, and neither is wanted.
  no-hash: .
  # Do not index Samba's recycle bin (17 GB here) or our own cache dir.
  no-idx: (^|/)\.(deleted|hist)/
  # Re-scan hourly to keep search fresh. copyparty has no inotify at all,
  # which is exactly why mergerfs's unreliable FUSE inotify is a non-issue.
  re-maxage: 3600

  # --- index + thumbnail cache locations ---
  # dbpath MUST be on the CT rootfs, never the mergerfs pool: SQLite on FUSE
  # corrupts (upstream issue #919).
  dbpath: /var/copyparty/db
  hist: /var/copyparty/hist

  # --- thumbnails ---
  # One thumbnail thread. With 2 vCPU and two containers, the default of
  # CORES-per-container would put 4 threads on 2 cores with smbd competing.
  th-mt: 1
  th-maxage: 604800
  th-clean: 43200

  # --- auth ---
  # MANDATORY. The default is `none`, i.e. plaintext passwords in this file.
  ah-alg: argon2

  # --- reverse-proxy real IP ---
  # Load-bearing: every ban rule keys on client IP. Trusted upstreams are
  # cloudflared (.6, public path) and Caddy (.12, LAN path) ONLY. Never `lan`
  # — copyparty is directly reachable on the LAN, so a lan-wide trust would
  # let any host forge this header to evade a ban or ban a third party.
  xff-hdr: cf-connecting-ip
  xff-src: 192.168.3.6, 192.168.3.12
  # Defaults to 9999999 (deliberately out of bounds); must be set explicitly.
  rproxy: 1

  # --- advisory feed ---
  # Default-disabled. This is the early-warning channel for new CVEs.
  # Note: do NOT add `vc-exit` (from upstream's sample config) — it shuts the
  # server down on a warning instead of just displaying it.
  vc-url: https://api.copyparty.eu/advisories

  # --- ui ---
  grid

[accounts]
  # Generate with the runbook in stacks/ct-files/README.md. The hash is only
  # valid against the salt in THIS directory.
  psy: REPLACE_WITH_ARGON2_HASH

[/]
  /w
  accs:
    rwmd: psy
  flags:
    # Matches the modes already present on this tree (644 files / 755 dirs).
    chmod_f: 644
    chmod_d: 755
```

- [ ] **Step 2: Write the family template**

Create `stacks/ct-files/copyparty/family/copyparty.conf.example` — identical to the psy template except for the header comment, the `no-hash`/tree-size comment, the account, and the modes. Full content:

```
# copyparty — family instance. Runs as uid 1001, serves the family Samba tree.
# Deployed to /opt/stacks/ct-files/copyparty/family/copyparty.conf on ct-files.
# This directory is also the container's /cfg, so it receives the argon2 salt
# and sessions.db at runtime. Do NOT change the container's `user:` after
# accounts exist — the salt is uid-sensitive and every hash silently breaks.

[global]
  # --- listen ---
  p: 3923

  # --- indexing ---
  e2dsa
  e2ts
  # Index name/size/mtime only. This tree is 281 GB / 91,212 files; content
  # hashing it through FUSE on one core would take many hours.
  no-hash: .
  # Do not index Samba's recycle bin (21 GB here) or our own cache dir.
  no-idx: (^|/)\.(deleted|hist)/
  re-maxage: 3600

  # --- index + thumbnail cache locations ---
  dbpath: /var/copyparty/db
  hist: /var/copyparty/hist

  # --- thumbnails ---
  th-mt: 1
  th-maxage: 604800
  th-clean: 43200

  # --- auth ---
  ah-alg: argon2

  # --- reverse-proxy real IP ---
  xff-hdr: cf-connecting-ip
  xff-src: 192.168.3.6, 192.168.3.12
  rproxy: 1

  # --- advisory feed ---
  vc-url: https://api.copyparty.eu/advisories

  # --- ui ---
  grid

[accounts]
  family: REPLACE_WITH_ARGON2_HASH

[/]
  /w
  accs:
    # Delete included, by explicit decision. copyparty has NO trash and a
    # delete here bypasses Samba's recycle vfs, so web deletes are permanent.
    # This tree also has no off-site backup — see spec Known limitations #8.
    rwmd: family
  flags:
    # Dirs match the tree exactly (770). Files use 660 rather than the tree's
    # mixed 770/764: identical effective access, without marking 66k photos
    # executable. No world bits on this tree.
    chmod_f: 660
    chmod_d: 770
```

- [ ] **Step 3: Write the gitignore**

Create `stacks/ct-files/.gitignore`:

```
# Real copyparty configs hold argon2 password hashes. This repo is sanitized
# for public release; only the .example templates are committed.
copyparty/*/copyparty.conf
```

- [ ] **Step 4: Verify the gitignore works**

```bash
cd /home/psy/Documents/personal/infra
cp stacks/ct-files/copyparty/psy/copyparty.conf.example stacks/ct-files/copyparty/psy/copyparty.conf
git status --porcelain stacks/ct-files/
```
Expected: the `.example` files and `.gitignore` show as untracked (`??`), but **`copyparty/psy/copyparty.conf` does NOT appear**. Then clean up:
```bash
rm stacks/ct-files/copyparty/psy/copyparty.conf
```

- [ ] **Step 5: Commit**

```bash
cd /home/psy/Documents/personal/infra
git add stacks/ct-files/.gitignore stacks/ct-files/copyparty/
git commit -m "feat(ct-files): add copyparty config templates for psy + family

Two instances, one per uid, each serving a single volume at the webroot with
a single account and no root volume. Real configs are gitignored because they
carry argon2 hashes; only templates are committed.

Load-bearing settings documented inline: ah-alg (default is plaintext),
xff-src pinned to the two proxy IPs (a lan-wide trust is forgeable and every
ban rule keys on client IP), rproxy (defaults out of bounds), dbpath off FUSE,
and no-idx excluding the 38GB of Samba recycle data."
```

---

## Task 5: Add the two copyparty services to compose

**Goal:** Add both containers alongside the still-running FileBrowser, so this is a parallel run rather than a cutover.

**Files:**
- Modify: `stacks/ct-files/docker-compose.yml`

**Interfaces:**
- Produces: containers `copyparty-psy` (host :3923) and `copyparty-family` (host :3924), consumed by Tasks 6–15.

- [ ] **Step 1: Add both services**

In `stacks/ct-files/docker-compose.yml`, insert after the `samba` service and before `portainer-agent`:

```yaml
  copyparty-psy:
    image: copyparty/ac:latest
    container_name: copyparty-psy
    restart: unless-stopped
    user: "1000:1000"
    mem_limit: 640m
    security_opt:
      - no-new-privileges:true
    ports:
      - "3923:3923"
    volumes:
      - /mnt/cloud/volumes/samba/data/psy:/w
      - /opt/stacks/ct-files/copyparty/psy:/cfg
      - /var/lib/copyparty/psy:/var/copyparty
    environment:
      TZ: Europe/Rome

  copyparty-family:
    image: copyparty/ac:latest
    container_name: copyparty-family
    restart: unless-stopped
    user: "1001:1001"
    mem_limit: 640m
    security_opt:
      - no-new-privileges:true
    ports:
      - "3924:3923"
    volumes:
      - /mnt/cloud/volumes/samba/data/family:/w
      - /opt/stacks/ct-files/copyparty/family:/cfg
      - /var/lib/copyparty/family:/var/copyparty
    environment:
      TZ: Europe/Rome
```

Note: no `command:` — the image auto-loads `/cfg/*.conf`. Both containers listen on 3923 internally; only the host-side mapping differs.

- [ ] **Step 2: Validate the compose file**

```bash
cd /home/psy/Documents/personal/infra
docker compose -f stacks/ct-files/docker-compose.yml config -q && echo "compose OK"
```
Expected: `compose OK`. (If the local Docker cannot resolve the bind paths, run the same check on ct-files after Step 3 instead.)

- [ ] **Step 3: Commit**

```bash
git add stacks/ct-files/docker-compose.yml
git commit -m "feat(ct-files): add copyparty-psy and copyparty-family services

Each runs as the uid owning its tree (1000/1001) so uploads land correctly by
nature — no chown, and no root. FileBrowser stays up for a parallel run and is
removed in a later step."
```

---

## Task 6: Generate argon2 hashes and start both containers

**Goal:** Create each instance's salt, generate a hash against that same salt, write the real configs, and bring both up.

**Ordering matters:** the salt lives in `/cfg` and is uid-sensitive. Generating a hash in a throwaway container with a different `/cfg` or a different uid produces a hash that will **not** validate. Every hash-generation command below mounts the *same* config directory and uses the *same* `user:` as the running container.

**Files:** None in repo (deploys the gitignored configs to ct-files)

- [ ] **Step 1: Copy the templates to ct-files as the real configs**

```bash
cd /home/psy/Documents/personal/infra
scp stacks/ct-files/copyparty/psy/copyparty.conf.example \
    root@ct-files:/opt/stacks/ct-files/copyparty/psy/copyparty.conf
scp stacks/ct-files/copyparty/family/copyparty.conf.example \
    root@ct-files:/opt/stacks/ct-files/copyparty/family/copyparty.conf
ssh ct-files 'chown 1000:1000 /opt/stacks/ct-files/copyparty/psy/copyparty.conf
chmod 0600 /opt/stacks/ct-files/copyparty/psy/copyparty.conf
chown 1001:1001 /opt/stacks/ct-files/copyparty/family/copyparty.conf
chmod 0600 /opt/stacks/ct-files/copyparty/family/copyparty.conf'
```
Expected: no output.

- [ ] **Step 2: Pull the image**

```bash
ssh ct-files 'docker pull copyparty/ac:latest && docker image inspect copyparty/ac:latest --format "{{.RepoDigests}} {{.Size}}"'
```
Expected: a digest and a size around 163 MB. **Record the digest** — Task 16 pins it in the docs so a future `docker pull` is a deliberate act.

- [ ] **Step 2a: Exclude PSD from thumbnailing**

141 layered Photoshop files live in the family tree (`Immagini lavori PSD/`), and `psd` appears in **both** default decoder lists — `--th-r-pil` (Pillow) and `--th-r-ffi` (FFmpeg). A 70 MB layered PSD decodes to well over 1 GB, inside a 640 MB `mem_limit`. Excluding them is better than letting the memory ceiling catch it, because hitting `mem_limit` OOM-kills the whole container.

The default lists change between versions, so read them from the image rather than hardcoding:

```bash
ssh ct-files 'docker run --rm copyparty/ac:latest --help 2>&1 | grep -A3 -E "th-r-pil|th-r-ffi"'
```

Expected: two lines showing comma-separated extension lists with `psd` present in each. Copy each list verbatim, delete `psd` from it, and add both to the `[global]` block of **each** instance's `copyparty.conf` (both trees, so a PSD dropped into either is covered):

```
  # psd removed from both decoder lists — layered PSDs decode to >1GB and
  # would OOM-kill the container inside its 640m mem_limit. Lists copied
  # verbatim from `--help` for this image version, minus `psd`. Re-check
  # after a major image upgrade.
  th-r-pil: <default list, minus psd>
  th-r-ffi: <default list, minus psd>
```

If a future upgrade adds new extensions, these pinned lists will silently omit them — that is the accepted cost, noted in the runbook.

- [ ] **Step 2b: Confirm nothing else was accidentally enabled**

```bash
ssh ct-files 'grep -nE "^\s*(ftp|dk|dedup|hardlink|reflink|vc-exit|th-bwrap|usernames|pw-urlp)" \
  /opt/stacks/ct-files/copyparty/psy/copyparty.conf \
  /opt/stacks/ct-files/copyparty/family/copyparty.conf || echo "none present — correct"'
```
Expected: `none present — correct`. All of these must stay at their defaults:
- `ftp`, `dk` — recent advisories hit exactly these paths.
- `dedup`/`hardlink`/`reflink` — unsafe across mergerfs branches.
- `vc-exit` — appears in upstream's sample config and would shut the server down on an advisory warning.
- `th-bwrap` — bubblewrap generally fails inside LXC.
- `usernames`, `pw-urlp` — deliberately left at defaults per the owner's "start simple" decision (see spec, *Deliberately minimal, by decision*). Do not add them.

- [ ] **Step 3: Generate the psy hash**

```bash
ssh -t ct-files 'docker run --rm -it \
  --user 1000:1000 \
  -v /opt/stacks/ct-files/copyparty/psy:/cfg \
  copyparty/ac:latest --ah-alg argon2 --ah-cli'
```
Enter the chosen psy password when prompted. Expected: the tool prints a hash beginning `+` (copyparty's hashed-password marker). Copy it.

If this errors with a permission problem writing the salt, re-check Task 3 Step 3 — `/opt/stacks/ct-files/copyparty/psy` must be owned `1000:1000`.

- [ ] **Step 4: Confirm the salt was created and is owned correctly**

```bash
ssh ct-files 'ls -la /opt/stacks/ct-files/copyparty/psy/'
```
Expected: a salt file present, owned `1000:1000`. **This file is now load-bearing** — it must never be deleted, and the container's `user:` must never change.

- [ ] **Step 5: Write the psy hash into the config**

```bash
ssh ct-files 'sed -i "s|^  psy: REPLACE_WITH_ARGON2_HASH|  psy: <PASTE_HASH_HERE>|" \
  /opt/stacks/ct-files/copyparty/psy/copyparty.conf
grep -c "REPLACE_WITH_ARGON2_HASH" /opt/stacks/ct-files/copyparty/psy/copyparty.conf'
```
Expected: `0` (placeholder gone).

- [ ] **Step 6: Repeat Steps 3–5 for family**

```bash
ssh -t ct-files 'docker run --rm -it \
  --user 1001:1001 \
  -v /opt/stacks/ct-files/copyparty/family:/cfg \
  copyparty/ac:latest --ah-alg argon2 --ah-cli'
```
Then:
```bash
ssh ct-files 'sed -i "s|^  family: REPLACE_WITH_ARGON2_HASH|  family: <PASTE_HASH_HERE>|" \
  /opt/stacks/ct-files/copyparty/family/copyparty.conf
grep -c "REPLACE_WITH_ARGON2_HASH" /opt/stacks/ct-files/copyparty/family/copyparty.conf'
```
Expected: `0`.

- [ ] **Step 7: Deploy the compose file and start both containers**

```bash
cd /home/psy/Documents/personal/infra
scp stacks/ct-files/docker-compose.yml root@ct-files:/opt/stacks/ct-files/docker-compose.yml
ssh ct-files 'cd /opt/stacks/ct-files && docker compose up -d copyparty-psy copyparty-family'
```
Expected: both containers created and started.

- [ ] **Step 8: Read the startup logs — this is where real-IP problems surface**

```bash
ssh ct-files 'docker logs copyparty-psy 2>&1 | tail -40'
```
Expected: no `WARNING` about `xff` or `rproxy`. copyparty prints the exact recommended flags for its detected topology if the configuration is wrong; if you see such a warning, fix the config and restart before continuing. Also confirm no plaintext-password warning appears (which would mean `ah-alg` did not take effect).

Repeat for `copyparty-family`.

- [ ] **Step 9: Confirm both are listening and Samba is unaffected**

```bash
ssh ct-files 'docker ps --format "{{.Names}}\t{{.Status}}" && ss -tlnp | grep -E ":(3923|3924) "'
```
Expected: `samba`, `filebrowser`, `portainer-agent`, `copyparty-psy`, `copyparty-family` all `Up`; both ports listening.

- [ ] **Step 10: Log in to both over the LAN by IP**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://192.168.3.11:3923/
curl -s -o /dev/null -w "%{http_code}\n" http://192.168.3.11:3924/
```
Expected: an auth-required response, **not** `200` with a directory listing. **Record the exact status code** — Task 13 needs it for the Gatus conditions.

Then log in through a browser at `http://192.168.3.11:3923/` as `psy` and `http://192.168.3.11:3924/` as `family`, confirming each shows only its own tree.

---

## Task 7: Verify upload ownership and modes

**Goal:** Prove the core correctness claim — web uploads land owned by the right uid with the right mode. This is the single most common failure mode for this class of app.

**Files:** None (verification, with a contingency)

- [ ] **Step 1: Upload a test file to the psy instance**

Through the browser at `http://192.168.3.11:3923/`, upload a small file named `_ownership-test-psy.txt`, and create a folder named `_ownership-test-psy-dir`.

- [ ] **Step 2: Check ownership and mode**

```bash
ssh ct-files 'stat -c "%n %u:%g %a" \
  "/mnt/cloud/volumes/samba/data/psy/_ownership-test-psy.txt" \
  "/mnt/cloud/volumes/samba/data/psy/_ownership-test-psy-dir"'
```
Expected:
```
/mnt/cloud/volumes/samba/data/psy/_ownership-test-psy.txt 1000:1000 644
/mnt/cloud/volumes/samba/data/psy/_ownership-test-psy-dir 1000:1000 755
```

- [ ] **Step 3: Repeat for family**

Upload `_ownership-test-family.txt` and create `_ownership-test-family-dir` at `http://192.168.3.11:3924/`, then:
```bash
ssh ct-files 'stat -c "%n %u:%g %a" \
  "/mnt/cloud/volumes/samba/data/family/_ownership-test-family.txt" \
  "/mnt/cloud/volumes/samba/data/family/_ownership-test-family-dir"'
```
Expected:
```
/mnt/cloud/volumes/samba/data/family/_ownership-test-family.txt 1001:1001 660
/mnt/cloud/volumes/samba/data/family/_ownership-test-family-dir 1001:1001 770
```

- [ ] **Step 4: Contingency — if the family file is `644` instead of `660`**

The uid will be correct regardless (the process runs as 1001), but the mode may not be if `chmod_f`/`chmod_d` only take effect when the `uid`/`gid` volflags are also set. The consequence is that family files would be **world-readable**, which this tree is not.

If Step 3 shows `644`/`755`, apply the umask fallback — add to the `copyparty-family` service in `stacks/ct-files/docker-compose.yml`:

```yaml
    entrypoint: ["/bin/sh", "-c", "umask 0007 && exec /usr/bin/python3 -m copyparty"]
```

Then redeploy and re-test:
```bash
cd /home/psy/Documents/personal/infra
scp stacks/ct-files/docker-compose.yml root@ct-files:/opt/stacks/ct-files/docker-compose.yml
ssh ct-files 'cd /opt/stacks/ct-files && docker compose up -d copyparty-family'
```
Repeat Step 3. Expected after the fallback: `660` / `770`. If the entrypoint path is wrong, find it with `ssh ct-files 'docker inspect copyparty-family --format "{{.Config.Entrypoint}} {{.Config.Cmd}}"'` and adapt.

- [ ] **Step 5: Clean up the test artifacts**

```bash
ssh ct-files 'rm -rf "/mnt/cloud/volumes/samba/data/psy/_ownership-test-psy.txt" \
  "/mnt/cloud/volumes/samba/data/psy/_ownership-test-psy-dir" \
  "/mnt/cloud/volumes/samba/data/family/_ownership-test-family.txt" \
  "/mnt/cloud/volumes/samba/data/family/_ownership-test-family-dir"'
```

- [ ] **Step 6: Commit — only if the Step 4 contingency was applied**

```bash
git add stacks/ct-files/docker-compose.yml
git commit -m "fix(ct-files): set umask 0007 on copyparty-family for 660/770 modes

chmod_f/chmod_d volflags did not take effect without the uid/gid volflags
(which require running as root). The umask achieves the same result: family
files stay group-private rather than world-readable."
```

---

## Task 8: Verify Samba interop in both directions

**Goal:** Prove the property the whole tool choice rests on — Samba and copyparty coexist as active writers on one tree.

**Files:** None (verification only)

- [ ] **Step 1: Samba write → instant web visibility**

From blvckmain, write a file into the psy share over SMB:
```bash
smbclient //192.168.3.11/psy -U psy -c 'put /etc/hostname _smb-to-web-test.txt'
```
Expected: `putting file ... as \_smb-to-web-test.txt`.

Immediately reload the web listing at `http://192.168.3.11:3923/` — **no rescan, no restart**. Expected: `_smb-to-web-test.txt` is present. Listings are built by live `os.scandir` per request, so this should be instantaneous.

- [ ] **Step 2: Web upload → in-place modification over SMB**

Upload `_web-to-smb-test.txt` through the psy web UI, then modify it in place over SMB:
```bash
echo "modified over smb" > /tmp/_mod.txt
smbclient //192.168.3.11/psy -U psy -c 'put /tmp/_mod.txt _web-to-smb-test.txt'
```
Expected: succeeds. This is the check that a wrong upload uid would break.

- [ ] **Step 3: Web upload → rename and delete over SMB**

```bash
smbclient //192.168.3.11/psy -U psy -c 'rename _web-to-smb-test.txt _renamed.txt'
smbclient //192.168.3.11/psy -U psy -c 'del _renamed.txt'
```
Expected: both succeed with no error.

- [ ] **Step 4: Repeat Steps 1–3 against the family share**

```bash
smbclient //192.168.3.11/family -U family -c 'put /etc/hostname _smb-to-web-test.txt'
```
Then upload via `http://192.168.3.11:3924/`, and modify/rename/delete over SMB as above. Expected: all succeed.

- [ ] **Step 5: Confirm the recycle bin is invisible in the web UI**

Browse and search both instances for `.deleted`. Expected: not present in listings or search results — copyparty hides dotfiles by default (`-ed` and `--dotsrch` are both off), so the 38 GB of Samba recycle data stays out of sight without extra config.

- [ ] **Step 6: Clean up**

```bash
smbclient //192.168.3.11/psy -U psy -c 'del _smb-to-web-test.txt'
smbclient //192.168.3.11/family -U family -c 'del _smb-to-web-test.txt'
```
Note: these SMB deletes land in `.deleted` (Samba's recycle), unlike web deletes.

---

## Task 9: Verify the per-user jail

**Goal:** Prove neither account can reach the other's tree or anything above it. The current FileBrowser exposes the whole `/mnt/cloud` pool; that is the defect being fixed.

**Files:** None (verification only)

- [ ] **Step 1: Confirm each instance's webroot is its own tree only**

Logged in as `psy` at `http://192.168.3.11:3923/`, confirm the root listing shows the psy tree's contents (`Cazzi miei`, `Documents`, `Shared`, `ha-backups`) and **no** `mediaserver`, `minecraft`, `games`, `satisfactory`, `dump`, or `lost+found`.

- [ ] **Step 2: Attempt traversal above the webroot**

```bash
curl -s -o /dev/null -w "%{http_code}\n" "http://192.168.3.11:3923/../"
curl -s -o /dev/null -w "%{http_code}\n" "http://192.168.3.11:3923/%2e%2e%2f"
curl -s -o /dev/null -w "%{http_code}\n" "http://192.168.3.11:3923/..%2f..%2fvolumes/"
```
Expected: every one is a 4xx (403/404/422) or a redirect back to the webroot — never a listing of `/mnt/cloud`. copyparty collapses `..` lexically before any filesystem mapping, so traversal is structurally prevented.

- [ ] **Step 3: Confirm cross-account isolation**

Attempt to log in to the family instance (`:3924`) with the `psy` credentials. Expected: rejected — the accounts are defined in separate configs with no shared users.

- [ ] **Step 4: Confirm the control panel leaks nothing**

As `psy`, open the control panel. Expected: only the single `/` volume is listed. No reference to the family tree or any other pool path.

---

## Task 10: LAN access via Caddy and Pi-hole

**Goal:** `drive.lan` → psy, `family.lan` → family, with Caddy overwriting `CF-Connecting-IP` so a LAN client cannot forge it.

**Files:**
- Modify: `stacks/ct-mgmt/Caddyfile`

- [ ] **Step 1: Add both hostnames**

```bash
infra dns add drive.lan http://192.168.3.11:3923
infra dns add family.lan http://192.168.3.11:3924
```
Expected: each reports the Caddy block and Pi-hole record added, then reloads both services.

- [ ] **Step 2: Add the header override to both blocks**

`infra dns` only appends plain `reverse_proxy` blocks, so edit `stacks/ct-mgmt/Caddyfile` by hand. Change the two new blocks to:

```
http://drive.lan {
	reverse_proxy 192.168.3.11:3923 {
		header_up CF-Connecting-IP {remote_host}
	}
}

http://family.lan {
	reverse_proxy 192.168.3.11:3924 {
		header_up CF-Connecting-IP {remote_host}
	}
}
```

This **overwrites** any client-supplied `CF-Connecting-IP` with the real LAN peer address. Without it, a LAN host could forge the header — Caddy is a trusted `xff-src`, so copyparty would believe it.

- [ ] **Step 3: Push the Caddyfile and reload**

```bash
cd /home/psy/Documents/personal/infra
scp stacks/ct-mgmt/Caddyfile root@ct-mgmt:/opt/stacks/ct-mgmt/Caddyfile
ssh ct-mgmt 'cd /opt/stacks/ct-mgmt && docker compose up -d caddy'
```
Expected: caddy recreated, no config error in `docker logs caddy`.

- [ ] **Step 4: Verify both hostnames resolve and serve**

```bash
curl -s -o /dev/null -w "drive.lan  %{http_code}\n" http://drive.lan/
curl -s -o /dev/null -w "family.lan %{http_code}\n" http://family.lan/
```
Expected: the same auth-required status recorded in Task 6 Step 10 for both.

- [ ] **Step 5: Verify the real LAN IP reaches copyparty**

```bash
ssh ct-files 'docker logs copyparty-psy 2>&1 | tail -5'
```
Expected: recent request lines show **your workstation's LAN IP** (192.168.1.110 or similar), **not** `192.168.3.12` (Caddy). If they show Caddy's IP, the `xff-src`/`xff-hdr` config or the `header_up` directive is wrong — fix it now, before public exposure, because every ban rule keys on this.

- [ ] **Step 6: Verify a forged header is rejected**

```bash
curl -s -o /dev/null -H "CF-Connecting-IP: 1.2.3.4" http://drive.lan/
ssh ct-files 'docker logs copyparty-psy 2>&1 | tail -3'
```
Expected: the log shows your real LAN IP, **not** `1.2.3.4`. Caddy discarded the forged value.

- [ ] **Step 7: Commit**

```bash
git add stacks/ct-mgmt/Caddyfile
git commit -m "feat(ct-mgmt): add drive.lan and family.lan for copyparty

Both blocks overwrite CF-Connecting-IP with {remote_host}. Caddy is a trusted
xff-src on the copyparty side, so without the override any LAN host could forge
the header to evade a ban or ban an arbitrary third party."
```

---

## Task 11: Public exposure via Cloudflare Tunnel

**Goal:** `drive.ncsp.dev` and `family.ncsp.dev` reachable from the internet, with the real public client IP visible to copyparty.

Public traffic goes cloudflared → copyparty **directly**, bypassing Caddy — matching the existing `portfolio.ncsp.dev` pattern. This is deliberate: chaining through Caddy would force it to either preserve a forgeable `CF-Connecting-IP` or overwrite the real client IP with cloudflared's own address.

**Files:** None in repo — **this task produces no diff.** The tunnel is token-managed, so ingress rules live only in the Cloudflare dashboard.

- [ ] **Step 1: Add both ingress rules in the Cloudflare Zero Trust dashboard**

Navigate to **Zero Trust → Networks → Tunnels → (the existing tunnel) → Public Hostnames**, and add:

| Subdomain | Domain | Type | URL |
|---|---|---|---|
| `drive` | `ncsp.dev` | HTTP | `192.168.3.11:3923` |
| `family` | `ncsp.dev` | HTTP | `192.168.3.11:3924` |

Leave TLS settings at their defaults (origin is plain HTTP inside the LAN).

- [ ] **Step 2: Confirm DNS records were created**

```bash
dig +short drive.ncsp.dev
dig +short family.ncsp.dev
```
Expected: Cloudflare proxy IPs (Cloudflare creates proxied CNAMEs automatically for tunnel hostnames). Unlike the Minecraft endpoint, these **should** be proxied (orange cloud) — this is HTTPS, which the free-tier proxy supports.

- [ ] **Step 3: Verify both are reachable from the internet**

```bash
curl -s -o /dev/null -w "drive  %{http_code}\n" https://drive.ncsp.dev/
curl -s -o /dev/null -w "family %{http_code}\n" https://family.ncsp.dev/
```
Expected: the same auth-required status as on the LAN. If you get 502/530, check `ssh ct-tunnel 'docker logs cloudflared 2>&1 | tail -20'`.

- [ ] **Step 4: Verify the real public client IP reaches copyparty — critical**

Load `https://drive.ncsp.dev/` from a phone on mobile data (not WiFi), then:
```bash
ssh ct-files 'docker logs copyparty-psy 2>&1 | tail -5'
```
Expected: the log shows the **phone's public IP**, not `192.168.3.6` (cloudflared) and not a Cloudflare edge IP.

If it shows `192.168.3.6`, the `xff-hdr`/`xff-src`/`rproxy` config is wrong. **Stop and fix before proceeding** — with the stock `ban-403 9,2,1440` rule, the first exploit scanner would ban cloudflared and lock every remote user out for 24 hours.

- [ ] **Step 5: Log in from outside the LAN**

From the phone on mobile data, log in to both hostnames. Expected: each shows only its own tree, and browsing works.

---

## Task 12: Verify large uploads and the ban system

**Goal:** Prove the two behaviours that decided the tool choice and that protect the public endpoint.

**Files:** None (verification only)

- [ ] **Step 1: Upload a file over 100 MB from the phone through the tunnel**

From the phone on mobile data, via `https://drive.ncsp.dev/`, upload a video **larger than 100 MB**.

Expected: completes successfully. This is the check that eliminated SFTPGo — Cloudflare's free plan caps request bodies at 100 MB and returns 413 at the edge. copyparty's chunked uploader (`--u2sz` defaults to 96 MiB chunks, sized for exactly this) stays under the cap.

If this fails with 413, confirm `u2sz` has not been raised above 96 in the config.

- [ ] **Step 2: Verify the upload landed correctly**

```bash
ssh ct-files 'ls -la /mnt/cloud/volumes/samba/data/psy/ | tail -5'
```
Expected: the file present, owned `1000:1000`, full size.

- [ ] **Step 3: Verify upload resume**

Start another large upload from the phone, kill the browser tab mid-transfer, then reopen and re-add the same file. Expected: copyparty recognises the partial upload and resumes rather than restarting from zero.

- [ ] **Step 4: Verify the ban system bans the right IP**

From one device, fail the login **9 times** at `https://drive.ncsp.dev/`. Expected: that device is then blocked (stock rule: `ban-pw 9,60,1440` = 9 wrong passwords per hour → 24 h ban).

- [ ] **Step 5: Verify a second device on a different IP is unaffected — critical**

Immediately from a *different* device on a *different* network, load `https://drive.ncsp.dev/`. Expected: **reachable and able to log in**.

If the second device is also blocked, real-IP detection is broken — copyparty banned the proxy rather than the offender. Fix `xff-hdr`/`xff-src`/`rproxy`, then clear the ban:
```bash
ssh ct-files 'docker restart copyparty-psy'
```

- [ ] **Step 6: Verify the ban tables are independent**

While the psy instance still has a ban active, log in normally at `https://family.ncsp.dev/`. Expected: unaffected — separate containers, separate ban state.

- [ ] **Step 7: Clear the test ban**

```bash
ssh ct-files 'docker restart copyparty-psy'
```

- [ ] **Step 8: Verify previews and share links**

In the psy web UI: open an image, seek within a video, open a PDF, play an audio file, view a text file. Expected: all render; video seeking works (copyparty serves `Accept-Ranges: bytes` and 206 responses).

Then create a share link with **a password and an expiry**, open it from a logged-out browser, and confirm the password is required.

Note: copyparty has no built-in PDF viewer — files are served with correct content types so the browser's native viewer handles them. Record whether this works.

- [ ] **Step 9: Thumbnail load test under concurrent Samba use**

Open a large photo folder in the family UI (e.g. `Archivio Foto e clip/<a big year>`) while running:
```bash
ssh ct-files 'top -b -n 3 -d 5 | grep -E "copyparty|smbd|Cpu"'
```
Simultaneously copy a file over SMB from the home PC. Expected: SMB stays responsive, and neither container is OOM-killed. Confirm no kills:
```bash
ssh ct-files 'docker ps --format "{{.Names}}\t{{.Status}}" && dmesg -T 2>/dev/null | grep -i "killed process" | tail -5'
```
Expected: both containers `Up` with no restart, no OOM lines.

- [ ] **Step 10: Confirm the index excluded the recycle bins**

```bash
ssh ct-files 'du -sh /var/lib/copyparty/psy /var/lib/copyparty/family'
```
Expected: both modest (tens of MB for the index plus whatever thumbnails have been generated on demand) — **not** tens of GB, which would mean `no-idx` failed and the 38 GB of `.deleted` data was indexed.

---

## Task 13: Gatus checks and dashboard tiles

**Goal:** Monitoring that proves the service is alive **and** enforcing auth.

**Files:**
- Modify: `stacks/ct-mgmt/gatus/config.yaml`
- Modify: `stacks/ct-mgmt/dashboard-src/src/services.js`

- [ ] **Step 1: Replace the FileBrowser check**

In `stacks/ct-mgmt/gatus/config.yaml`, replace the `FileBrowser` block (currently at ~line 129) with:

```yaml
  - name: Drive (psy)
    group: important
    url: "http://192.168.3.11:3923"
    interval: 60s
    conditions:
      - "[CONNECTED] == true"
      - "[STATUS] == 401"
    alerts:
      - type: telegram

  - name: Drive (family)
    group: important
    url: "http://192.168.3.11:3924"
    interval: 60s
    conditions:
      - "[CONNECTED] == true"
      - "[STATUS] == 401"
    alerts:
      - type: telegram
```

**Substitute the actual status code recorded in Task 6 Step 10.** A bare `[STATUS] < 400` is not acceptable here — it would pass even if the ACL broke and the root became publicly listable. If copyparty returns `200` with a login page rather than `401`, use:

```yaml
    conditions:
      - "[CONNECTED] == true"
      - "[STATUS] == 200"
      - "[BODY] pat *login*"
```
adjusting the pattern to a string that appears only on the login page (find one with `curl -s http://192.168.3.11:3923/ | head -40`).

- [ ] **Step 2: Replace the dashboard tile**

In `stacks/ct-mgmt/dashboard-src/src/services.js`, replace the FileBrowser entry (line ~26) with two entries:

```javascript
      { name: 'Drive', desc: 'Personal files', href: 'http://drive.lan', ping: 'http://192.168.3.11:3923', icon: `${I}/files.svg` },
      { name: 'Drive (family)', desc: 'Family files', href: 'http://family.lan', ping: 'http://192.168.3.11:3924', icon: `${I}/files.svg` },
```

Check whether an icon exists before referencing it:
```bash
ssh ct-mgmt 'ls /opt/stacks/ct-mgmt/dashboard-src/public/icons/ 2>/dev/null | grep -iE "file|drive|folder"'
```
If no suitable icon exists, keep `${I}/filebrowser.svg` (the file is already present) rather than referencing a missing asset.

- [ ] **Step 3: Deploy both**

```bash
cd /home/psy/Documents/personal/infra
scp stacks/ct-mgmt/gatus/config.yaml root@ct-mgmt:/opt/stacks/ct-mgmt/gatus/config.yaml
scp stacks/ct-mgmt/dashboard-src/src/services.js root@ct-mgmt:/opt/stacks/ct-mgmt/dashboard-src/src/services.js
ssh ct-mgmt 'cd /opt/stacks/ct-mgmt && docker compose up -d --build gatus dashboard'
```
Expected: both recreated.

- [ ] **Step 4: Verify the checks pass**

```bash
sleep 70
curl -s http://status.lan/api/v1/endpoints/statuses 2>/dev/null | grep -o '"name":"Drive[^}]*"success":[a-z]*' | head -4
```
Expected: both `Drive (psy)` and `Drive (family)` show `"success":true`. If the endpoint shape differs, check the status page in a browser at `http://status.lan`.

- [ ] **Step 5: Verify the check actually fails when auth breaks**

```bash
ssh ct-files 'docker stop copyparty-family'
sleep 70
```
Expected: `Drive (family)` goes red on `http://status.lan` and a Telegram alert fires. Then:
```bash
ssh ct-files 'docker start copyparty-family'
```

- [ ] **Step 6: Confirm the dashboard renders**

Open `http://home.lan`. Expected: two Drive tiles, both showing healthy, no broken icon.

- [ ] **Step 7: Commit**

```bash
git add stacks/ct-mgmt/gatus/config.yaml stacks/ct-mgmt/dashboard-src/src/services.js
git commit -m "feat(ct-mgmt): monitor copyparty drives, retire FileBrowser check

Conditions assert the auth-required status rather than [STATUS] < 400, so a
broken ACL that made the webroot publicly listable would fail the check
instead of passing it."
```

---

## Task 14: ct-backup integration

**Goal:** Capture the argon2 salts, `sessions.db`, and the gitignored configs. Without the salt, a restore leaves every password broken.

**Two distinct gaps:** ct-files is **not** currently in the full-`/opt/stacks` rsync list (only ct-ha, ct-tools, ct-games, ct-workout are), and the configs are gitignored — so without this task they exist in neither the repo nor the backup.

**Per the standing lesson from ct-workout: committed ≠ deployed.** This task deploys the script and runs a real cycle.

**Files:**
- Modify: `stacks/ct-backup/scripts/pre-backup.sh`

- [ ] **Step 1: Inspect the current full-stacks list**

```bash
cd /home/psy/Documents/personal/infra
sed -n '60,90p' stacks/ct-backup/scripts/pre-backup.sh
```
Note the loop that rsyncs `/opt/stacks/$CT/` for a fixed list of CTs, and the `FULL_STACK_EXCLUDES` array.

- [ ] **Step 2: Add ct-files to that list**

In `stacks/ct-backup/scripts/pre-backup.sh` line 36, change:

```bash
FULL_STACK_CTS=(ct-ha ct-tools ct-games ct-workout)
```

to:

```bash
FULL_STACK_CTS=(ct-ha ct-tools ct-games ct-workout ct-files)
```

This captures `/opt/stacks/ct-files/copyparty/*/copyparty.conf`, the argon2 salt, and `sessions.db`.

Also extend the comment block just above the `FULL_STACK_EXCLUDES` array (around line 63) to mention ct-files:

```bash
# ct-ha and ct-tools bind-mount their service state (HA config, Mosquitto,
# ESPHome per-device keys) directly from /opt/stacks subdirs into containers,
# so a full rsync is the only way to capture it. ct-files is here for the
# copyparty configs (gitignored — they hold argon2 hashes) and, critically,
# the per-instance argon2 salt: restore without it and every password breaks.
```

No new exclude is needed — the regenerable index and thumbnail cache lives at `/var/lib/copyparty/`, outside `/opt/stacks` by design.

- [ ] **Step 3: Deploy the script — the step that was missed for ct-workout**

```bash
scp stacks/ct-backup/scripts/pre-backup.sh root@ct-backup:/usr/local/bin/pre-backup.sh
ssh ct-backup 'chmod +x /usr/local/bin/pre-backup.sh && grep -n "ct-files" /usr/local/bin/pre-backup.sh'
```
Expected: `ct-files` appears in both the volume-export list (already there) and the new full-stacks list.

- [ ] **Step 4: Run a real backup cycle**

```bash
ssh ct-backup 'systemctl start backup.service && sleep 5 && journalctl -u backup.service -n 5 --no-pager'
```
Then wait for completion and check:
```bash
ssh ct-backup 'journalctl -u backup.service -n 40 --no-pager | tail -20'
```
Expected: `Backup complete` with no `WARN: full stacks sync failed for ct-files`.

- [ ] **Step 5: Prove the config and salt are actually in the repository**

```bash
ssh ct-backup 'restic snapshots --latest 1 --json | head -c 300; echo
restic ls latest 2>/dev/null | grep -E "ct-files/copyparty" | head -10'
```
Expected: the two `copyparty.conf` files and the salt file are listed. **If they are not present, this task is not done** — that is precisely the ct-workout failure mode repeating.

- [ ] **Step 6: Commit**

```bash
cd /home/psy/Documents/personal/infra
git add stacks/ct-backup/scripts/pre-backup.sh
git commit -m "feat(ct-backup): back up ct-files /opt/stacks for copyparty secrets

ct-files was not in the full-stacks rsync list, and the copyparty configs are
gitignored because they hold argon2 hashes — so without this they existed in
neither the repo nor the backup. The salt in each /cfg is load-bearing: restore
without it and every password breaks.

Deployed to /usr/local/bin and verified present in a real restic snapshot, not
just committed."
```

---

## Task 15: Remove FileBrowser

**Goal:** Complete the cutover. Keep the `filebrowser-db` volume for a grace period.

**Files:**
- Modify: `stacks/ct-files/docker-compose.yml`
- Modify: `stacks/ct-mgmt/Caddyfile`

- [ ] **Step 1: Confirm everything from Tasks 7–13 passed**

Do not proceed unless upload ownership, Samba interop both ways, the jail, real-IP, the >100 MB tunnel upload, and both Gatus checks are all verified green.

- [ ] **Step 2: Remove the `filebrowser` service**

Delete the `filebrowser` service block from `stacks/ct-files/docker-compose.yml`. **Keep the `filebrowser-db` entry in the top-level `volumes:` section** — retaining the volume is the rollback path.

- [ ] **Step 3: Remove `files.lan`**

```bash
infra dns rm files.lan
```
Expected: the Caddy block and Pi-hole record are removed and both services reload.

- [ ] **Step 4: Apply on ct-files**

```bash
cd /home/psy/Documents/personal/infra
scp stacks/ct-files/docker-compose.yml root@ct-files:/opt/stacks/ct-files/docker-compose.yml
ssh ct-files 'cd /opt/stacks/ct-files && docker compose up -d --remove-orphans'
```
Expected: `filebrowser` removed; `samba`, `copyparty-psy`, `copyparty-family`, `portainer-agent` remain `Up`.

- [ ] **Step 5: Confirm the old endpoints are gone and the volume is retained**

```bash
ssh ct-files 'ss -tlnp | grep ":8080 " || echo "8080 closed"
docker volume ls | grep filebrowser'
curl -s -o /dev/null -w "%{http_code}\n" http://files.lan/ 2>&1 || echo "files.lan gone"
```
Expected: `8080 closed`, the `filebrowser-db` volume still listed, and `files.lan` no longer resolving or serving.

- [ ] **Step 6: Confirm the drives still work after the cutover**

```bash
curl -s -o /dev/null -w "drive.lan  %{http_code}\n" http://drive.lan/
curl -s -o /dev/null -w "family.lan %{http_code}\n" http://family.lan/
curl -s -o /dev/null -w "public     %{http_code}\n" https://drive.ncsp.dev/
```
Expected: the auth-required status for all three.

- [ ] **Step 7: Commit**

```bash
git add stacks/ct-files/docker-compose.yml stacks/ct-mgmt/Caddyfile
git commit -m "refactor(ct-files): remove FileBrowser, retire files.lan

copyparty replaces it. The filebrowser-db volume is retained as the rollback
path and should be deleted after a grace period.

Also closes a pre-existing scope defect: FileBrowser served the entire
/mnt/cloud pool (mediaserver, minecraft, games, dump, lost+found), whereas
each copyparty instance is jailed to a single Samba tree."
```

---

## Task 16: Documentation

**Goal:** Update the fleet docs so ct-files' new shape is discoverable without reading this plan.

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/hardware.md`
- Create: `stacks/ct-files/README.md`

- [ ] **Step 1: Update the ct-files section in `CLAUDE.md`**

Replace the ct-files block's `Resources`, `Role`, `Ports`, and `Config notes` with:

```markdown
- **Resources:** 2 vCPU, 2048MB RAM, 512MB swap, 16GB disk
- **Role:** File server. Runs Samba for SMB shares and two copyparty instances providing a Drive-like web UI over the same trees.
- **Stack:** `/opt/stacks/ct-files/docker-compose.yml` (local copy: `stacks/ct-files/`)
- **Ports:** 139/445 (Samba SMB), 3923 (copyparty psy), 3924 (copyparty family)
- **Config notes:** Privileged CT for clean UID mapping on shared storage. Full mergerfs pool (`/mnt/cloud`) bind-mounted into CT. AppArmor unconfined for Docker compatibility. Samba config/data/users from `/mnt/cloud/volumes/samba/`. **copyparty (2026-07-28):** two containers, one per uid — `copyparty-psy` (uid 1000, :3923, `drive.lan` / `drive.ncsp.dev`) and `copyparty-family` (uid 1001, :3924, `family.lan` / `family.ncsp.dev`). Each serves exactly one Samba tree at its webroot with one account and no root volume, so neither can reach the other or the wider pool. Never run as root — the `uid` volflag would require it and this is a privileged CT. **The argon2 salt in each `/cfg` (`/opt/stacks/ct-files/copyparty/<name>/`) is load-bearing: changing a container's `user:` silently invalidates every password hash.** Public path is cloudflared → copyparty directly (not via Caddy) so `CF-Connecting-IP` survives; Caddy serves only the `.lan` names and overwrites that header. Regenerable index/thumbnail cache lives at `/var/lib/copyparty/` — deliberately outside `/opt/stacks` so it is not backed up. Runbook: `stacks/ct-files/README.md`.
```

- [ ] **Step 2: Update the Services section in `CLAUDE.md`**

Replace the `Samba + FileBrowser run on ct-files...` line with:

```markdown
Samba runs on ct-files (SMB shares `psy` and `family`). copyparty provides the web drive over the same trees — http://drive.lan / https://drive.ncsp.dev (psy) and http://family.lan / https://family.ncsp.dev (family), each jailed to one tree. Access-only; no sync client. Web deletes are permanent and bypass Samba's recycle.
```

- [ ] **Step 3: Update `docs/hardware.md`**

Change the ct-files row (line ~86) from `Samba + FileBrowser, full pool` to `Samba + copyparty (2 instances), full pool`.

- [ ] **Step 4: Write the runbook**

Create `stacks/ct-files/README.md`:

```markdown
# ct-files

Samba (SMB) plus two copyparty instances giving a web drive over the *same* files.

| Instance | uid | Host port | LAN | Public | Tree |
|---|---|---|---|---|---|
| `copyparty-psy` | 1000 | 3923 | `drive.lan` | `drive.ncsp.dev` | `/mnt/cloud/volumes/samba/data/psy` |
| `copyparty-family` | 1001 | 3924 | `family.lan` | `family.ncsp.dev` | `/mnt/cloud/volumes/samba/data/family` |

Design: `docs/superpowers/specs/2026-07-28-ct-files-web-drive-design.md`

## Why two containers

copyparty's `uid` volflag only works when running as root, and this is a
privileged LXC where container uid 0 is host uid 0 — unacceptable for an
internet-facing service. Instead each container simply *runs as* the uid that
owns its tree, so uploads land correctly with no chown and no root.

## Layout on the CT

```
/opt/stacks/ct-files/copyparty/psy/       → container /cfg   (config + argon2 salt + sessions.db)
/opt/stacks/ct-files/copyparty/family/    → container /cfg
/var/lib/copyparty/psy/                   → container /var/copyparty  (index + thumbnails)
/var/lib/copyparty/family/                → container /var/copyparty
```

`/opt/stacks/ct-files` is backed up by ct-backup. `/var/lib/copyparty` is
**not**, on purpose — it is regenerable, and thumbnails would otherwise ship
to B2 nightly.

## THE SALT TRAP

The argon2 salt lives in each instance's `/cfg` and is uid-sensitive.

- **Never change a container's `user:`** after accounts exist. Every password
  hash silently stops working (upstream issue #1421).
- **Never delete the salt file.** Same effect.
- Hashes are **not portable** between the two instances — they have separate salts.
- A restore that omits `/cfg` leaves every password broken.

## Regenerating a password

Generate the hash with the *same* `/cfg` and the *same* uid as the running
container, or it will not validate:

```bash
# psy (uid 1000)
docker run --rm -it --user 1000:1000 \
  -v /opt/stacks/ct-files/copyparty/psy:/cfg \
  copyparty/ac:latest --ah-alg argon2 --ah-cli

# family (uid 1001)
docker run --rm -it --user 1001:1001 \
  -v /opt/stacks/ct-files/copyparty/family:/cfg \
  copyparty/ac:latest --ah-alg argon2 --ah-cli
```

Paste the result into the `[accounts]` block of that instance's
`copyparty.conf`, then `docker compose up -d <service>`.

## Real IP — read before touching the proxy config

Every ban rule keys on client IP, so a mistake here either disables the bans
or bans your own proxy and locks everyone out for 24 h.

- Public: cloudflared (`192.168.3.6`) → copyparty **directly**. `CF-Connecting-IP`
  comes from the Cloudflare edge and cannot be forged by the client.
- LAN: Caddy (`192.168.3.12`) → copyparty, and Caddy **overwrites**
  `CF-Connecting-IP` with `{remote_host}` so a LAN client cannot forge it.
- Direct to `:3923`/`:3924`: untrusted peer, so the header is ignored.

Never set `xff-src` to `lan` — copyparty is directly reachable on the LAN, so
any host could forge the header to evade a ban or ban a third party.

## Gotchas

- **No trash.** Web deletes are a real `unlink` and bypass Samba's `recycle`
  vfs. SMB deletes go to `.deleted` and are recoverable; web deletes are not.
  The `family` tree currently has **no off-site backup** — see spec Known
  limitations #8 and Follow-up work.
- **Do not add `vc-exit`** from upstream's sample config; it shuts the server
  down on an advisory warning instead of just displaying it.
- **Keep FTP and `dk`/dirkeys off.** Both are default-off. Recent advisories
  hit exactly those paths.
- **Keep dedup off.** Hardlinks across mergerfs branches are unsafe.
- **`usernames` and `pw-urlp` are deliberately at their defaults**, per the
  decision to start simple and add Authelia later. Login is password-only
  (one account per instance, so nothing to disambiguate) and `?pw=` in URLs is
  accepted (avoids breaking WebDAV clients). Do not "fix" these without
  reading the spec's *Deliberately minimal, by decision* section.
- **`th-r-pil` / `th-r-ffi` are pinned lists with `psd` removed** — layered
  PSDs decode to >1 GB and would OOM-kill the container inside its 640 MB
  `mem_limit`. The lists were copied from `--help` for the pinned image
  version, so **re-check them after a major image upgrade**; new extensions
  added upstream will be silently omitted until you refresh them.
- 122 CR2 raws are in neither decoder list and will silently not thumbnail.
  Cosmetic, accepted.
- `dbpath` must stay off the mergerfs pool — SQLite on FUSE corrupts (issue #919).
- Patch promptly. `vc-url` is enabled and surfaces new advisories in the control
  panel; upstream ships fixes same-day.
- No 2FA yet. Authelia is the planned follow-up.
```

- [ ] **Step 5: Verify the docs match reality**

```bash
ssh ct-files 'docker ps --format "{{.Names}}\t{{.Ports}}"; nproc; free -m | head -2; df -h / | tail -1'
```
Expected: matches what `CLAUDE.md` now claims — 2 cores, ~2048 MB, ~16 GB rootfs, ports 3923 and 3924.

- [ ] **Step 6: Commit**

```bash
cd /home/psy/Documents/personal/infra
git add CLAUDE.md docs/hardware.md stacks/ct-files/README.md
git commit -m "docs(ct-files): document copyparty web drive + runbook

Records the salt trap (uid-sensitive, breaks every hash if user: changes),
the split public/LAN real-IP topology and why it is not chained, and the
accepted no-trash risk on a family tree that has no off-site backup."
```

---

## Done criteria

- [ ] Two copyparty containers running as uid 1000 / 1001, neither as root.
- [ ] Web uploads land `1000:1000 644` (psy) and `1001:1001 660` (family).
- [ ] Samba writes appear in the web UI with no rescan; web uploads are modifiable in place over SMB.
- [ ] Neither account can reach the other's tree or anything above its webroot.
- [ ] `drive.lan`, `family.lan`, `drive.ncsp.dev`, `family.ncsp.dev` all serve and require auth.
- [ ] copyparty logs show real client IPs — the phone's public IP over the tunnel, the workstation's LAN IP via Caddy, and a forged header is discarded.
- [ ] A >100 MB upload from a phone through the tunnel succeeds.
- [ ] Failing 9 logins bans only that IP; a second device on another IP still works; the two instances ban independently.
- [ ] Both Gatus checks pass, assert auth is enforced, and go red when a container stops.
- [ ] Both configs and both argon2 salts are verified present in a real restic snapshot.
- [ ] FileBrowser removed, `files.lan` retired, `filebrowser-db` volume retained.
- [ ] `/mnt/cloud` is no longer exposed over HTTP anywhere.
- [ ] `CLAUDE.md`, `docs/hardware.md`, and `stacks/ct-files/README.md` match reality.

## Deferred (recorded, not done here)

- **Add the `family` tree to ct-backup** — `pct set 109 -mp10 /mnt/cloud/volumes/samba/data/family,mp=/backup-sources/samba-family,ro=1`, roughly $1.50–2/month for 281 GB in B2. Highest-value follow-up; 281 GB currently has no off-site copy at all.
- **Authelia** in front of both instances for TOTP 2FA.
- **An `xbd` before-delete trash hook**, if a recycle bin is wanted without relying on backups.
- **Delete the `filebrowser-db` volume** after the grace period.
