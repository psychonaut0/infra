# Gaming Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy `ct-games` on proxmoxmain — a single LXC running two Minecraft servers (vanilla/Paper + heavy modded), Playit.gg agents for CGNAT-friendly public reach, `itzg/mc-backup` sidecars for on-box cold snapshots to mergerfs, and Gatus monitoring. All config-as-code, compose-only, extensible to future games.

**Architecture:** One unprivileged Debian 13 LXC (VMID 112, 192.168.3.14) with a Docker Compose stack of five services on a shared `gamesnet` bridge network: two MC servers (`mc-vanilla`, `mc-modded`), one shared `playit-agent` with two tunnels attached server-side in the Playit dashboard, and two `itzg/mc-backup` sidecars (`backup-vanilla`, `backup-modded`). Live world data lives on the CT's NVMe boot disk; `itzg/mc-backup` writes daily `.tgz` snapshots to a bind-mounted mergerfs path (`/mnt/cloud/volumes/games/archives`); existing ct-backup → restic → B2 handles off-site. LAN-direct game + RCON ports for local play and admin. No new CTs elsewhere — proxmoxnode is hands-off per standing preference.

**Tech Stack:** Proxmox LXC, Docker Engine CE, Docker Compose v2, `itzg/minecraft-server`, `itzg/mc-backup`, `ghcr.io/playit-cloud/playit-agent`, Gatus TCP checks, existing Caddy / Portainer / ct-backup / `infra` CLI.

**Reference:** Design spec at `docs/superpowers/specs/2026-04-20-gaming-stack-design.md`.

**Git identity for commits:** Match existing repo history — `psy <psychonaut0@users.noreply.github.com>`. If `git commit` fails with "Author identity unknown", prepend the single commit with the env-var form used in the spec commit:
```bash
GIT_AUTHOR_NAME=psy GIT_AUTHOR_EMAIL=psychonaut0@users.noreply.github.com \
GIT_COMMITTER_NAME=psy GIT_COMMITTER_EMAIL=psychonaut0@users.noreply.github.com \
git commit -m "..."
```

---

## Task 1: Pre-flight checks

**Goal:** Verify every precondition in the spec's Pre-flight table before touching anything. If any check fails, stop and surface the failure — do not proceed.

**Files:** None (verification only)

- [ ] **Step 1: RAM headroom on proxmoxmain**

Run from blvckmain:
```bash
ssh proxmoxmain 'free -h'
```
Expected: `available` column (free -h row labeled `Mem:`) shows **≥ 18 GB**. If lower, stop and escalate.

- [ ] **Step 2: local-lvm free**

```bash
ssh proxmoxmain 'pvesm status'
```
Expected: row `local-lvm` shows `Avail` **≥ 40G**. Spec reference says ~308 GB free at design time.

- [ ] **Step 3: mergerfs free**

```bash
ssh proxmoxmain 'df -h /mnt/cloud'
```
Expected: `Avail` **≥ 50 GB**. Spec reference says ~1.6 TB free.

- [ ] **Step 4: VMID 112 unused**

```bash
ssh proxmoxmain 'pct list | awk "{print \$1}" | grep -w 112 || echo free'
```
Expected: outputs `free`. If it outputs `112`, stop — VMID clash.

- [ ] **Step 5: IP 192.168.3.14 unused**

```bash
ping -c1 -W1 192.168.3.14 || echo "no-reply"
```
Expected: outputs `no-reply`. Also cross-check Pi-hole DHCP leases if present.

- [ ] **Step 6: ct-backup source coverage (recorded, fixed in Task 15)**

```bash
ssh ct-backup 'ls /etc/backup-dispatch/ /opt/stacks/ct-backup/ 2>/dev/null'
ssh ct-backup 'grep -rn "ct-games\|/opt/stacks" /etc/backup-dispatch/ /opt/stacks/ct-backup/ 2>/dev/null || true'
```
Expected: identifies the source-list file(s) used by ct-backup. Record the path and whether `/opt/stacks/ct-games` (or a wildcard like `/opt/stacks/*`) is already covered. Fix in Task 15.

- [ ] **Step 7: Docker-in-LXC template (reference)**

Pick one existing privileged or unprivileged Docker CT and capture its `pct config` so the new CT matches:
```bash
ssh proxmoxmain 'pct config 105 | grep -E "^(features|unprivileged|lxc\.|mp|ostype|onboot)"'
```
Record output for reference (Task 3 uses the same `features:` and AppArmor flags).

- [ ] **Step 8: Playit.gg account ready (manual pre-req for Task 2)**

Confirm: you have (or will shortly create) a playit.gg account. No shell command.

- [ ] **Step 9: Record results**

Paste all check outputs into a throwaway scratch file or the conversation log. Do NOT commit anything yet.

---

## Task 2: Provision one Playit.gg agent (tunnels deferred to Task 12)

**Goal:** Create the Playit agent and capture its Secret Key. Tunnel creation happens post-deploy (Task 12 Step 1) because Playit's UI requires the agent to be online before tunnels can be attached.

**Files:** None (external web dashboard)

- [ ] **Step 1: Sign in at https://playit.gg**

Create an account if needed. Free tier is sufficient.

- [ ] **Step 2: Create the agent**

Dashboard → **Agents** → **Create Agent** → name it `ct-games`. On creation, Playit displays a **Secret Key** (a hex string). Copy it — it's the only time it's shown. This is the single secret that goes into `PLAYIT_SECRET` on ct-games.

- [ ] **Step 3: Store secret temporarily**

Hold the Secret Key in a scratch note for Task 10. Nothing committed.

- [ ] **Step 4: (Note — not an action)**

Tunnels `mc-vanilla` and `mc-modded` get created in **Task 12 Step 1**, after the agent container is running and registered online. That's Playit's required ordering — the dashboard won't let you attach a tunnel to an offline agent.

---

## Task 3: Create mergerfs archive directories on proxmoxmain

**Goal:** Prepare the bind-mount target before the CT references it.

**Files:** None (proxmox host filesystem)

- [ ] **Step 1: Check ownership/permissions pattern of an existing volumes dir**

```bash
ssh proxmoxmain 'ls -ld /mnt/cloud/volumes/mediaserver /mnt/cloud/volumes/samba 2>/dev/null'
```
Expected: both show ownership (likely `root:root` or similar). Record the pattern.

