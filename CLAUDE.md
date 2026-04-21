# Infrastructure

## Upstream / External Access

- **ISP:** <ISP> FWA (wireless). Static public IPv4 **`<PUBLIC_IP>`** ("IP Pubblico Statico" — explicitly requested, confirmed **not CGNAT** via external reachability test 2026-04-21).
- **Topology:** <ISP_ROUTER> (`192.168.0.1`, WAN = public IP) → UniFi Gateway (WAN `192.168.0.2`, LAN gateway `192.168.1.1`) → LAN `192.168.1.0/24`.
- **Inbound architecture:** <ISP_ROUTER> is in **DMZ Host → `192.168.0.2`** (UniFi WAN). All inbound port-forward rules live on **UniFi only** — never touch the <ISP_ROUTER>. <ISP_ROUTER> firewall is off (no extra filtering beyond default NAT). If inbound breaks: check (a) UniFi PF rule enabled, (b) <ISP_ROUTER> DMZ still pointed at 192.168.0.2 (<ISP_ROUTER> has been known to drop DMZ across firmware updates), (c) UniFi firewall not overriding the PF.
- **Personal domain:** `<PERSONAL_DOMAIN>` (Cloudflare Registrar + Cloudflare DNS). Records pointing at home public IP must be **DNS-only (grey cloud)** — CF free-tier proxy only supports HTTP(S), not arbitrary TCP/UDP.
- **Public endpoints currently exposed via UniFi port-forward:**
  - `mc.<PERSONAL_DOMAIN>:25565/tcp` → `192.168.3.14:25565` (mc-vanilla on ct-games)

## Network & Devices

### blvckmain (Main PC)
- **Hostname:** blvckmain (resolves to 192.168.1.110)
- **User:** psy
- **OS:** Desktop (Windows/Linux)
- **SSH:** Port 22, key-based auth (no password)
- **Role:** Primary workstation. Has SSH access to all other machines.

### proxmoxmain
- **IP:** 192.168.3.2
- **User:** root
- **OS:** Proxmox VE
- **SSH:** Port 22, key-based auth (no password)
- **CPU:** Intel i5-10400 @ 2.90GHz (6C/12T)
- **RAM:** 32GB
- **Role:** Primary Proxmox hypervisor node. Clustered with proxmoxnode. Authorized keys are shared across the cluster. Hosts all bulk storage — mergerfs pool `/mnt/cloud` (exposed as the `cloud` PVE dir pool) and `nvr-data` LVM thin — bind-mounted into local CTs.
- **CTs:** ct-tunnel (VMID 103), ct-nvr (VMID 104), ct-media (VMID 105), ct-photos (VMID 106), ct-files (VMID 107), ct-mgmt (VMID 108), ct-backup (VMID 109), ct-tools (VMID 110), ct-games (VMID 112)
- **Storage:** See `docs/hardware.md` for full disk and storage layout.

### proxmoxnode
- **IP:** 192.168.3.3
- **User:** root
- **OS:** Proxmox VE
- **SSH:** Port 22, key-based auth (no password)
- **CPU:** Intel N100 (4C/4T)
- **RAM:** 16GB
- **Role:** Secondary Proxmox cluster node. Shares authorized_keys with proxmoxmain via pmxcfs.
- **VMs:** Home Assistant OS (VMID 101) — **retired 2026-04-17**, pending destroy after ≥1 week of ct-ha stability
- **CTs:** ct-dns (VMID 102), ct-ha (VMID 111)
- **Storage:** See `docs/hardware.md` for full disk and storage layout.

### Home Assistant OS (VM — VMID 101 on proxmoxnode) — RETIRED
- **Status:** Powered off 2026-04-17 after migration to ct-ha (HA Container). Kept as rollback for ≥1 week, then `qm destroy 101 --purge`.
- **Note:** Its IP `192.168.3.10` and MAC `<HAOS_INHERITED_MAC>` were transferred to ct-ha because the router has a per-client rule granting HAOS access to the IoT subnet (192.168.2.0/24). Do not reuse that IP/MAC for other hosts.
- **Backup:** `pre-migration` tar on ct-files at `/mnt/cloud/volumes/samba/data/psy/ha-backups/pre-migration.tar`.

