# Hardware & Storage

## proxmoxmain

### Compute
- **CPU:** Intel i5-10400 @ 2.90GHz — 6 cores, 12 threads
- **RAM:** 32GB

### Physical Disks

| Device | Type | Size | Usage |
|--------|------|------|-------|
| nvme0n1 | NVMe SSD | ~466GB | Proxmox OS + LVM thin (`local-lvm`) |
| sdc | HDD | ~456GB | LVM thin (`nvr-data`) — Frigate NVR recordings |
| sda | HDD (WD Red 4TB) | 3.6TB | Passed through raw to blvckserver (`/mnt/cloud-2`) |
| sdb | HDD (WD Red 1TB) | 916GB | Passed through raw to blvckserver (`/mnt/cloud-1`) |

### Proxmox Storage Pools

| Pool | Type | Total | Used | Available | Notes |
|------|------|-------|------|-----------|-------|
| local | dir | 94GB | 7.3GB | 82GB | ISOs, backups, templates |
| local-lvm | lvmthin | 338GB | 221GB | 117GB | VM boot disks (on NVMe) |
| nvr-data | lvmthin | 456GB | 400GB | 56GB | Frigate NVR storage (on HDD) |

### VM Disk Allocations (VMID 100 — blvckserver)

| VM Disk | Pool / Passthrough | Size | Guest Mount |
|---------|-------------------|------|-------------|
| scsi0 | local-lvm:vm-100-disk-1 | 233GB | `/` (boot) |
| efidisk0 | local-lvm:vm-100-disk-0 | 4MB | EFI |
| sata0 | nvr-data:vm-100-disk-0 | 400GB | `/mnt/cloud-nvr` |
| sata1 | raw passthrough (WD 4TB) | 3.6TB | `/mnt/cloud-2` |
| sata2 | raw passthrough (WD 1TB) | 916GB | `/mnt/cloud-1` |

### VM Config (VMID 100)
- 12 vCPU (2 sockets x 6 cores), cpu type: x86-64-v3
- 24GB RAM
- iGPU passthrough: `hostpci0: 0000:00:02,x-vga=1` (Intel UHD 630)
- OVMF (UEFI) boot
- Network: virtio on vmbr0, firewall enabled

## proxmoxnode

### Compute
- **CPU:** Intel N100 — 4 cores, 4 threads
- **RAM:** 16GB

### Physical Disks

| Device | Type | Size | Usage |
|--------|------|------|-------|
| sda | SSD | 476GB | Proxmox OS + LVM thin |

### Proxmox Storage Pools

| Pool | Type | Total | Available | Notes |
|------|------|-------|-----------|-------|
| local | dir | 94GB | 59GB | ISOs, backups, templates |
| local-lvm | lvmthin | 349GB | ~336GB | VM disks |

### VM Disk Allocations (VMID 101 — Home Assistant)
- 50GB disk + 4MB EFI on local-lvm
- 8GB RAM allocated

## blvckserver Guest View

### Filesystem Layout

| Mount | Device | FS | Size | Used | Avail | Use% |
|-------|--------|----|------|------|-------|------|
| `/` | /dev/sdd3 (scsi0) | ext4 | 197GB | 84GB | 103GB | 45% |
| `/mnt/cloud-nvr` | /dev/sda1 (sata0) | ext4 | 393GB | 69GB | 305GB | 19% |
| `/mnt/cloud-1` | /dev/sdc1 (sata2) | ext4 | 916GB | 759GB | 112GB | 88% |
| `/mnt/cloud-2` | /dev/sdb1 (sata1) | ext4 | 3.6TB | 2.4TB | 1.1TB | 70% |
| `/mnt/cloud` | mergerfs | fuse | 4.9TB | 3.2TB | 1.5TB | 69% |

### mergerfs Config
Pools `/mnt/cloud-1`, `/mnt/cloud-2`, and `/mnt/cloud-nvr` into `/mnt/cloud`.
```
/mnt/cloud-*  /mnt/cloud  fuse.mergerfs  allow_other,fsname=cloud,threads=12,category.create=mfs,use_ino,dropcacheonclose=true,cache.files=partial  0 0
```

### Key Observations
- The two raw HDDs (sata1, sata2) are passed through directly to the VM — Proxmox has no visibility or management over them.
- The mergerfs pool combines all three data mounts into a single namespace, but this is a guest-level construct.
- cloud-1 is at 88% — approaching capacity.
- iGPU is passed through for hardware transcoding (Jellyfin, Frigate, Immich ML).