- [ ] **Step 2: Create the games archive tree**

```bash
ssh proxmoxmain 'mkdir -p /mnt/cloud/volumes/games/archives/vanilla /mnt/cloud/volumes/games/archives/modded'
```

- [ ] **Step 3: Align ownership with sibling dirs**

Match what Step 1 showed. If the sibling volumes dirs are `root:root 0755`, apply the same:
```bash
ssh proxmoxmain 'chown -R root:root /mnt/cloud/volumes/games && chmod -R 0755 /mnt/cloud/volumes/games'
```
(Adjust if Step 1 revealed a different pattern — copy that pattern instead.)

- [ ] **Step 4: Verify**

```bash
ssh proxmoxmain 'ls -ld /mnt/cloud/volumes/games /mnt/cloud/volumes/games/archives /mnt/cloud/volumes/games/archives/vanilla /mnt/cloud/volumes/games/archives/modded'
```
Expected: four directories, ownership/permissions match sibling `volumes/*` dirs.

---

## Task 4: Create LXC ct-games

**Goal:** Provision VMID 112 on proxmoxmain with the exact spec parameters. Do not start it yet.

**Files:** None (Proxmox pct command)

- [ ] **Step 1: Find the Debian 13 LXC template**

```bash
ssh proxmoxmain 'pveam list local | grep -i debian-13'
```
Expected: one line like `local:vztmpl/debian-13-standard_13.0-1_amd64.tar.zst`. Record the exact name.

If not present:
```bash
ssh proxmoxmain 'pveam update && pveam available --section system | grep debian-13'
ssh proxmoxmain 'pveam download local debian-13-standard_13.0-1_amd64.tar.zst'
```
(Replace with actual available name from the list.)

- [ ] **Step 2: Pick an SSH public key**

Use the same key that's authorized on existing CTs (from blvckmain). Confirm the key path:
```bash
ls ~/.ssh/id_ed25519.pub
```

- [ ] **Step 3: Create the CT**

Substitute `<TEMPLATE>` with the string from Step 1:
```bash
ssh proxmoxmain "pct create 112 local:vztmpl/<TEMPLATE> \
  --hostname ct-games \
  --cores 6 \
  --memory 16384 \
  --swap 4096 \
  --rootfs local-lvm:40 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.3.14/24,gw=192.168.3.1 \
  --nameserver 192.168.3.5 \
  --ostype debian \
  --unprivileged 1 \
  --features nesting=1 \
  --onboot 1 \
  --start 0 \
  --ssh-public-keys /dev/stdin" < ~/.ssh/id_ed25519.pub
```

Expected: `pct create` finishes without error. If `nameserver` option rejects the IP format, drop it — CT will inherit proxmoxmain's resolv.conf.

- [ ] **Step 4: Add AppArmor + proc/sys for Docker-in-LXC**

```bash
ssh proxmoxmain 'cat >> /etc/pve/lxc/112.conf <<EOF
lxc.apparmor.profile: unconfined
lxc.cgroup2.devices.allow: a
lxc.cap.drop:
lxc.mount.auto: "proc:rw sys:rw"
EOF'
```

- [ ] **Step 5: Verify config**

```bash
ssh proxmoxmain 'pct config 112'
```
Expected: shows hostname `ct-games`, 6 cores, 16384 memory, 4096 swap, rootfs 40G on local-lvm, net0 with 192.168.3.14, features nesting=1, unprivileged 1, onboot 1. The four `lxc.*` lines from Step 4 appear at the end.

- [ ] **Step 6: Still do not start the CT** — bind mount goes in next.

---

## Task 5: Configure bind mount for archives

**Goal:** Attach the mergerfs archive dir into ct-games at `/mnt/archives`.

**Files:** None (pct set)

- [ ] **Step 1: Add the bind mount**

```bash
ssh proxmoxmain 'pct set 112 -mp0 /mnt/cloud/volumes/games/archives,mp=/mnt/archives'
```

- [ ] **Step 2: Verify**

```bash
ssh proxmoxmain 'pct config 112 | grep mp0'
```
Expected: `mp0: /mnt/cloud/volumes/games/archives,mp=/mnt/archives`.

- [ ] **Step 3: Start the CT**

```bash
ssh proxmoxmain 'pct start 112'
```

- [ ] **Step 4: Wait for network**

```bash
for i in $(seq 1 30); do ping -c1 -W1 192.168.3.14 >/dev/null && echo up && break || sleep 1; done
```
Expected: `up` within 30 seconds.

- [ ] **Step 5: Verify bind mount inside CT**

```bash
ssh proxmoxmain 'pct exec 112 -- ls -ld /mnt/archives /mnt/archives/vanilla /mnt/archives/modded'
```
Expected: three directories visible. Writable by root (the mount is rw).

- [ ] **Step 6: Confirm SSH from blvckmain works**

The CT inherits the authorized key from Task 4 Step 3.
```bash
ssh root@192.168.3.14 'hostname && uname -a'
```
Expected: outputs `ct-games` and a Debian 13 kernel line. If the hostname doesn't resolve as `ct-games`, add it to your local `~/.ssh/config` or `/etc/hosts` matching the pattern used for other CTs:
```
# ~/.ssh/config entry
Host ct-games
    HostName 192.168.3.14
    User root
```

---

## Task 6: Install Docker Engine + Compose on ct-games

**Goal:** Standard Docker-in-LXC install, identical to ct-media / ct-photos.

**Files:** None (package install)

- [ ] **Step 1: Update apt + install prerequisites**

```bash
ssh root@ct-games 'apt update && apt install -y ca-certificates curl gnupg lsb-release'
```

- [ ] **Step 2: Add Docker's GPG key**

```bash
ssh root@ct-games 'install -m 0755 -d /etc/apt/keyrings && \
  curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
  chmod a+r /etc/apt/keyrings/docker.gpg'
```

- [ ] **Step 3: Add the Docker apt repo**

```bash
ssh root@ct-games 'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list'
```

- [ ] **Step 4: Install Docker**

```bash
ssh root@ct-games 'apt update && apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin'
```

- [ ] **Step 5: Verify**

```bash
ssh root@ct-games 'docker version && docker compose version && docker run --rm hello-world'
```
Expected: Docker client + server versions print, compose v2 prints, `hello-world` image pulls and prints its message.

