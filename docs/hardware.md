# Hardware & Storage

Post-decomposition state (blvckserver VM retired 2026-04-12). All storage is now managed at the Proxmox host level and consumed by LXC containers via bind mounts.

## proxmoxmain

### Compute
- **CPU:** Intel i5-10400 @ 2.90GHz — 6 cores, 12 threads
- **RAM:** 32GB
- **iGPU:** Intel UHD 630 — shared across CTs via `/dev/dri` bind mount (ct-nvr, ct-media, ct-photos)

### Physical Disks

| Device | Type | Size | Usage |
|--------|------|------|-------|
| nvme0n1 | NVMe SSD (CT500P2SSD8) | 466GB | Proxmox OS + LVM thin (`local-lvm`) — CT boot disks |
| sdc | HDD (WD5000AADS) | 466GB | LVM thin pool `nvr-data` — Frigate recordings |
| sdb | HDD (WD10EFRX, WD Red 1TB) | 916GB | ext4, mounted on host at `/mnt/cloud-1` — mergerfs member |
| sda | HDD (WD40EFZX, WD Red 4TB) | 3.6TB | ext4, mounted on host at `/mnt/cloud-2` — mergerfs member |

### Proxmox Storage Pools

| Pool | Type | Total | Used | Available | Notes |
|------|------|-------|------|-----------|-------|
| local | dir | 94GB | 7.3GB | 82GB | ISOs, backups, templates |
| local-lvm | lvmthin | 338GB | 30GB | 308GB | CT boot disks (on NVMe) |
| nvr-data | lvmthin | 456GB | 400GB | 56GB | Frigate NVR storage (on sdc) — **87% used** |
| cloud | dir | 4.5TB | 2.7TB | 1.6TB | mergerfs of cloud-1 + cloud-2, exposed as a PVE dir pool |

### LXC Boot Disk Allocations (on local-lvm)

| VMID | Name | LV | Size | Data% |
|------|------|----|------|-------|
| 103 | ct-tunnel | vm-103-disk-0 | 2GB | 86% |
| 104 | ct-nvr | vm-104-disk-0 | 24GB | 37% |
| 105 | ct-media | vm-105-disk-0 | 16GB | 37% |
| 106 | ct-photos | vm-106-disk-0 | 16GB | 51% |
| 107 | ct-files | vm-107-disk-0 | 16GB | 13% |
| 108 | ct-mgmt | vm-108-disk-0 | 4GB | 91% |
| 109 | ct-backup | vm-109-disk-0 | 8GB | 12% |
| 110 | ct-tools | vm-110-disk-0 | 8GB | 35% |
| 112 | ct-games | vm-112-disk-0 | 40GB | 11% |
| 113 | ct-portfolio | vm-113-disk-0 | 8GB | 27% |
| 114 | ct-workout | vm-114-disk-0 | 16GB | 19% |
| 115 | ct-chat | vm-115-disk-0 | 32GB | 27% |

## proxmoxnode

### Compute
- **CPU:** Intel N100 — 4 cores, 4 threads
- **RAM:** 16GB

### Physical Disks

| Device | Type | Size | Usage |
|--------|------|------|-------|
| sda | SSD (ASint AS606) | 477GB | Proxmox OS + LVM thin |

### Proxmox Storage Pools

| Pool | Type | Total | Used | Available |
|------|------|-------|------|-----------|
| local | dir | 94GB | 31GB | 60GB |
| local-lvm | lvmthin | 349GB | 16GB | 333GB |

### VM / LXC Disk Allocations

| VMID | Name | LV | Size | Data% | Notes |
|------|------|----|------|-------|-------|
| 101 | homeassistant (VM) | vm-101-disk-0 | 50GB | 27% | HAOS root |
| 101 | homeassistant (VM) | vm-101-disk-1 | 4MB | — | EFI |
| 102 | ct-dns | vm-102-disk-0 | 4GB | 49% | |

## Bulk Storage — mergerfs `/mnt/cloud`

Two HDDs (sda, sdb) are formatted ext4 and pooled with mergerfs on the proxmoxmain host:

```fstab
/mnt/cloud-1:/mnt/cloud-2  /mnt/cloud  fuse.mergerfs  \
  allow_other,fsname=cloud,category.create=mfs,use_ino,dropcacheonclose=true,cache.files=partial  0 0
```

The `/mnt/cloud` pool is exposed to Proxmox as a `dir` storage pool (`cloud`) and bind-mounted into the CTs that need it:

| CT | Bind from host | Mount in CT | Purpose |
|----|---------------|-------------|---------|
| ct-files | `/mnt/cloud` | `/mnt/cloud` | Samba + copyparty (2 instances), full pool |
| ct-media | `/mnt/cloud/volumes/mediaserver` | `/mnt/mediaserver` | Jellyfin libraries + *arr configs |
| ct-photos | `/mnt/cloud/volumes/mediaserver/immich` | `/mnt/immich` | Immich data, ML cache, Postgres |

## NVR Storage — `/mnt/nvr-data`

Dedicated HDD (sdc) is an LVM thin pool `nvr-data`. A single 400GB thin LV named **`vm-100-disk-0`** (legacy name — was the blvckserver disk, now repurposed) holds an ext4 partition mounted on proxmoxmain:

```
/dev/mapper/nvr--data-vm--100--disk--0p1  →  /mnt/nvr-data  (ext4, 393GB usable)
```

Partition activation is handled by `kpartx` via the systemd unit `/etc/systemd/system/mnt-nvr-data.service`, which also bind-mounts the volume into ct-nvr.

**Note:** the LV name `vm-100-disk-0` is a cosmetic holdover from the pre-migration VM; it is not a zombie and should not be deleted. Renaming requires detaching /mnt/nvr-data and recreating the LV — not worth the churn.

### Retention: 7 days → 4 days (2026-08-26)

Continuous 24/7 recording of all cameras writes roughly **49GB/day**, so the
original 7-day window did not fit: `/mnt/nvr-data` reached 93% and the thin LV
`vm-100-disk-0` sat at 99.98% allocated. ct-nvr then thrashed its 6GiB memcg on
page cache and wedged; the CT had to be stopped.

`stacks/ct-nvr/config.yaml` now sets `continuous`, `alerts` and `detections`
retention to **4 days** (~200GB steady state, comfortable inside 393GB).

Two things to keep in mind:

- **The thin LV stays ~100% allocated even after footage is deleted.** Thin
  provisioning does not reclaim blocks without discard, so `lvs` will keep
  reporting `Data% 99.98` while `df` shows free space. Judge headroom by `df -h
  /mnt/nvr-data`, not by `lvs`.
- **ct-nvr is currently stopped on purpose** (2026-08-31) — recording is not
  needed right now. `onboot: 1` is still set, so it will come back on the next
  proxmoxmain reboot unless that is changed.

## Notes & Capacity Concerns

- `nvr-data` thin pool at 87.6%; the `vm-100-disk-0` LV reads 99.98% allocated and will stay there (no discard). Filesystem is the real signal: 287GB/393GB used (77%) after the retention cut. See *Retention* above.
- `ct-mgmt` boot disk at 91%, `ct-tunnel` at 86% — both 4GB and 2GB respectively. Candidates for disk resize via `pct resize` if they grow further.
- No off-box backup target configured (`pvesm status` has no PBS or remote pool). 2.7TB of mergerfs data is single-point-of-failure on two non-RAID HDDs.
- proxmoxnode is heavily underutilized (4% local-lvm) — capacity headroom for future workloads, but no shared storage to live-migrate from proxmoxmain.