### ct-dns (LXC — VMID 102 on proxmoxnode)
- **IP:** 192.168.3.5
- **User:** root
- **OS:** Debian 13 (Trixie), unprivileged LXC
- **SSH:** Port 22, key-based auth (no password)
- **Resources:** 1 vCPU, 512MB RAM, 256MB swap, 4GB disk
- **Role:** DNS server. Runs Pi-hole via Docker Compose.
- **Stack:** `/opt/stacks/ct-dns/docker-compose.yml` (local copy: `stacks/ct-dns/`)
- **Config notes:** AppArmor set to unconfined + proc/sys rw mounts for Docker compatibility. DNS listening mode set to ALL to accept queries from all subnets.

### ct-tunnel (LXC — VMID 103 on proxmoxmain)
- **IP:** 192.168.3.6
- **User:** root
- **OS:** Debian 13 (Trixie), unprivileged LXC
- **SSH:** Port 22, key-based auth (no password)
- **Resources:** 1 vCPU, 256MB RAM, 128MB swap, 2GB disk
- **Role:** Cloudflare Tunnel endpoint. Runs cloudflared for selective public access to internal services, replacing the old nginx-proxy-manager + cloudflare-ddns + port forwarding setup.
- **Stack:** `/opt/stacks/ct-tunnel/docker-compose.yml` (local copy: `stacks/ct-tunnel/`)
- **Config notes:** AppArmor set to unconfined for Docker compatibility. Uses `network_mode: host` for cloudflared. Tunnel token stored in `/opt/stacks/ct-tunnel/.env`.

### ct-nvr (LXC — VMID 104 on proxmoxmain)
- **IP:** 192.168.3.7
- **User:** root
- **OS:** Debian 13 (Trixie), privileged LXC
- **SSH:** Port 22, key-based auth (no password)
- **Resources:** 2 vCPU, 4096MB RAM, 1024MB swap, 24GB disk
- **Role:** NVR (Network Video Recorder). Runs Frigate with iGPU passthrough for hardware video decoding and AI object detection.
- **Stack:** `/opt/stacks/ct-nvr/docker-compose.yml` (local copy: `stacks/ct-nvr/`)
- **Ports:** 8971 (Frigate HTTPS UI), 8554 (RTSP), 8555 (WebRTC)
- **Config notes:** Privileged CT for device access. iGPU passthrough via `lxc.cgroup2.devices.allow: c 226:* rwm` and `/dev/dri` bind mount. NVR data on LVM thin volume (`nvr-data:vm-100-disk-0`) mounted on host via kpartx at `/mnt/nvr-data` and bind-mounted into CT. AppArmor unconfined + proc/sys rw mount for Docker compatibility. Systemd service `mnt-nvr-data.service` on proxmoxmain handles persistent mount across reboots.

### ct-media (LXC — VMID 105 on proxmoxmain)
- **IP:** 192.168.3.8
- **User:** root
- **OS:** Debian 13 (Trixie), privileged LXC
- **SSH:** Port 22, key-based auth (no password)
- **Resources:** 4 vCPU, 8192MB RAM, 2048MB swap, 16GB disk
- **Role:** Media server. Runs Jellyfin (with iGPU passthrough for hardware transcoding), Sonarr, Radarr, Deluge, Prowlarr, and FlareSolverr.
- **Stack:** `/opt/stacks/ct-media/docker-compose.yml` (local copy: `stacks/ct-media/`)
- **Ports:** 8096 (Jellyfin HTTP), 8920 (Jellyfin HTTPS), 8989 (Sonarr), 7878 (Radarr), 8112 (Deluge), 9696 (Prowlarr), 8191 (FlareSolverr)
- **Config notes:** Privileged CT for device access. iGPU passthrough via `lxc.cgroup2.devices.allow: c 226:* rwm` and `/dev/dri` bind mount. Media data on mergerfs pool (`/mnt/cloud/volumes/mediaserver`) bind-mounted into CT at `/mnt/mediaserver`. AppArmor unconfined + proc/sys rw mount for Docker compatibility.