- [ ] **Step 6: Enable Docker on boot**

```bash
ssh root@ct-games 'systemctl enable --now docker'
```

---

## Task 7: Onboard ct-games to Portainer (optional but recommended)

**Goal:** Get ct-games into the existing Portainer on ct-mgmt so containers appear in the UI alongside the rest of the fleet.

**Files:** None (Portainer agent container on ct-games)

- [ ] **Step 1: Find the portainer-agent command used on other CTs**

```bash
ssh root@ct-media 'docker inspect portainer-agent 2>/dev/null | head -80'
```
Record the image tag + port binding pattern used (typically `portainer/agent:latest` exposing `9001`).

- [ ] **Step 2: Launch the agent on ct-games**

Match the Step 1 pattern:
```bash
ssh root@ct-games 'docker run -d \
  --name portainer-agent \
  --restart=always \
  -p 9001:9001 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /var/lib/docker/volumes:/var/lib/docker/volumes \
  portainer/agent:latest'
```

(If Step 1 revealed a different tag or mount set, use that instead.)

- [ ] **Step 3: Add ct-games as an environment in Portainer UI**

Browser → `https://portainer.lan` → **Environments** → **Add environment** → **Agent** → URL `192.168.3.14:9001` → Name `ct-games` → **Add environment**.

- [ ] **Step 4: Verify**

