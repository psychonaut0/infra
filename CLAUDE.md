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
- **VMs:** blvckserver (VMID 100)
- **Storage:** See `docs/hardware.md` for full disk and storage layout.

### proxmoxnode
- **IP:** 192.168.3.3
- **User:** root
- **OS:** Proxmox VE
- **SSH:** Port 22, key-based auth (no password)
- **CPU:** Intel N100 (4C/4T)
- **RAM:** 16GB
- **Role:** Secondary Proxmox cluster node. Shares authorized_keys with proxmoxmain via pmxcfs.
- **VMs:** Home Assistant OS (VMID 101, 8GB RAM, 50GB disk)
- **Storage:** 476GB SSD, ~300GB LVM thin available.

### blvckserver (Arch Linux VM — VMID 100)
- **IP:** 192.168.3.4
- **User:** psy
- **OS:** Arch Linux (VM under proxmoxmain)
- **SSH:** Port 123, key-based auth + 2FA (authenticator)
- **Resources:** 12 vCPU, 24GB RAM, iGPU passthrough (Intel UHD 630)
- **Note:** Migrated from bare-metal Arch to Proxmox VM. Monolithic server — all services run as Docker containers in this single VM. Being decomposed into proper Proxmox VMs/CTs. See `docs/services.md` for inventory and `docs/migration.md` for plan.
- **SSH multiplexing:** ControlMaster configured on both Termux and blvckmain to persist connections for 12h and avoid repeated 2FA prompts.

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
  └── ssh blvckserver    → 192.168.3.4:123   (psy, key+2FA, multiplexed)

blvckmain (main PC)
  └── ssh blvckserver    → 192.168.3.4:123   (psy, key+2FA, multiplexed)
```

## SSH Multiplexing

blvckserver requires 2FA on every connection. To avoid repeated prompts, SSH multiplexing is configured on both Termux and blvckmain:
- `ControlMaster auto`
- `ControlPath ~/.ssh/sockets/%r@%h-%p`
- `ControlPersist 12h`

First connection requires 2FA. Subsequent connections within 12h reuse the socket.

## Services

All services currently run on blvckserver as Docker containers managed via Portainer.
Full inventory in `docs/services.md`. Hardware and storage details in `docs/hardware.md`.
Migration plan in `docs/migration.md`.