### ct-files (LXC — VMID 107 on proxmoxmain)
- **IP:** 192.168.3.11
- **User:** root
- **OS:** Debian 13 (Trixie), privileged LXC
- **SSH:** Port 22, key-based auth (no password)
- **Resources:** 1 vCPU, 1024MB RAM, 512MB swap, 4GB disk
- **Role:** File server. Runs Samba for SMB shares and FileBrowser for web-based file management.
- **Stack:** `/opt/stacks/ct-files/docker-compose.yml` (local copy: `stacks/ct-files/`)
- **Ports:** 139/445 (Samba SMB), 8080 (FileBrowser HTTP)
- **Config notes:** Privileged CT for clean UID mapping on shared storage. Full mergerfs pool (`/mnt/cloud`) bind-mounted into CT. AppArmor unconfined for Docker compatibility. Samba config/data/users from `/mnt/cloud/volumes/samba/`.

### ct-games (LXC — VMID 112 on proxmoxmain)
- **IP:** 192.168.3.14
- **User:** root
- **OS:** Debian 13 (Trixie), unprivileged LXC
- **SSH:** Port 22, key-based auth (no password)
- **Resources:** 6 vCPU, 16384MB RAM, 4096MB swap, 40GB disk
- **Role:** Game server host. Runs two Minecraft servers (vanilla/Paper + heavy modded) plus two itzg/mc-backup sidecars (daily `.tgz` to mergerfs). Public reach for mc-vanilla is via UniFi port-forward → `mc.<PERSONAL_DOMAIN>:25565` (direct from static public IP — see top-level *Upstream / External Access*). A Playit.gg agent remains in the stack as transitional fallback since 2026-04-21 and will be removed once the direct endpoint is proven stable.
- **Stack:** `/opt/stacks/ct-games/docker-compose.yml` (local copy: `stacks/ct-games/`)
- **Ports:** 25565 (mc-vanilla game, also publicly reachable via UniFi PF), 25566 (mc-modded game), 25575 (mc-vanilla RCON, LAN-only), 25576 (mc-modded RCON, LAN-only)
- **Config notes:** AppArmor unconfined + proc/sys rw for Docker-in-LXC. Shared `gamesnet` Docker bridge for agent→MC routing. Live world data on CT's NVMe at `/opt/stacks/ct-games/data/`. Archive bind mount: `/mnt/cloud/volumes/games/archives` → `/mnt/archives` (daily `.tgz` per server, 14-day retention via `PRUNE_BACKUPS_DAYS`). Playit agent (transitional): reads `SECRET_KEY` from `PLAYIT_SECRET` in `.env`; two tunnels attached server-side.

### ct-mgmt (LXC — VMID 108 on proxmoxmain)
- **IP:** 192.168.3.12
- **User:** root
- **OS:** Debian 13 (Trixie), unprivileged LXC
- **SSH:** Port 22, key-based auth (no password)
- **Resources:** 1 vCPU, 512MB RAM, 256MB swap, 4GB disk
- **Role:** Management and monitoring. Runs Portainer CE, the custom Home Lab dashboard, Gatus uptime monitoring, and Caddy reverse proxy for all `.lan` services.
- **Stack:** `/opt/stacks/ct-mgmt/docker-compose.yml` (local copy: `stacks/ct-mgmt/`)
- **Ports:** 80/443 (Caddy), 3000 (dashboard), 8080 (Gatus status page), 9443 (Portainer HTTPS UI), 8000 (Edge Agent communication)
- **Dashboard:** Custom Preact + Bun SSR app with live health checks and Proxmox resource monitoring (source: `stacks/ct-mgmt/dashboard-src/`). Requires PVE API token in `dashboard-src/.env` (gitignored).
- **Gatus:** Uptime monitoring with 3-tier checks (critical/important/background) and Telegram alerting. Config: `stacks/ct-mgmt/gatus/config.yaml`. Telegram credentials in `gatus/.env` (gitignored).
- **Config notes:** AppArmor set to unconfined for Docker compatibility. Portainer manages remote environments via their portainer-agent (port 9001).