Back in the Portainer dashboard, ct-games should appear as a healthy environment with zero containers (the agent container itself is hidden from the normal container list on agent environments — that's expected).

---

## Task 8: Scaffold `stacks/ct-games/` in the infra repo

**Goal:** Create the compose file and env template in git. Do not deploy yet.

**Files:**
- Create: `stacks/ct-games/docker-compose.yml`
- Create: `stacks/ct-games/.env.example`
- Modify: `.gitignore` (add `stacks/ct-games/.env` if not already covered by a repo-wide pattern)

- [ ] **Step 1: Check existing gitignore pattern**

```bash
cd /home/psy/Documents/personal/infra
grep -n ".env" .gitignore 2>/dev/null
```

Expected: either a repo-wide `**/.env` or `stacks/*/.env` pattern. If present, no edit needed in Step 4. If missing, Step 4 adds one.

- [ ] **Step 2: Create the compose file**

Write `stacks/ct-games/docker-compose.yml` with exactly this content:

```yaml
services:
  mc-vanilla:
    image: itzg/minecraft-server:latest
    container_name: mc-vanilla
    restart: unless-stopped
    environment:
      EULA: "TRUE"
      TYPE: "PAPER"
      VERSION: "LATEST"
      MEMORY: "4G"
      MAX_PLAYERS: "20"
      DIFFICULTY: "normal"
      MOTD: "Vanilla Paper | 192.168.3.14:25565"
      OPS: ""
      WHITELIST: ""
      ENABLE_RCON: "true"
      RCON_PASSWORD: "${RCON_PASSWORD_VANILLA}"
      RCON_PORT: "25575"
      TZ: "Europe/Rome"
    ports:
      - "25565:25565/tcp"
      - "25575:25575/tcp"
    volumes:
      - ./data/vanilla:/data
    networks:
      - gamesnet
    healthcheck:
      test: ["CMD", "mc-health"]
      interval: 30s
      timeout: 10s
      start_period: 3m
      retries: 3

  mc-modded:
    image: itzg/minecraft-server:latest
    container_name: mc-modded
    restart: unless-stopped
    environment:
      EULA: "TRUE"
      TYPE: "FORGE"
      VERSION: "LATEST"
      MEMORY: "8G"
      MAX_PLAYERS: "10"
      DIFFICULTY: "normal"
      MOTD: "Modded | 192.168.3.14:25566"
      OPS: ""
      WHITELIST: ""
      ENABLE_RCON: "true"
      RCON_PASSWORD: "${RCON_PASSWORD_MODDED}"
      RCON_PORT: "25575"
      TZ: "Europe/Rome"
    ports:
      - "25566:25565/tcp"
      - "25576:25575/tcp"
    volumes:
      - ./data/modded:/data
    networks:
      - gamesnet
    healthcheck:
      test: ["CMD", "mc-health"]
      interval: 30s
      timeout: 10s
      start_period: 5m
      retries: 3

  playit-agent:
    image: ghcr.io/playit-cloud/playit-agent:latest
    container_name: playit-agent
    restart: unless-stopped
    environment:
      PLAYIT_SECRET: "${PLAYIT_SECRET}"
    volumes:
      - playit-data:/etc/playit
    networks:
      - gamesnet
    depends_on:
      - mc-vanilla
      - mc-modded

  backup-vanilla:
    image: itzg/mc-backup:latest
    container_name: backup-vanilla
    restart: unless-stopped
    environment:
      RCON_HOST: "mc-vanilla"
      RCON_PORT: "25575"
      RCON_PASSWORD: "${RCON_PASSWORD_VANILLA}"
      BACKUP_INTERVAL: "24h"
      INITIAL_DELAY: "30m"
      PRUNE_BACKUPS_DAYS: "14"
      PAUSE_IF_NO_PLAYERS: "true"
      BACKUP_NAME: "world-%Y%m%d-%H%M%S.tgz"
      DEST_DIR: "/backups"
      TZ: "Europe/Rome"
    volumes:
      - ./data/vanilla:/data:ro
      - /mnt/archives/vanilla:/backups
    networks:
      - gamesnet
    depends_on:
      - mc-vanilla

  backup-modded:
    image: itzg/mc-backup:latest
    container_name: backup-modded
    restart: unless-stopped
    environment:
      RCON_HOST: "mc-modded"
      RCON_PORT: "25575"
      RCON_PASSWORD: "${RCON_PASSWORD_MODDED}"
      BACKUP_INTERVAL: "24h"
      INITIAL_DELAY: "30m"
      PRUNE_BACKUPS_DAYS: "14"
      PAUSE_IF_NO_PLAYERS: "true"
      BACKUP_NAME: "world-%Y%m%d-%H%M%S.tgz"
      DEST_DIR: "/backups"
      TZ: "Europe/Rome"
    volumes:
      - ./data/modded:/data:ro
      - /mnt/archives/modded:/backups
    networks:
      - gamesnet
    depends_on:
      - mc-modded

networks:
  gamesnet:
    driver: bridge

volumes:
  playit-data:
```

- [ ] **Step 3: Create `.env.example`**

Write `stacks/ct-games/.env.example` with exactly this content:

```bash
# Gaming stack secrets. Copy to `.env` and fill in real values.
# `.env` is gitignored. Never commit real secrets.

# RCON passwords — 32 random characters each.
# Generate: openssl rand -base64 24
RCON_PASSWORD_VANILLA=CHANGE_ME_VANILLA_RCON
RCON_PASSWORD_MODDED=CHANGE_ME_MODDED_RCON

# Playit.gg agent secret key from https://playit.gg/account/agents
# One agent with two tunnels attached (mc-vanilla, mc-modded).
PLAYIT_SECRET=CHANGE_ME_PLAYIT_AGENT_SECRET

# Optional: CurseForge API key, only if mc-modded pulls a CF modpack.
# Create at https://console.curseforge.com/?#/api-keys
#CF_API_KEY=
```

- [ ] **Step 4: Verify or add `.env` gitignore coverage**

If Step 1 found no matching pattern, append to the repo-root `.gitignore`:
```
stacks/*/.env
```

- [ ] **Step 5: Sanity-check compose syntax**

```bash
cd /home/psy/Documents/personal/infra/stacks/ct-games
docker compose config -q
```
Expected: no output (means valid). If `docker compose` isn't on blvckmain, run the check on ct-games after Task 10 Step 1 — it'll fail loudly if the syntax is bad.

- [ ] **Step 6: Commit**

```bash
cd /home/psy/Documents/personal/infra
git add stacks/ct-games/docker-compose.yml stacks/ct-games/.env.example .gitignore
git commit -m "Add ct-games compose stack (MC vanilla+modded, Playit, mc-backup)"
```

---

## Task 9: Generate RCON passwords

**Goal:** Produce two strong random passwords for the RCON ports.

**Files:** None (secrets written to ct-games in Task 10)

- [ ] **Step 1: Generate vanilla RCON password**

```bash
openssl rand -base64 24
```
Copy output → save in scratch as `RCON_PASSWORD_VANILLA`.

- [ ] **Step 2: Generate modded RCON password**

```bash
openssl rand -base64 24
```
Copy output → save as `RCON_PASSWORD_MODDED`.

Keep them in the scratch note alongside the single Playit agent secret from Task 2.

---

## Task 10: Deploy the stack to ct-games

**Goal:** Sync compose files to ct-games, write `.env` with real secrets, bring the stack up.

**Files:**
- Create on ct-games: `/opt/stacks/ct-games/docker-compose.yml`, `/opt/stacks/ct-games/.env`

- [ ] **Step 1: Deploy the stack files via `infra` CLI**

From blvckmain (the CLI auto-discovers ct-games because the compose file now exists):
```bash
cd /home/psy/Documents/personal/infra
infra ls | grep ct-games
```
Expected: shows mc-vanilla, mc-modded, playit-agent, backup-vanilla, backup-modded mapped to ct-games.

- [ ] **Step 2: Push the `.env.example` via rsync, then write `.env` directly on ct-games**

```bash
ssh root@ct-games 'mkdir -p /opt/stacks/ct-games'
rsync -av stacks/ct-games/ root@ct-games:/opt/stacks/ct-games/
```

- [ ] **Step 3: Create real `.env` on ct-games**

SSH in and write it with the four secrets (vanilla RCON, modded RCON, vanilla Playit, modded Playit):
```bash
ssh root@ct-games 'cat > /opt/stacks/ct-games/.env <<EOF
RCON_PASSWORD_VANILLA=<paste from Task 9 Step 1>
RCON_PASSWORD_MODDED=<paste from Task 9 Step 2>
PLAYIT_SECRET=<paste agent secret from Task 2 Step 2>
EOF
chmod 600 /opt/stacks/ct-games/.env'
```

- [ ] **Step 4: Bring the stack up**

```bash
ssh root@ct-games 'cd /opt/stacks/ct-games && docker compose up -d'
```

Expected: images pull (may take a few minutes — `itzg/minecraft-server` is ~1 GB). Then containers report `Started`. On first startup, `mc-vanilla` will download Paper + run EULA initialization; `mc-modded` will download Forge.

- [ ] **Step 5: Tail mc-vanilla until it's ready**

```bash
ssh root@ct-games 'cd /opt/stacks/ct-games && docker compose logs -f mc-vanilla' # Ctrl-C after seeing "Done"
```
Expected: within ~3 min, a line like `[Server thread/INFO]: Done (xx.xs)! For help, type "help"`. If RCON starts, a second line `[Server thread/INFO]: RCON running on 0.0.0.0:25575`.

- [ ] **Step 6: Tail mc-modded until it's ready**

```bash
ssh root@ct-games 'cd /opt/stacks/ct-games && docker compose logs -f mc-modded' # Ctrl-C after "Done"
```
Expected: same — may take 5–10 min on first boot while Forge downloads.

- [ ] **Step 7: Confirm all five services are up**

```bash
ssh root@ct-games 'cd /opt/stacks/ct-games && docker compose ps'
```
Expected: all five containers listed with state `running` or `healthy`. `playit-agent` + `backup-*` sidecars should be running (backups just wait for their 30-min INITIAL_DELAY).

---

## Task 11: Verify LAN connectivity + RCON

**Goal:** Confirm both servers accept LAN game connections and RCON commands from blvckmain.

**Files:** None (verification)

- [ ] **Step 1: TCP reachability on both game ports**

```bash
nc -zv 192.168.3.14 25565
nc -zv 192.168.3.14 25566
```
Expected: both say `succeeded` / `open`.

- [ ] **Step 2: Install an RCON client if not already present**

```bash
which mcrcon || sudo apt install -y mcrcon || \
  ( go install github.com/Tiiffi/mcrcon@latest 2>/dev/null ) || \
  ( echo "Manual install: https://github.com/Tiiffi/mcrcon/releases" )
```
Expected: `mcrcon --help` works.

- [ ] **Step 3: RCON ping vanilla**

Using the vanilla password:
```bash
mcrcon -H 192.168.3.14 -P 25575 -p '<RCON_PASSWORD_VANILLA>' list
```
Expected: output like `There are 0 of a max of 20 players online:`.

- [ ] **Step 4: RCON ping modded**

```bash
mcrcon -H 192.168.3.14 -P 25576 -p '<RCON_PASSWORD_MODDED>' list
```
Expected: `There are 0 of a max of 10 players online:`.

- [ ] **Step 5: Connect from a Minecraft client on LAN**

Launch a Minecraft Java client on blvckmain (or phone on Wi-Fi). Multiplayer → Direct Connect → `192.168.3.14:25565` for vanilla, `:25566` for modded. Verify both join and can spawn.

If clients fail to join, check:
```bash
ssh root@ct-games 'cd /opt/stacks/ct-games && docker compose logs --tail 50 mc-vanilla'
```
Typical issues: EULA wasn't accepted (shouldn't happen — the env sets it), version mismatch (client too old), or mc-health not ready yet.

