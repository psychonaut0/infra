# Infrastructure

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
- **Role:** Primary Proxmox hypervisor node. Clustered with proxmoxnode. Authorized keys are shared across the cluster.
- **CTs:** ct-tunnel (VMID 103), ct-nvr (VMID 104), ct-media (VMID 105), ct-photos (VMID 106), ct-files (VMID 107), ct-mgmt (VMID 108)
- **Storage:** See `docs/hardware.md` for full disk and storage layout.

### proxmoxnode
- **IP:** 192.168.3.3
- **User:** root
- **OS:** Proxmox VE
- **SSH:** Port 22, key-based auth (no password)
- **CPU:** Intel N100 (4C/4T)
- **RAM:** 16GB
- **Role:** Secondary Proxmox cluster node. Shares authorized_keys with proxmoxmain via pmxcfs.
- **VMs:** Home Assistant OS (VMID 101)
- **CTs:** ct-dns (VMID 102)
- **Storage:** 476GB SSD, ~300GB LVM thin available.

### Home Assistant OS (VM — VMID 101 on proxmoxnode)
- **IP:** 192.168.3.10
- **OS:** Home Assistant OS (HAOS)
- **Resources:** 4 vCPU, 8192MB RAM, 50GB disk
- **Machine:** q35 with OVMF/UEFI
- **Role:** Home automation. Runs Home Assistant.
- **Ports:** 8123 (HTTP UI)
- **Config notes:** Auto-start on boot (`onboot: 1`). Installed via Proxmox community script.

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

### ct-mgmt (LXC — VMID 108 on proxmoxmain)
- **IP:** 192.168.3.12
- **User:** root
- **OS:** Debian 13 (Trixie), unprivileged LXC
- **SSH:** Port 22, key-based auth (no password)
- **Resources:** 1 vCPU, 512MB RAM, 256MB swap, 4GB disk
- **Role:** Management and monitoring. Runs Portainer CE for container orchestration across all CTs.
- **Stack:** `/opt/stacks/ct-mgmt/docker-compose.yml` (local copy: `stacks/ct-mgmt/`)
- **Ports:** 9443 (Portainer HTTPS UI), 8000 (Edge Agent communication)
- **Config notes:** AppArmor set to unconfined for Docker compatibility. Portainer manages remote environments via their portainer-agent (port 9001).

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
  └── ssh ct-mgmt        → 192.168.3.12:22   (root, key auth)
```

## Services

Portainer CE runs on ct-mgmt (https://portainer.lan or https://192.168.3.12:9443) and manages container environments across CTs via portainer-agent (port 9001).
Frigate NVR runs on ct-nvr (https://nvr.lan or https://192.168.3.7:8971) with iGPU-accelerated video decoding.
Jellyfin media server runs on ct-media (https://jellyfin.lan or http://192.168.3.8:8096) with iGPU hardware transcoding, alongside Sonarr, Radarr, Deluge, Prowlarr, and FlareSolverr.
Samba + FileBrowser run on ct-files (https://files.lan or http://192.168.3.11:8080) for SMB file shares and web-based file management.
Home Assistant runs on proxmoxnode (https://homeassistant.lan or http://192.168.3.10:8123) for home automation.
Proxmox VE runs on proxmoxmain (https://proxmox.lan or https://192.168.3.2:8006) and proxmoxnode (https://proxmox-node.lan or https://192.168.3.3:8006).
Immich photo management runs on ct-photos (https://immich.lan or http://192.168.3.9:2283) with iGPU ML inference.
Full inventory in `docs/services.md`. Hardware and storage details in `docs/hardware.md`.