### ct-ha (LXC — VMID 111 on proxmoxnode)
- **IP:** 192.168.3.10 (inherited from retired HAOS VM — see note below)
- **MAC:** `<HAOS_INHERITED_MAC>` (inherited from retired HAOS VM)
- **User:** root
- **OS:** Debian 13 (Trixie), unprivileged LXC
- **SSH:** Port 22, key-based auth (no password)
- **Resources:** 2 vCPU, 4096MB RAM, 1024MB swap, 16GB disk
- **Role:** Home automation. Runs Home Assistant Container + Mosquitto MQTT broker.
- **Stack:** `/opt/stacks/ct-ha/docker-compose.yml` (local copy: `stacks/ct-ha/`)
- **Ports:** 8123 (HA HTTP UI), 1883 (Mosquitto MQTT)
- **Config notes:** AppArmor unconfined + proc/sys rw mount for Docker compatibility. HA uses `network_mode: host` for mDNS/zeroconf. Mosquitto passwd file at `stacks/ct-ha/mosquitto/config/passwd` (gitignored). MQTT broker reachable from HA at `127.0.0.1:1883` and from LAN at `192.168.3.10:1883`.
- **Network note:** The router has a per-client rule granting HAOS access to the IoT subnet (192.168.2.0/24). To inherit that access without router-side changes, ct-ha was given HAOS's original MAC + IP. Any replacement for ct-ha in the future should also inherit those identifiers (or update the router rule).

### ct-tools (LXC — VMID 110 on proxmoxmain)
- **IP:** 192.168.3.15
- **User:** root
- **OS:** Debian 13 (Trixie), unprivileged LXC
- **SSH:** Port 22, key-based auth (no password)
- **Resources:** 2 vCPU, 2048MB RAM, 512MB swap, 8GB disk
- **Role:** Utility services. Runs ESPHome dashboard for IoT device firmware management.
- **Stack:** `/opt/stacks/ct-tools/docker-compose.yml` (local copy: `stacks/ct-tools/`)
- **Ports:** 6052 (ESPHome HTTP UI)
- **Config notes:** AppArmor unconfined + proc/sys rw mount for Docker compatibility. ESPHome uses `network_mode: host` for mDNS device discovery.

### ct-photos (LXC — VMID 106 on proxmoxmain)
- **IP:** 192.168.3.9
- **User:** root
- **OS:** Debian 13 (Trixie), privileged LXC
- **SSH:** Port 22, key-based auth (no password)
- **Resources:** 4 vCPU, 8192MB RAM, 2048MB swap, 16GB disk
- **Role:** Photo management. Runs Immich with iGPU passthrough for ML inference.
- **Stack:** `/opt/stacks/ct-photos/docker-compose.yml` (local copy: `stacks/ct-photos/`)
- **Ports:** 2283 (Immich HTTP UI)
- **Config notes:** Privileged CT for device access. iGPU passthrough for ML. Immich data on mergerfs pool (`/mnt/cloud/volumes/mediaserver/immich`) bind-mounted into CT at `/mnt/immich`. DB password in `/opt/stacks/ct-photos/.env`.

### ct-backup (LXC — VMID 109 on proxmoxmain)
- **IP:** 192.168.3.13
- **User:** root
- **OS:** Debian 13 (Trixie), privileged LXC
- **SSH:** Port 22, key-based auth (no password)
- **Resources:** 1 vCPU, 512MB RAM, 256MB swap, 4GB disk
- **Role:** Off-site backup runner. Runs restic nightly against Backblaze B2, pulling .env files + Docker named volumes + Immich Postgres dump + /etc/pve + host config from all other CTs and both Proxmox nodes, plus bind-mounted bulk data (Immich, samba/psy, *arr configs, Jellyfin config, Frigate config) and the full /opt/stacks tree for ct-ha and ct-tools.
- **Stack:** `/opt/stacks/ct-backup/` (local copy: `stacks/ct-backup/`) — no Docker; scripts + systemd units installed natively.
- **Ports:** 80 (Caddy status endpoint at `backup.lan`)
- **Schedule:** nightly 03:00, weekly prune Sun 04:00, monthly partial check 1st of month 05:00. Triggered by systemd timers; status.json served for Gatus freshness check.
- **Config notes:** Privileged LXC (to read host files with arbitrary ownership without idmap gymnastics). Read-only bind mounts for bulk data sources. SSH key in `/root/.ssh/id_ed25519` with forced-command restrictions on every target via `backup-dispatch.sh`. Secrets in `/etc/restic/` (repo password, B2 creds, Telegram creds). Caddy systemd override at `/etc/systemd/system/caddy.service.d/lxc-override.conf` disables `PrivateTmp`/`ProtectSystem` for LXC compatibility.