---

## Task 12: Create Playit tunnels + verify external connectivity

**Goal:** Now that `playit-agent` is running, create the two tunnels in the Playit dashboard and prove they route traffic from the public internet into both MC servers.

**Files:** None (external web dashboard + verification)

- [ ] **Step 1: Confirm the Playit agent is registered and online**

```bash
ssh root@ct-games 'docker logs playit-agent --tail 30'
```
Expected: log lines like `tunnel established` / `connected to playit` and no repeating error loops. If you see "waiting for secret" or "invalid claim", the `PLAYIT_SECRET` in `.env` is wrong — re-check, `docker compose up -d playit-agent` to re-apply.

Also confirm in the Playit dashboard → **Agents** that the `ct-games` agent shows **Online**. Creating tunnels requires the agent to be online.

- [ ] **Step 2: Create tunnel `mc-vanilla` attached to the `ct-games` agent**

Playit dashboard → **Create Tunnel**:
- Protocol: **Minecraft Java**
- Agent: `ct-games`
- Name: `mc-vanilla`
- Local target: `mc-vanilla:25565`
- Accept the free-tier hostname (format: `something.joinmc.link`). Copy it.

- [ ] **Step 3: Create tunnel `mc-modded` attached to the same agent**

Same flow:
- Protocol: **Minecraft Java**
- Agent: `ct-games`
- Name: `mc-modded`
- Local target: `mc-modded:25565` (both MC containers listen on 25565 internally — different service names, same port, `gamesnet` disambiguates)
- Copy the hostname.

- [ ] **Step 4: Confirm both tunnels are green in the Playit dashboard**

Each tunnel should show **Online** within ~30 s of creation. If one is red, verify its local target (`mc-vanilla:25565` or `mc-modded:25565`, not `localhost` or an IP).

- [ ] **Step 5: Join vanilla from off-LAN**

Use your phone on cellular data (or ask a friend) to connect a Minecraft Java client to the vanilla hostname (the one from Step 2). Verify join succeeds.

- [ ] **Step 6: Join modded from off-LAN**

Same test against the modded hostname (from Step 3). Verify join succeeds.

---

## Task 13: Add Gatus monitoring checks

**Goal:** Wire the two MC TCP checks into the existing Gatus config so outages page via Telegram.

**Files:**
- Modify: `stacks/ct-mgmt/gatus/config.yaml`

- [ ] **Step 1: Find the right section of the config**

```bash
grep -n "important\|critical\|alerts\|endpoints" /home/psy/Documents/personal/infra/stacks/ct-mgmt/gatus/config.yaml | head -40
```
Expected: locate the `endpoints:` list and the tier pattern (the spec says checks under `important` alongside Jellyfin / Immich).

- [ ] **Step 2: Add the two TCP checks**

Open `stacks/ct-mgmt/gatus/config.yaml` in an editor. Find the group containing Jellyfin (or whichever check is in the `important` tier), then append the following two endpoints in the same style as existing entries. The exact YAML shape (e.g. whether each endpoint has `group: important`, whether alerts are per-endpoint or inherited) depends on the current file — match the existing pattern verbatim:

```yaml
- name: mc-vanilla
  group: games
  url: "tcp://192.168.3.14:25565"
  interval: 60s
  conditions:
    - "[CONNECTED] == true"
  alerts:
    - type: telegram

- name: mc-modded
  group: games
  url: "tcp://192.168.3.14:25566"
  interval: 60s
  conditions:
    - "[CONNECTED] == true"
  alerts:
    - type: telegram
```

If the existing config uses a tier-level `alerts:` block and individual endpoints omit the `alerts:` list, drop the `alerts:` lines above and rely on inheritance.

- [ ] **Step 3: Validate YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('/home/psy/Documents/personal/infra/stacks/ct-mgmt/gatus/config.yaml'))" && echo OK
```
Expected: `OK`. If it errors, fix the indent.

- [ ] **Step 4: Deploy to ct-mgmt**

```bash
cd /home/psy/Documents/personal/infra
infra deploy ct-mgmt
```
Expected: rsync + compose up completes. Gatus reloads; if it doesn't auto-reload config, restart explicitly:
```bash
ssh root@ct-mgmt 'cd /opt/stacks/ct-mgmt && docker compose restart gatus'
```

- [ ] **Step 5: Verify in Gatus UI**

Browser → `http://status.lan` → expand the `games` group (or `important` tier, depending on grouping). Both endpoints should appear; within 60 s, both show green.

- [ ] **Step 6: Commit**

```bash
cd /home/psy/Documents/personal/infra
git add stacks/ct-mgmt/gatus/config.yaml
git commit -m "Gatus: add mc-vanilla and mc-modded TCP checks"
```

---

## Task 14: Verify alerting round-trip

**Goal:** Prove an outage triggers a Telegram alert and recovery clears it. This matches Acceptance Criterion 7 in the spec.

**Files:** None

- [ ] **Step 1: Stop mc-vanilla**

```bash
ssh root@ct-games 'docker stop mc-vanilla'
```

- [ ] **Step 2: Wait and watch the status page**

Leave stopped for ~3 minutes. Gatus checks every 60 s — after 2 consecutive failures (~2 min) it should flip red and fire the Telegram alert via `@blvckhomelab_bot`.

Expected: Telegram message arrives within 2–3 min naming `mc-vanilla`.

- [ ] **Step 3: Start mc-vanilla back up**

```bash
ssh root@ct-games 'docker start mc-vanilla'
```

