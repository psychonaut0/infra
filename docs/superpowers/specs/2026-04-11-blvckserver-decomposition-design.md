# blvckserver Decomposition — Design Spec

## Overview

Decompose the monolithic blvckserver VM (VMID 100, Arch Linux, 29 Docker containers) into 6 purpose-built LXC containers across the Proxmox cluster. Migration follows a gradual approach in 3 phases, keeping blvckserver running as fallback until all services are verified on the new CTs.

## Current State

### Infrastructure
- **proxmoxmain** (i5-10400, 32GB RAM, 12T): Primary hypervisor. Hosts blvckserver VM.
- **proxmoxnode** (N100, 16GB RAM, 4C): Secondary node. Hosts Home Assistant (VMID 101).
- **blvckserver** (VMID 100): 12 vCPU, 24GB RAM, iGPU passthrough (Intel UHD 630). All services run as Docker containers managed via Portainer.

### Storage
- **NVMe SSD (466GB):** Proxmox OS + local-lvm (VM boot disks)
- **HDD 456GB (sdc):** nvr-data LVM thin — Proxmox-managed, Frigate recordings
- **HDD 4TB (sda):** Raw passthrough to VM — data disk
- **HDD 1TB (sdb):** Raw passthrough to VM — data disk
- **mergerfs** pools the 4TB + 1TB into `/mnt/cloud` inside the VM (NVR disk is also in the pool currently but shouldn't be — it has its own mount)
- All service data lives under `/mnt/cloud/volumes/<service>/`

### Problems
- All services in one VM — no isolation, single point of failure
- Raw HDD passthrough — invisible to Proxmox, can't share or manage
- iGPU locked to one VM via hostpci0 — can't share across guests
- No per-service snapshots or backups
- 3 services crash-looping (nextcloud, prometheus, cloudflare-ddns)

## Target State

### Architecture

6 LXC containers, each running Docker inside (nesting enabled). Services managed via `docker-compose.yml` files stored in the infra git repo.

```
proxmoxnode
  └── ct-dns (pihole)

proxmoxmain
  ├── ct-tunnel (cloudflared)
  ├── ct-nvr (frigate)
  ├── ct-media (jellyfin + *arr stack)
  ├── ct-photos (immich)
  └── ct-files (samba + file browser)

Storage (proxmoxmain host):
  /mnt/cloud-1 (1TB HDD) ─┐
  /mnt/cloud-2 (4TB HDD) ─┴── mergerfs → /mnt/cloud
  nvr-data (456GB LVM thin) ── direct mount for ct-nvr
```

### Container Specifications

#### ct-dns (proxmoxnode)
- **Purpose:** DNS resolution for the entire network
- **Services:** pihole (Docker)
- **Resources:** 1 vCPU, 512MB RAM, 4GB boot disk on local-lvm
- **Storage:** None beyond boot disk (config is tiny)
- **GPU:** None
- **Network:** vmbr0, static IP on 192.168.3.0/24
- **Why separate node:** DNS must survive proxmoxmain maintenance/reboots. proxmoxnode is the always-on home essentials node (DNS + Home Assistant).

#### ct-tunnel (proxmoxmain)
- **Purpose:** Public access to select services via Cloudflare Tunnel
- **Services:** cloudflared (Docker)
- **Resources:** 1 vCPU, 256MB RAM, 2GB boot disk on local-lvm
- **Storage:** None beyond boot disk
- **GPU:** None
- **Network:** vmbr0, static IP on 192.168.3.0/24
- **Notes:** Replaces the old nginx-proxy-manager + cloudflare-ddns + port forwarding approach. Outbound-only connection to Cloudflare, no inbound ports needed.

#### ct-nvr (proxmoxmain)
- **Purpose:** Security camera recording and AI object detection
- **Services:** frigate (Docker)
- **Resources:** 2 vCPU, 4GB RAM, 8GB boot disk on local-lvm
- **Storage:** nvr-data volume mount for recordings
- **GPU:** `/dev/dri/renderD128` passthrough for hardware decoding/detection
- **Network:** vmbr0, static IP on 192.168.3.0/24
- **Notes:** First CT to use iGPU sharing — validates the approach for ct-media and ct-photos.

#### ct-media (proxmoxmain)
- **Purpose:** Media server — streaming, downloading, indexing
- **Services:** jellyfin, sonarr, radarr, deluge, prowlarr, flaresolverr (all Docker)
- **Resources:** 4 vCPU, 8GB RAM, 16GB boot disk on local-lvm
- **Storage:** Bind mounts from host mergerfs:
  - `/mnt/cloud/volumes/mediaserver/movies` → movies library
  - `/mnt/cloud/volumes/mediaserver/series` → TV series library
  - `/mnt/cloud/volumes/mediaserver/downloads` → download staging
  - `/mnt/cloud/volumes/mediaserver/jellyfin` → jellyfin config
  - `/mnt/cloud/volumes/mediaserver/<service>` → per-service configs
- **GPU:** `/dev/dri/renderD128` passthrough for Jellyfin hardware transcoding
- **Network:** vmbr0, static IP on 192.168.3.0/24
- **Notes:** Services are tightly coupled — sonarr/radarr trigger deluge, prowlarr manages indexers, jellyfin reads the media files. Single CT avoids cross-CT storage complexity.

#### ct-photos (proxmoxmain)
- **Purpose:** Photo management with AI-powered search
- **Services:** immich-server, immich-machine-learning, postgres (pgvecto-rs), redis (all Docker)
- **Resources:** 4 vCPU, 8GB RAM, 16GB boot disk on local-lvm
- **Storage:** Bind mounts from host mergerfs:
  - `/mnt/cloud/volumes/mediaserver/immich/data` → photo uploads
  - `/mnt/cloud/volumes/mediaserver/immich/cache` → ML cache
  - `/mnt/cloud/volumes/mediaserver/immich/db` → postgres data
- **GPU:** `/dev/dri/renderD128` passthrough for ML inference
- **Network:** vmbr0, static IP on 192.168.3.0/24

#### ct-files (proxmoxmain)
- **Purpose:** File sharing — network drives and web file browser
- **Services:** samba, file browser (e.g., filebrowser — Docker)
- **Resources:** 1 vCPU, 1GB RAM, 4GB boot disk on local-lvm
- **Storage:** Bind mount of `/mnt/cloud` (full pool access for Samba shares and web browsing)
- **GPU:** None
- **Network:** vmbr0, static IP on 192.168.3.0/24
- **Notes:** File browser replaces Nextcloud — lightweight web UI for phone/browser file access.

### Resource Budget

#### proxmoxmain (32GB RAM, 12 threads)
| Guest | vCPU | RAM |
|-------|------|-----|
| ct-tunnel | 1 | 256MB |
| ct-nvr | 2 | 4GB |
| ct-media | 4 | 8GB |
| ct-photos | 4 | 8GB |
| ct-files | 1 | 1GB |
| **Total** | **12** | **21.3GB** |
| **Remaining** | — | **~10GB** (headroom + Proxmox host) |

#### proxmoxnode (16GB RAM, 4 cores)
| Guest | vCPU | RAM |
|-------|------|-----|
| Home Assistant (VM 101) | — | 8GB |
| ct-dns | 1 | 512MB |
| **Total** | **1** | **8.5GB** |
| **Remaining** | **3** | **~7GB** |

### Storage Layout (post-migration)

#### proxmoxmain host
```
/mnt/cloud-1          ext4    (1TB HDD, UUID: 64d9ab64...)
/mnt/cloud-2          ext4    (4TB HDD, UUID: f635f68d...)
/mnt/cloud            mergerfs (pool of cloud-1 + cloud-2)
```

mergerfs installed via `.deb` from GitHub releases. fstab entry:
```
/mnt/cloud-1:/mnt/cloud-2  /mnt/cloud  fuse.mergerfs  allow_other,fsname=cloud,category.create=mfs,use_ino,dropcacheonclose=true,cache.files=partial  0 0
```

#### Bind mounts into CTs
Configured in each CT's Proxmox config (`/etc/pve/lxc/<vmid>.conf`):
```
mp0: /mnt/cloud/volumes/mediaserver,mp=/mnt/data
```
Specific mount paths per CT documented above.

### Network

- **Topology:** Flat — all CTs on vmbr0, 192.168.3.0/24 subnet
- **Internal access:** Tailscale → CT IP:port
- **Public access:** Cloudflare Tunnel (ct-tunnel) → maps subdomains to internal CT:port
- **DNS:** ct-dns (pihole) on proxmoxnode — network clients point here
- **Future:** VLAN segmentation by role (services, management, IoT/NVR)

### Access Model

| Method | Scope | How |
|--------|-------|-----|
| Tailscale | All services, internal | Connect to Tailscale → access via 192.168.3.x:port |
| Cloudflare Tunnel | Select public services | cloudflared maps `sub.domain.com` → internal CT:port |
| SSH | Management | Key-based to each CT from blvckmain/Termux |

Public services (via Cloudflare Tunnel — configured when needed):
- Portfolio site (future rebuild)
- Minecraft server (future)
- File/link sharing (future)

### Compose File Organization

All compose files stored in the infra git repo under `stacks/`:
```
infra/
  stacks/
    ct-dns/
      docker-compose.yml
    ct-tunnel/
      docker-compose.yml
    ct-nvr/
      docker-compose.yml
    ct-media/
      docker-compose.yml
    ct-photos/
      docker-compose.yml
    ct-files/
      docker-compose.yml
```

Each CT clones or pulls the repo (or the compose files are deployed via rsync/scp). Services are managed with `docker compose up -d` and `docker compose pull`.

## Migration Plan

### Phase 1: No-disk services
**Scope:** ct-dns, ct-tunnel
**Downtime:** Zero — old services stay up during setup, traffic switches after verification.

Steps:
1. Create ct-dns on proxmoxnode (LXC, Docker, pihole compose)
2. Create ct-tunnel on proxmoxmain (LXC, Docker, cloudflared compose)
3. Verify pihole resolves correctly
4. Update network DNS (UniFi gateway 192.168.1.1) to point to ct-dns IP
5. Set up Cloudflare Tunnel with initial config (no public services yet)
6. Stop old pihole, cloudflare-ddns, wireguard, NPM containers on blvckserver
7. Remove nginx-proxy-manager stack from blvckserver

### Phase 2: NVR (independent disk)
**Scope:** ct-nvr
**Downtime:** Minutes — cameras stop recording during switchover.
**Prerequisite:** Phase 1 complete.

Steps:
1. Create ct-nvr on proxmoxmain (LXC, Docker, privileged for device access)
2. Configure iGPU passthrough (`/dev/dri/*` in CT config)
3. Mount nvr-data storage into CT
4. Copy Frigate config from blvckserver
5. Start Frigate, verify camera feeds + object detection
6. Stop old Frigate container on blvckserver
7. Remove sata0 (nvr-data) passthrough from VM 100 config (optional — can keep until final decommission)

**Validation:** This phase proves iGPU sharing via LXC works. If it fails, we reassess the approach for ct-media and ct-photos before proceeding.

### Phase 3: Storage cutover + remaining services
**Scope:** mergerfs setup, ct-media, ct-photos, ct-files
**Downtime:** 15-30 minutes for the storage cutover step.
**Prerequisite:** Phase 2 complete.

**Step 3a — Reclaim HDDs:**
1. Stop blvckserver (VM 100)
2. Remove sata1 (4TB) and sata2 (1TB) passthrough from VM config
3. On proxmoxmain host:
   - Install mergerfs (`dpkg -i mergerfs_*.deb`)
   - Create mount points: `/mnt/cloud-1`, `/mnt/cloud-2`, `/mnt/cloud`
   - Mount HDDs by UUID
   - Set up mergerfs pool
   - Add to fstab
4. Verify data is intact (`ls /mnt/cloud/volumes/`)
5. Optionally restart blvckserver with `/mnt/cloud` bind-mounted via CT-style mount for remaining services during migration

**Step 3b — Create CTs:**
1. Create ct-media on proxmoxmain
   - Docker + compose with jellyfin, sonarr, radarr, deluge, prowlarr, flaresolverr
   - Bind-mount media storage from `/mnt/cloud/volumes/mediaserver/`
   - iGPU passthrough for Jellyfin transcoding
   - Copy configs from old containers
   - Verify: media library visible, streaming works, downloads work
2. Create ct-photos on proxmoxmain
   - Docker + compose with immich server, ML, postgres, redis
   - Bind-mount immich data from `/mnt/cloud/volumes/mediaserver/immich/`
   - iGPU passthrough for ML
   - Copy configs, verify photo library loads and ML works
3. Create ct-files on proxmoxmain
   - Docker + compose with samba + file browser
   - Bind-mount full `/mnt/cloud`
   - Copy samba config
   - Verify SMB shares accessible, file browser works on phone/web

**Step 3c — Decommission blvckserver:**
1. Verify all services operational on new CTs
2. Stop and destroy VM 100
3. Reclaim 233GB NVMe boot disk on local-lvm
4. Clean up stale Docker volumes data from `/mnt/cloud/volumes/` (gitlab, firefly, minecraft, rocket_chat, photoprism, plex, satisfactory)

## Services Dropped

| Service | Reason | Replacement |
|---------|--------|-------------|
| nextcloud | Overkill for file viewing | File browser (in ct-files) |
| nginx-proxy-manager | No longer exposing services publicly | Cloudflare Tunnel |
| cloudflare-ddns | Was for public IP tracking | Cloudflare Tunnel |
| wireguard | Replaced by Tailscale | Tailscale (already in use) |
| prometheus + grafana + node_exporter | Stale, needs rebuild | Future: fresh monitoring stack |
| portfolio (strapi + frontend + postgres) | Stale, needs rebuild | Future: rebuild separately |
| owntracks | No longer needed | Dropped |
| portainer | No longer needed after migration | Proxmox manages CT lifecycle, Docker manages services |
| jackett | Redundant with prowlarr | prowlarr covers indexer management |

## Future Work (out of scope)

- **RAID 1 upgrade:** 4x 4TB disks → 2 RAID 1 pairs → mergerfs pool (8TB usable). mergerfs config on host stays the same, just more/bigger mount points.
- **VLAN segmentation:** Separate networks by role (services, management, IoT/NVR).
- **Monitoring rebuild:** Fresh prometheus + grafana stack, node_exporter per CT/host.
- **Off-site backup:** Free cloud tier for configs and critical data.
- **Portfolio rebuild:** Lightweight static site or new stack, deployed via ct-tunnel.
- **Minecraft server:** Game server CT, exposed via Cloudflare Tunnel.