### Termux (Nothing Phone A024)
- **User:** u0_a416
- **OS:** Android 16 (API 36), Termux
- **SSH key:** `ssh-ed25519 ...termux@nothing-a024`
- **Role:** Mobile dev environment. Has passwordless SSH to all machines above.

## Network Layout

```
Termux (phone)
  ├── ssh blvckmain      → 192.168.1.110:22  (psy, key auth)
  ├── ssh proxmoxmain    → 192.168.3.2:22    (root, key auth)
  ├── ssh proxmoxnode    → 192.168.3.3:22    (root, key auth)
blvckmain (main PC)
  ├── ssh ct-dns         → 192.168.3.5:22    (root, key auth)
  ├── ssh ct-tunnel      → 192.168.3.6:22    (root, key auth)
  ├── ssh ct-nvr         → 192.168.3.7:22    (root, key auth)
  ├── ssh ct-media       → 192.168.3.8:22    (root, key auth)
  ├── ssh ct-photos      → 192.168.3.9:22    (root, key auth)
  ├── ssh ct-files       → 192.168.3.11:22   (root, key auth)
  ├── ssh ct-mgmt        → 192.168.3.12:22   (root, key auth)
  ├── ssh ct-games       → 192.168.3.14:22   (root, key auth)
  ├── ssh ct-ha          → 192.168.3.10:22   (root, key auth)
  ├── ssh ct-tools       → 192.168.3.15:22   (root, key auth)
  └── ssh ct-backup      → 192.168.3.13:22   (root, key auth)
```

## Services

Home Lab dashboard runs on ct-mgmt (http://home.lan) — custom Preact + Bun SSR app with service health checks and live Proxmox resource stats (node CPU/RAM, SSD + mergerfs storage).
Portainer CE runs on ct-mgmt (https://portainer.lan or https://192.168.3.12:9443) and manages container environments across CTs via portainer-agent (port 9001).
Gatus uptime monitoring runs on ct-mgmt (http://status.lan or http://192.168.3.12:8080) with 3-tier service checks and Telegram alerts via @blvckhomelab_bot.
Frigate NVR runs on ct-nvr (https://nvr.lan or https://192.168.3.7:8971) with iGPU-accelerated video decoding.
Jellyfin media server runs on ct-media (https://jellyfin.lan or http://192.168.3.8:8096) with iGPU hardware transcoding, alongside Sonarr, Radarr, Deluge, Prowlarr, and FlareSolverr.
Samba + FileBrowser run on ct-files (https://files.lan or http://192.168.3.11:8080) for SMB file shares and web-based file management.
Home Assistant Container runs on ct-ha (https://homeassistant.lan or http://192.168.3.10:8123) for home automation, alongside Mosquitto MQTT broker on the same host.
ESPHome dashboard runs on ct-tools (http://esphome.lan or http://192.168.3.15:6052) for IoT device firmware builds and OTA updates.
Proxmox VE runs on proxmoxmain (https://proxmox.lan or https://192.168.3.2:8006) and proxmoxnode (https://proxmox-node.lan or https://192.168.3.3:8006).
Immich photo management runs on ct-photos (https://immich.lan or http://192.168.3.9:2283) with iGPU ML inference.
Minecraft servers run on ct-games (192.168.3.14:25565 for vanilla/Paper, :25566 for modded). Vanilla is publicly reachable at `mc.<PERSONAL_DOMAIN>:25565` via UniFi port-forward from the static public IP. Playit.gg agent remains as transitional fallback and will be retired. LAN RCON on :25575 (vanilla) / :25576 (modded). Daily `.tgz` archives on mergerfs at `/mnt/cloud/volumes/games/archives/<server>/`.
Hardware and storage details in `docs/hardware.md`.

## CLI

`infra` is a Go CLI at `~/.local/bin/infra` that wraps common SSH + docker compose operations across the fleet. Source at `cli/`. Commands: `ls`, `status`, `logs <service>`, `restart <service>`, `deploy <service|ct>`, `ct status`, `update` (self-update), `version`. Auto-discovers services from `stacks/ct-*/docker-compose.yml` at each run. Build/install with `cd cli && make install`; self-update with `infra update`. Design: `docs/superpowers/specs/2026-04-18-infra-cli-design.md`.
