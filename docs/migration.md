# Migration Plan: blvckserver Decomposition

## Goal

Decompose the monolithic blvckserver VM into proper Proxmox-managed VMs and LXC containers, with storage visible and managed at the hypervisor level.

## Current State

Everything runs in a single Arch Linux VM (VMID 100) on proxmoxmain:
- 29 running containers across 15 Docker Compose stacks
- 3 HDDs passed through raw — invisible to Proxmox
- iGPU passed through for transcoding (Jellyfin, Frigate, Immich)
- mergerfs pooling all data disks into `/mnt/cloud`
- All managed via Portainer (no compose files on filesystem)

### Problems
- No per-service resource isolation
- No Proxmox-level snapshots or backups of individual services
- Raw disk passthrough prevents Proxmox storage sharing across guests
- Single point of failure — one bad container or update can cascade
- Can't scale services independently across cluster nodes

## Storage: What Needs to Happen First

Before splitting services, the HDDs must be reclaimed by Proxmox.

### Current Disk Mapping

```
proxmoxmain physical disks:
  nvme0n1 (466GB SSD)  → local-lvm (Proxmox managed)     ✓
  sdc     (456GB HDD)  → nvr-data LVM thin (Proxmox managed) ✓
  sda     (4TB HDD)    → raw passthrough to VM             ✗
  sdb     (1TB HDD)    → raw passthrough to VM             ✗
```

### Target State

Both HDDs managed by Proxmox, mountable by any guest:

**Option A: Proxmox directory storage**
- Format and mount HDDs on Proxmox host
- Create Proxmox `dir` storage pools
- Bind-mount into LXC containers as needed
- Pros: Simple, native, no network overhead
- Cons: Only works for CTs on the same node

**Option B: Dedicated NAS CT/VM**
- One LXC container owns the HDDs (via bind mount or passthrough)
- Exports via NFS/SMB to other guests
- Pros: Works across cluster nodes, access control
- Cons: Network overhead, extra complexity

**Option C: Hybrid**
- HDDs mounted on Proxmox host as directory storage
- Bind-mount into local CTs directly
- NFS export for anything on proxmoxnode
- Pros: Best of both — local speed + cross-node access
- Cons: Two access paths to manage

### Data on Disks

| Disk | Mount | Size | Used | Content |
|------|-------|------|------|---------|
| WD 1TB | /mnt/cloud-1 | 916GB | 759GB (88%) | TBD — needs audit |
| WD 4TB | /mnt/cloud-2 | 3.6TB | 2.4TB (70%) | TBD — needs audit |
| 456GB HDD (LVM) | /mnt/cloud-nvr | 393GB | 69GB (19%) | Frigate NVR recordings |

The mergerfs pool combines all three into `/mnt/cloud` (4.9TB). Docker volumes reference paths under these mounts. Need to audit which services use which paths before migration.

## Proposed Service Groupings

### Group 1: Network & Infrastructure (LXC)
**Priority: High — migrate first, low risk**

| Service | Current | Target |
|---------|---------|--------|
| pihole | Docker | LXC container (unprivileged) |
| wireguard | Docker | LXC container (may need privileged) |
| nginx-proxy-manager | Docker + MariaDB | LXC container |
| cloudflare-ddns | Docker (broken) | LXC or fix in place |

- No disk dependencies beyond config
- Lightweight — 512MB–1GB RAM each
- Network-critical — migrate with care, update DNS/firewall in parallel

### Group 2: Monitoring (LXC)
**Priority: High — useful to have independent monitoring during migration**

| Service | Current | Target |
|---------|---------|--------|
| grafana | Docker | LXC container |
| prometheus | Docker (broken) | LXC container — fix during migration |
| node_exporter | Docker | Install natively per host/CT |

- Small footprint
- Should monitor all new CTs/VMs from day one

### Group 3: Media Server (LXC or VM)
**Priority: Medium — large, needs iGPU + bulk storage**

| Service | Current | Target |
|---------|---------|--------|
| jellyfin | Docker | LXC or VM — needs iGPU passthrough |
| sonarr | Docker | Same CT/VM as media stack |
| radarr | Docker | Same CT/VM as media stack |
| deluge | Docker | Same CT/VM as media stack |
| prowlarr | Docker | Same CT/VM as media stack |
| jackett | Docker | Can remove if prowlarr covers all indexers |
| flaresolverr | Docker | Same CT/VM as media stack |

- Heaviest disk user — needs access to cloud-1/cloud-2
- iGPU passthrough for transcoding (works with LXC but needs config)
- Keep as a single unit — these services are tightly coupled

### Group 4: Photos (LXC or VM)
**Priority: Medium — needs iGPU + storage**

| Service | Current | Target |
|---------|---------|--------|
| immich server | Docker | LXC or VM |
| immich ML | Docker | Same — needs iGPU for inference |
| immich postgres | Docker | Same or separate DB CT |
| immich redis | Docker | Same |

- Needs iGPU for ML processing
- Significant storage for photo library
- Self-contained stack

### Group 5: NVR (LXC or VM)
**Priority: Medium — needs iGPU + dedicated NVR disk**

| Service | Current | Target |
|---------|---------|--------|
| frigate | Docker | LXC or VM — needs iGPU + /mnt/cloud-nvr |

- Already has dedicated Proxmox storage pool (nvr-data)
- Needs iGPU for detection/decoding
- Note: iGPU can only be passed to one VM, but can be shared across LXC containers via device bind

### Group 6: Applications (LXC)
**Priority: Low — migrate last**

| Service | Current | Target |
|---------|---------|--------|
| nextcloud | Docker (broken) | LXC container — fix during migration |
| owntracks | Docker | LXC container |
| portfolio (strapi + frontend + db) | Docker | LXC container |
| samba | Docker | LXC container or native on storage host |

- Mostly independent, low resource usage
- Nextcloud and portfolio need their own databases

### Decommission
- **Portainer** — no longer needed once services are in Proxmox CTs/VMs
- **Stale volumes** — gitlab, firefly, rocket_chat, satisfactory, minecraft, photoprism, plex

## iGPU Sharing Consideration

Three services need the Intel UHD 630: Jellyfin, Frigate, Immich ML.
- **VM passthrough** (`hostpci0`): Only one VM can own the GPU
- **LXC device passthrough**: Multiple CTs can share `/dev/dri/*` — preferred approach
- This is a strong argument for LXC over full VMs for GPU-dependent services

## Migration Order

```
1. Reclaim HDDs from VM → Proxmox storage          (prerequisite)
2. Network/Infra CTs (pihole, wireguard, NPM)      (foundation)
3. Monitoring CTs (grafana, prometheus)             (observability)
4. NVR CT (frigate)                                 (already has PVE storage)
5. Media CT (jellyfin + *arr stack)                 (needs bulk storage)
6. Photos CT (immich)                               (needs bulk storage)
7. Apps CTs (nextcloud, owntracks, portfolio, samba)(cleanup)
8. Decommission blvckserver                         (done)
```

## Open Questions

- [ ] Audit what data lives on cloud-1 vs cloud-2 — which services use which paths?
- [ ] Storage strategy decision: Option A, B, or C?
- [ ] Keep mergerfs concept or split storage per service?
- [ ] Network topology: same bridge/VLAN for all CTs, or segment by role?
- [ ] Backup strategy for new CTs (Proxmox Backup Server?)
- [ ] What to do with the 3 crash-looping services before migration?
- [ ] Portfolio site — still needed or can be decommissioned?