- [ ] **Step 4: Wait for recovery alert**

Within 1–2 min after the container reaches healthy, Gatus fires a recovery message.

Expected: Telegram "recovered" / "back up" message.

- [ ] **Step 5: Sanity re-check both servers**

```bash
nc -zv 192.168.3.14 25565
nc -zv 192.168.3.14 25566
```
Expected: both open.

---

## Task 15: Add ct-games to ct-backup source list

**Goal:** Make sure the nightly restic → B2 job captures ct-games — both `.env` secrets and the live NVMe world data under `/opt/stacks/ct-games/data/`.

**Context from Task 1 Step 6:** ct-backup's source list is a bash associative array `CT_IPS` in `stacks/ct-backup/scripts/pre-backup.sh` (deployed to `/usr/local/bin/pre-backup.sh` on ct-backup). The pre-existing stale `[ct-ha]=192.168.3.14` entry was already corrected to `.10` in a standalone commit before this task (see `git log --oneline | grep "ct-ha"`). ct-games must be added to both `CT_IPS` (for `.env` capture) and `FULL_STACK_CTS` (so live world data under `/opt/stacks/ct-games/data/` ships to B2, not just `.env`).

**Files:**
- Modify: `stacks/ct-backup/scripts/pre-backup.sh`

- [ ] **Step 1: Confirm current state**

```bash
grep -nE "CT_IPS|FULL_STACK_CTS|ct-ha|ct-games" /home/psy/Documents/personal/infra/stacks/ct-backup/scripts/pre-backup.sh
```
Expected: shows `[ct-ha]=192.168.3.10` (corrected), no `[ct-games]` entry yet, `FULL_STACK_CTS=(ct-ha ct-tools)`.

- [ ] **Step 2: Add ct-games to `CT_IPS`**

Edit `stacks/ct-backup/scripts/pre-backup.sh`. Locate the `CT_IPS` array and add the ct-games entry (keep alphabetical or match existing ordering — after `[ct-files]`, before `[ct-ha]` works):

```bash
[ct-files]=192.168.3.11
[ct-games]=192.168.3.14
[ct-ha]=192.168.3.10
```

- [ ] **Step 3: Add ct-games to `FULL_STACK_CTS`**

In the same file, change:
```bash
FULL_STACK_CTS=(ct-ha ct-tools)
```
to:
```bash
FULL_STACK_CTS=(ct-ha ct-tools ct-games)
```

Rationale: MC world data lives on ct-games at `/opt/stacks/ct-games/data/<server>/` (bind-mounted into the Docker containers). Without `FULL_STACK_CTS` inclusion, only `.env` gets captured and the live worlds are absent from B2.

- [ ] **Step 4: Verify diff**

```bash
cd /home/psy/Documents/personal/infra
git diff stacks/ct-backup/scripts/pre-backup.sh
```
Expected: two small additions — one line inside `CT_IPS`, one token added to `FULL_STACK_CTS`.

- [ ] **Step 5: Deploy to ct-backup**

No `infra deploy` support for ct-backup (it's not a compose stack — uses native systemd + scripts). Copy manually:
```bash
scp stacks/ct-backup/scripts/pre-backup.sh root@192.168.3.13:/usr/local/bin/pre-backup.sh
ssh root@192.168.3.13 'chmod 755 /usr/local/bin/pre-backup.sh'
```

- [ ] **Step 6: Authorize ct-backup's SSH key on ct-games**

ct-backup uses its own key with rrsync forced-command restriction to pull from other CTs. Grab the public key from ct-backup and install it on ct-games with the same forced-command:

```bash
BACKUP_PUBKEY=$(ssh root@192.168.3.13 'cat /root/.ssh/id_ed25519.pub')
# Look at an existing CT (e.g. ct-media) for the exact authorized_keys line format:
ssh ct-media 'grep "ct-backup-dispatch\|backup-dispatch" /root/.ssh/authorized_keys | head -1'
```
Expected: a line like `command="/usr/local/bin/backup-dispatch.sh",restrict ssh-ed25519 ... ct-backup-dispatch`.

Verify the `backup-dispatch.sh` script exists on ct-games (if not, copy from the repo or from ct-media):
```bash
ssh root@ct-games 'test -x /usr/local/bin/backup-dispatch.sh && echo ok || echo missing'
```

If missing, install it:
```bash
scp /home/psy/Documents/personal/infra/stacks/ct-backup/scripts/backup-dispatch.sh root@ct-games:/usr/local/bin/backup-dispatch.sh
ssh root@ct-games 'chmod 755 /usr/local/bin/backup-dispatch.sh'
```

Then append the authorized_keys line on ct-games:
```bash
ssh root@ct-games "echo 'command=\"/usr/local/bin/backup-dispatch.sh\",restrict $BACKUP_PUBKEY ct-backup-dispatch' >> /root/.ssh/authorized_keys"
```

- [ ] **Step 7: Dry-run the pre-backup on ct-backup**

```bash
ssh root@192.168.3.13 'systemctl start backup.service'
ssh root@192.168.3.13 'journalctl -u backup.service -f -n 80'   # Ctrl-C when run finishes
```
Expected: log lines showing `Pulling .env files from ct-games (192.168.3.14)` and `Pulling full /opt/stacks/ct-games from ct-games (192.168.3.14)` with non-zero byte counts. Run ends with a successful restic snapshot message.

- [ ] **Step 8: Confirm ct-games content in the snapshot**

```bash
ssh root@192.168.3.13 'source /etc/restic/b2.env && restic -p /etc/restic/password snapshots --latest 1'
ssh root@192.168.3.13 'source /etc/restic/b2.env && restic -p /etc/restic/password ls latest | grep ct-games | head'
```
Expected: ct-games paths appear, including `.env` and at least one file under `stacks/ct-games/data/vanilla/` or `/modded/`.

(Exact env-file and password-file paths may differ — if the above fails, check `/etc/restic/` for the right names.)

- [ ] **Step 9: Commit**

```bash
cd /home/psy/Documents/personal/infra
GIT_AUTHOR_NAME=psy GIT_AUTHOR_EMAIL=psychonaut0@users.noreply.github.com \
GIT_COMMITTER_NAME=psy GIT_COMMITTER_EMAIL=psychonaut0@users.noreply.github.com \
git commit stacks/ct-backup/scripts/pre-backup.sh -m "ct-backup: add ct-games to source list (env + full /opt/stacks)"
```

---

## Task 16: Verify on-box archive creation (time-gated)

**Goal:** Confirm `itzg/mc-backup` writes .tgz snapshots to mergerfs. This is time-gated — the sidecar waits `INITIAL_DELAY=30m` then runs on the `BACKUP_INTERVAL=24h` cadence.

**Files:** None (verification)

- [ ] **Step 1: Force a one-off backup by short-circuiting INITIAL_DELAY**

The `itzg/mc-backup` container respects `INITIAL_DELAY` on every restart. Temporarily override it to run immediately:

```bash
ssh root@ct-games 'cd /opt/stacks/ct-games && \
  docker compose stop backup-vanilla backup-modded && \
  INITIAL_DELAY=1s docker compose up -d backup-vanilla backup-modded'
```

After this one-shot cycle, the compose file's `INITIAL_DELAY=30m` resumes on any future `docker compose up`.

Alternatively, just wait — the sidecars run on their own within 30 min of stack startup. Confirm via `docker compose logs backup-vanilla | grep -i "starting backup"`.

- [ ] **Step 2: Check archive directory for vanilla**

```bash
ssh proxmoxmain 'ls -la /mnt/cloud/volumes/games/archives/vanilla/'
```
Expected: at least one `world-YYYYMMDD-HHMMSS.tgz` file, non-zero size.

- [ ] **Step 3: Check archive directory for modded**

```bash
ssh proxmoxmain 'ls -la /mnt/cloud/volumes/games/archives/modded/'
```
Expected: same.

- [ ] **Step 4: Sanity-extract a file from the archive**

```bash
ssh proxmoxmain 'ls /mnt/cloud/volumes/games/archives/vanilla/world-*.tgz | head -1 | xargs -I{} tar -tzf {} | head -10'
```
Expected: shows world files like `world/level.dat`, `world/region/*.mca`.

- [ ] **Step 5: Note the next-check date**

After 14 days of operation, confirm `PRUNE_BACKUPS_DAYS=14` is pruning oldest archives (you should never see more than ~14 files per server). Add a reminder in your calendar for 2026-05-04 to re-verify.

---

## Task 17: Add Pi-hole local DNS entries (optional, quality-of-life)

**Goal:** Resolve `mc-vanilla.lan` and `mc-modded.lan` on the home network so players on LAN don't need the IP.

**Files:** Local DNS config on ct-dns (Pi-hole web UI or custom config)

- [ ] **Step 1: Check how existing `.lan` entries are maintained**

The homelab runs Pi-hole on ct-dns. Entries may be in:
- Web UI → **Local DNS** → **DNS Records** (runtime config)
- A static `/etc/pihole/custom.list` or `/etc/dnsmasq.d/02-custom.conf`

```bash
ssh root@ct-dns 'ls /etc/pihole/custom.list /etc/dnsmasq.d/ 2>/dev/null && cat /etc/pihole/custom.list 2>/dev/null'
```
Expected: identifies where to add entries.

- [ ] **Step 2: Add the two records**

If `/etc/pihole/custom.list` is used:
```bash
ssh root@ct-dns 'grep -qxF "192.168.3.14 mc-vanilla.lan" /etc/pihole/custom.list || echo "192.168.3.14 mc-vanilla.lan" >> /etc/pihole/custom.list'
ssh root@ct-dns 'grep -qxF "192.168.3.14 mc-modded.lan" /etc/pihole/custom.list || echo "192.168.3.14 mc-modded.lan" >> /etc/pihole/custom.list'
ssh root@ct-dns 'pihole restartdns reload-lists 2>/dev/null || pihole restartdns'
```

If the web UI is used: Admin → Local DNS → DNS Records → add `mc-vanilla.lan → 192.168.3.14`, repeat for `mc-modded.lan`.

- [ ] **Step 3: Verify resolution from blvckmain**

```bash
dig @192.168.3.5 mc-vanilla.lan +short
dig @192.168.3.5 mc-modded.lan +short
```
Expected: both return `192.168.3.14`.

- [ ] **Step 4 (conditional): If Pi-hole config is git-tracked**

Some homelabs track custom.list in the stacks repo. Check:
```bash
grep -rn "custom\.list\|DNS Records" /home/psy/Documents/personal/infra/stacks/ct-dns/ 2>/dev/null
```
If tracked, mirror the edit there and commit:
```bash
cd /home/psy/Documents/personal/infra
git add stacks/ct-dns/...  # adjust path
git commit -m "ct-dns: add mc-vanilla.lan and mc-modded.lan records"
```

---

## Task 18: Update CLAUDE.md and docs/hardware.md

**Goal:** Document ct-games in the main infra inventory so future context windows don't miss it.

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/hardware.md`

- [ ] **Step 1: Locate the insertion point in CLAUDE.md**

The file lists CTs under `### ct-dns`, `### ct-tunnel`, etc. The new `ct-games` block goes in alphabetical-ish order matching the surrounding style — insert after `### ct-files` (since `g` follows `f`) or wherever the existing ordering places it.

Read the neighboring blocks to match tone and field layout.

- [ ] **Step 2: Add the ct-games block to CLAUDE.md**

Insert this block (match existing heading/section style exactly):

```markdown
### ct-games (LXC — VMID 112 on proxmoxmain)
- **IP:** 192.168.3.14
- **User:** root
- **OS:** Debian 13 (Trixie), unprivileged LXC
- **SSH:** Port 22, key-based auth (no password)
- **Resources:** 6 vCPU, 16384MB RAM, 4096MB swap, 40GB disk
- **Role:** Game server host. Runs two Minecraft servers (vanilla/Paper + heavy modded) plus Playit.gg agents for CGNAT-friendly public reach and itzg/mc-backup sidecars for daily on-box `.tgz` snapshots to mergerfs.
- **Stack:** `/opt/stacks/ct-games/docker-compose.yml` (local copy: `stacks/ct-games/`)
- **Ports:** 25565 (mc-vanilla game), 25566 (mc-modded game), 25575 (mc-vanilla RCON, LAN-only), 25576 (mc-modded RCON, LAN-only)
- **Config notes:** AppArmor set to unconfined + proc/sys rw mounts for Docker compatibility. Shared `gamesnet` Docker bridge for agent → MC routing. Live world data on CT's NVMe at `/opt/stacks/ct-games/data/`. Archive bind mount: `/mnt/cloud/volumes/games/archives` → `/mnt/archives` (daily `.tgz` per server, 14-day retention). Playit tunnels route external traffic (no inbound from router, CGNAT-friendly).
```

- [ ] **Step 3: Update the Network Layout section in CLAUDE.md**

Find the `ssh ...` lines enumerating CT access. Add a new line in the same block:
```
  ├── ssh ct-games       → 192.168.3.14:22   (root, key auth)
```
(Match indentation and `→` arrow of existing lines exactly.)

- [ ] **Step 4: Update the Services paragraph in CLAUDE.md**

Add a new line in the Services block:
```
Minecraft servers (vanilla + modded) run on ct-games (192.168.3.14:25565 / :25566) with Playit.gg tunnels for public reach. RCON exposed on LAN at :25575 (vanilla) / :25576 (modded).
```

- [ ] **Step 5: Add ct-games to the LXC Boot Disk Allocations table in docs/hardware.md**

Locate the table under `### LXC Boot Disk Allocations (on local-lvm)` and insert a new row (after ct-mgmt or in VMID order — match current ordering):

```
| 112  | ct-games | vm-112-disk-0 | 40GB | -% |
```

Fill in actual Data% by querying:
```bash
ssh proxmoxmain 'lvs --noheadings -o data_percent local-lvm/vm-112-disk-0 2>/dev/null'
```

Also update the **CTs:** list near the top of `### proxmoxmain` section in CLAUDE.md to include `ct-games (VMID 112)`.

- [ ] **Step 6: Commit**

```bash
cd /home/psy/Documents/personal/infra
git add CLAUDE.md docs/hardware.md
git commit -m "Document ct-games in CLAUDE.md and hardware inventory"
```

---

## Task 19: Final acceptance sweep

**Goal:** Walk the spec's Acceptance Criteria section and tick each off. Record any deviation.

**Files:** None (verification)

- [ ] **Step 1: `pct list` shows VMID 112 running**

```bash
ssh proxmoxmain 'pct list | grep 112'
```
Expected: line shows `112 running ct-games`.

- [ ] **Step 2: All five services healthy**

```bash
ssh root@ct-games 'cd /opt/stacks/ct-games && docker compose ps --format "table {{.Name}}\t{{.State}}\t{{.Status}}"'
```
Expected: five rows, all `running`, MC containers `healthy`.

- [ ] **Step 3: Playit external join works for both servers**

Already verified in Task 12 — re-check only if anything has changed since.

- [ ] **Step 4: LAN TCP + RCON**

```bash
nc -zv 192.168.3.14 25565 && nc -zv 192.168.3.14 25566
mcrcon -H 192.168.3.14 -P 25575 -p '<VANILLA>' list
mcrcon -H 192.168.3.14 -P 25576 -p '<MODDED>' list
```
Expected: all succeed.

- [ ] **Step 5: Gatus greens + Telegram round-trip**

Already verified in Tasks 13–14.

- [ ] **Step 6: `infra` CLI sees new services without code change**

```bash
cd /home/psy/Documents/personal/infra
infra ls | grep -E "mc-vanilla|mc-modded|playit-agent|backup-vanilla|backup-modded"
infra status | grep ct-games
infra ct status | grep 112
```
Expected: all five services listed; `ct-games` row in status; VMID 112 in `ct status`.

- [ ] **Step 7: On-box archive verified (Task 16) + ct-backup captures /opt/stacks/ct-games (Task 15)**

Already done — re-check once more that latest restic snapshot includes ct-games data:
```bash
ssh root@ct-backup 'source /etc/restic/env && restic ls latest | grep ct-games/data | head -5'
```
Expected: at least a few files from `/opt/stacks/ct-games/data/vanilla/` or `/modded/` listed.

- [ ] **Step 8: Open Decisions resolved or deferred**

Review the spec's "Open Decisions Before First Deploy":
- Modded loader + modpack → what did you pick? If you deployed with default `TYPE=FORGE` and empty modpack, note that in a follow-up issue for operator attention.
- Vanilla flavor → deployed as PAPER (spec default).
- Server identity (MOTD, seeds, OPS, whitelist) → these can be tuned via env + `infra deploy`.
- Playit hostnames captured → yes, from Task 2.

Record any pending decisions in a follow-up note / issue; they're operator-facing, not blockers for plan completion.

- [ ] **Step 9: Final commit with summary**

No repo changes required here if all prior tasks committed cleanly. Confirm the git log contains the expected sequence:
```bash
cd /home/psy/Documents/personal/infra
git log --oneline -10
```
Expected: commits from Tasks 8, 13, 15 (conditional), 17 (conditional), 18. The spec commit from brainstorming is at the bottom of this sequence.

---

## Rollback guide

If anything goes wrong and you need to start over cleanly:

```bash
# Stop + remove CT (preserves mergerfs archives, preserves repo commits)
ssh proxmoxmain 'pct stop 112 && pct destroy 112 --purge'

# If you want to nuke the archive dir too (destructive):
ssh proxmoxmain 'rm -rf /mnt/cloud/volumes/games'

# Revert the repo commits (chain: ct-games scaffold + gatus + ct-backup + claude.md)
cd /home/psy/Documents/personal/infra
git log --oneline | head -6   # identify the range
git reset --hard <commit-before-task-8>   # CAUTION: destructive
```

Playit.gg tunnels on the dashboard remain; delete them from the UI if you want to fully back out.

---

## Post-launch follow-ups (not part of this plan)

These are acknowledged in the spec as out-of-scope — track separately after the stack is live:

1. **`infra rcon <service> <cmd>` subcommand** — small Go PR in `cli/cmd/`; parallels existing `infra logs` / `infra restart` patterns.
2. **MC status tiles on the ct-mgmt dashboard** — ~10-line edit in `stacks/ct-mgmt/dashboard-src/`.
3. **Native Minecraft-protocol Gatus check** — upgrade TCP check to MC-aware check for player-count visibility.
4. **First non-MC game** (Valheim / Satisfactory / Palworld / etc.) — follow the "Pattern per new game" section of the spec; add compose services in the same stack.
