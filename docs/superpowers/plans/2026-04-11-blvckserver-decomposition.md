# blvckserver Decomposition — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate all services from the monolithic blvckserver VM into 6 LXC containers running Docker, across 3 phases, with zero data loss.

**Architecture:** Gradual peel — Phase 1 migrates diskless services (pihole, cloudflared), Phase 2 migrates NVR (Frigate, uses its own Proxmox-managed disk), Phase 3 reclaims HDDs from the VM, sets up host-level mergerfs, and migrates the remaining storage-dependent services (media, photos, files). Each CT runs Debian 13 with Docker CE and a single `docker-compose.yml`.

**Tech Stack:** Proxmox VE (LXC), Debian 13 (Trixie), Docker CE, Docker Compose v2, mergerfs

**Key details:**
- proxmoxmain: `root@192.168.3.2` (SSH port 22)
- proxmoxnode: `root@192.168.3.3` (SSH port 22)
- blvckserver: `psy@192.168.3.4` (SSH port 123, multiplexed via `ssh blvckserver`)
- Debian 13 template: `debian-13-standard_13.1-2_amd64.tar.zst` (on proxmoxmain `local`, needs download on proxmoxnode)
- Next VMID: 102
- Subnet: 192.168.3.0/24, gateway 192.168.3.1
- Known IPs in use: .1 (gateway), .2 (proxmoxmain), .3 (proxmoxnode), .4 (blvckserver), .10 (HA)

**VMID and IP allocation:**

| CT | VMID | IP | Node |
|----|------|----|------|
| ct-dns | 102 | 192.168.3.5 | proxmoxnode |
| ct-tunnel | 103 | 192.168.3.6 | proxmoxmain |
| ct-nvr | 104 | 192.168.3.7 | proxmoxmain |
| ct-media | 105 | 192.168.3.8 | proxmoxmain |
| ct-photos | 106 | 192.168.3.9 | proxmoxmain |
| ct-files | 107 | 192.168.3.11 | proxmoxmain |

**File structure (infra repo):**
```
infra/
  stacks/
    ct-dns/docker-compose.yml
    ct-tunnel/docker-compose.yml
    ct-nvr/docker-compose.yml
    ct-media/docker-compose.yml
    ct-photos/docker-compose.yml
    ct-files/docker-compose.yml
```

---

## Phase 1: No-Disk Services

### Task 1: Create ct-dns on proxmoxnode

**Target:** LXC container running pihole via Docker on proxmoxnode.

- [ ] **Step 1: Download Debian 13 template on proxmoxnode**

```bash
ssh root@192.168.3.3 'pveam download local debian-13-standard_13.1-2_amd64.tar.zst'
```

Expected: template downloads successfully.

- [ ] **Step 2: Create the LXC container**

```bash
ssh root@192.168.3.3 'pct create 102 local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
  --hostname ct-dns \
  --cores 1 \
  --memory 512 \
  --swap 256 \
  --storage local-lvm \
  --rootfs local-lvm:4 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.3.5/24,gw=192.168.3.1,firewall=1 \
  --nameserver 1.1.1.1 \
  --features nesting=1,keyctl=1 \
  --onboot 1 \
  --start 0 \
  --unprivileged 1'
```

Expected: `CT 102 created`.

- [ ] **Step 3: Start the container and install Docker**

```bash
ssh root@192.168.3.3 'pct start 102'
ssh root@192.168.3.3 'pct exec 102 -- bash -c "
  apt-get update &&
  apt-get install -y ca-certificates curl gnupg &&
  install -m 0755 -d /etc/apt/keyrings &&
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc &&
  chmod a+r /etc/apt/keyrings/docker.asc &&
  echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \$(. /etc/os-release && echo \$VERSION_CODENAME) stable\" > /etc/apt/sources.list.d/docker.list &&
  apt-get update &&
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
"'
```

Expected: Docker installs successfully.

- [ ] **Step 4: Verify Docker works inside the CT**

```bash
ssh root@192.168.3.3 'pct exec 102 -- docker run --rm hello-world'
```

Expected: `Hello from Docker!` output.

- [ ] **Step 5: Write the pihole compose file locally**

Create `stacks/ct-dns/docker-compose.yml`:

```yaml
services:
  pihole:
    image: pihole/pihole:latest
    container_name: pihole
    hostname: pihole
    restart: unless-stopped
    ports:
      - "53:53/tcp"
      - "53:53/udp"
      - "67:67/udp"
      - "80:80/tcp"
    environment:
      TZ: Europe/Rome
      PIHOLE_UID: "1000"
      PIHOLE_GID: "1000"
      WEB_UID: "1000"
      WEB_GID: "1000"
    volumes:
      - pihole-data:/etc/pihole
      - pihole-dns:/etc/dnsmasq.d
    dns:
      - 127.0.0.1
      - 1.1.1.1

volumes:
  pihole-data:
  pihole-dns:
```

- [ ] **Step 6: Deploy compose file to ct-dns**

```bash
ssh root@192.168.3.3 'pct exec 102 -- mkdir -p /opt/stacks/ct-dns'
scp stacks/ct-dns/docker-compose.yml root@192.168.3.3:/tmp/docker-compose-dns.yml
ssh root@192.168.3.3 'pct push 102 /tmp/docker-compose-dns.yml /opt/stacks/ct-dns/docker-compose.yml'
```

- [ ] **Step 7: Copy pihole config from blvckserver**

```bash
ssh blvckserver 'docker cp pihole:/etc/pihole/. /tmp/pihole-config/'
scp -P 123 -r psy@192.168.3.4:/tmp/pihole-config /tmp/pihole-config
scp -r /tmp/pihole-config root@192.168.3.3:/tmp/pihole-config
ssh root@192.168.3.3 'pct push 102 /tmp/pihole-config /tmp/pihole-config --recursive'
```

Note: The pihole config (custom DNS entries, blocklists, etc.) will be imported after first start.

- [ ] **Step 8: Start pihole**

```bash
ssh root@192.168.3.3 'pct exec 102 -- bash -c "cd /opt/stacks/ct-dns && docker compose up -d"'
```

Expected: pihole container starts.

- [ ] **Step 9: Verify pihole is working**

```bash
# Test DNS resolution from blvckmain
dig @192.168.3.5 google.com +short
# Test admin UI is reachable
curl -s -o /dev/null -w "%{http_code}" http://192.168.3.5/admin/
```

Expected: DNS resolves, admin UI returns 200.

- [ ] **Step 10: Import pihole config**

```bash
ssh root@192.168.3.3 'pct exec 102 -- bash -c "
  docker cp /tmp/pihole-config/. pihole:/etc/pihole/
  docker restart pihole
"'
```

Verify custom DNS entries and blocklists are present in the admin UI at `http://192.168.3.5/admin/`.

- [ ] **Step 11: Switch network DNS to new pihole**

Update the UniFi gateway (192.168.1.1) DHCP settings to advertise 192.168.3.5 as the primary DNS server. This is a manual step via the UniFi web UI:
1. Go to Settings → Networks → Default → DHCP → DNS Server
2. Set to `192.168.3.5`
3. Save

Verify clients pick up the new DNS after DHCP renewal:
```bash
# On blvckmain
resolvectl status | grep "DNS Servers"
```

- [ ] **Step 12: Stop old pihole on blvckserver**

```bash
ssh blvckserver 'docker stop pihole'
```

Verify DNS still works:
```bash
dig @192.168.3.5 google.com +short
```

- [ ] **Step 13: Set up SSH access to ct-dns**

```bash
# Copy blvckmain's public key into the CT
ssh root@192.168.3.3 'pct exec 102 -- bash -c "
  mkdir -p /root/.ssh &&
  chmod 700 /root/.ssh
"'
ssh root@192.168.3.3 "pct exec 102 -- bash -c \"echo '$(cat ~/.ssh/id_ed25519.pub)' >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys\""

# Test direct SSH
ssh -o StrictHostKeyChecking=accept-new root@192.168.3.5 'hostname'
```

Expected: `ct-dns`.

- [ ] **Step 14: Commit**

```bash
git add stacks/ct-dns/docker-compose.yml
git commit -m "Add ct-dns pihole stack — Phase 1"
```

---

### Task 2: Create ct-tunnel on proxmoxmain

**Target:** LXC container running cloudflared via Docker on proxmoxmain.

- [ ] **Step 1: Create the LXC container**

```bash
ssh root@192.168.3.2 'pct create 103 local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
  --hostname ct-tunnel \
  --cores 1 \
  --memory 256 \
  --swap 128 \
  --storage local-lvm \
  --rootfs local-lvm:2 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.3.6/24,gw=192.168.3.1,firewall=1 \
  --nameserver 192.168.3.5 \
  --features nesting=1,keyctl=1 \
  --onboot 1 \
  --start 0 \
  --unprivileged 1'
```

- [ ] **Step 2: Start and install Docker**

```bash
ssh root@192.168.3.2 'pct start 103'
ssh root@192.168.3.2 'pct exec 103 -- bash -c "
  apt-get update &&
  apt-get install -y ca-certificates curl gnupg &&
  install -m 0755 -d /etc/apt/keyrings &&
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc &&
  chmod a+r /etc/apt/keyrings/docker.asc &&
  echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \$(. /etc/os-release && echo \$VERSION_CODENAME) stable\" > /etc/apt/sources.list.d/docker.list &&
  apt-get update &&
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
"'
```

- [ ] **Step 3: Verify Docker works**

```bash
ssh root@192.168.3.2 'pct exec 103 -- docker run --rm hello-world'
```

- [ ] **Step 4: Create a Cloudflare Tunnel**

This step requires a Cloudflare account and a domain. Run interactively from blvckmain:

```bash
# Install cloudflared locally (if not already)
# Then create tunnel and get the token:
# cloudflared tunnel login
# cloudflared tunnel create infra-tunnel
# Copy the tunnel token — it will be used in the compose file
```

Alternatively, create the tunnel via the Cloudflare Zero Trust dashboard:
1. Go to https://one.dash.cloudflare.com → Networks → Tunnels
2. Create a tunnel named `infra-tunnel`
3. Copy the tunnel token

- [ ] **Step 5: Write the cloudflared compose file locally**

Create `stacks/ct-tunnel/docker-compose.yml`:

```yaml
services:
  cloudflared:
    image: cloudflare/cloudflared:latest
    container_name: cloudflared
    restart: unless-stopped
    command: tunnel run
    environment:
      TUNNEL_TOKEN: "${TUNNEL_TOKEN}"
    network_mode: host
```

Create `stacks/ct-tunnel/.env.example`:

```
TUNNEL_TOKEN=your-tunnel-token-here
```

Note: The actual `.env` file with the real token lives only on the CT, not in git.

- [ ] **Step 6: Deploy to ct-tunnel**

```bash
ssh root@192.168.3.2 'pct exec 103 -- mkdir -p /opt/stacks/ct-tunnel'
scp stacks/ct-tunnel/docker-compose.yml root@192.168.3.2:/tmp/docker-compose-tunnel.yml
ssh root@192.168.3.2 'pct push 103 /tmp/docker-compose-tunnel.yml /opt/stacks/ct-tunnel/docker-compose.yml'
```

Then create the `.env` file with the real token on the CT:
```bash
ssh root@192.168.3.2 "pct exec 103 -- bash -c 'echo \"TUNNEL_TOKEN=<your-real-token>\" > /opt/stacks/ct-tunnel/.env'"
```

- [ ] **Step 7: Start cloudflared**

```bash
ssh root@192.168.3.2 'pct exec 103 -- bash -c "cd /opt/stacks/ct-tunnel && docker compose up -d"'
```

- [ ] **Step 8: Verify tunnel is connected**

```bash
ssh root@192.168.3.2 'pct exec 103 -- docker logs cloudflared 2>&1 | tail -5'
```

Expected: logs show `Connection ... registered` and `Registered tunnel connection`.

Also verify in Cloudflare Zero Trust dashboard: tunnel should show as "Healthy".

- [ ] **Step 9: Set up SSH access to ct-tunnel**

```bash
ssh root@192.168.3.2 'pct exec 103 -- bash -c "
  mkdir -p /root/.ssh &&
  chmod 700 /root/.ssh
"'
ssh root@192.168.3.2 "pct exec 103 -- bash -c \"echo '$(cat ~/.ssh/id_ed25519.pub)' >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys\""
ssh -o StrictHostKeyChecking=accept-new root@192.168.3.6 'hostname'
```

- [ ] **Step 10: Stop old services on blvckserver**

```bash
ssh blvckserver 'docker stop cloudflare-ddns_cloudflare-ddns_1 nginx-proxy-manager_app_1 nginx-proxy-manager_db_1 wireguard'
```

- [ ] **Step 11: Commit**

```bash
git add stacks/ct-tunnel/docker-compose.yml stacks/ct-tunnel/.env.example
git commit -m "Add ct-tunnel cloudflared stack — Phase 1"
```

---

### Task 3: Phase 1 validation and cleanup

- [ ] **Step 1: Verify both CTs are running and set to autostart**

```bash
ssh root@192.168.3.3 'pct status 102'
ssh root@192.168.3.2 'pct status 103'
ssh root@192.168.3.3 'pct config 102 | grep onboot'
ssh root@192.168.3.2 'pct config 103 | grep onboot'
```

Expected: both `status: running`, both `onboot: 1`.

- [ ] **Step 2: Verify DNS resolution via new pihole**

```bash
dig @192.168.3.5 google.com +short
dig @192.168.3.5 github.com +short
```

- [ ] **Step 3: Verify Cloudflare Tunnel is healthy**

```bash
ssh root@192.168.3.6 'docker logs cloudflared 2>&1 | grep -i "registered\|connected" | tail -3'
```

- [ ] **Step 4: Confirm old services are stopped on blvckserver**

```bash
ssh blvckserver 'docker ps --format "{{.Names}}" | grep -E "pihole|wireguard|nginx-proxy|cloudflare-ddns"'
```

Expected: no output (all stopped).

- [ ] **Step 5: Commit Phase 1 completion note**

Update `docs/migration.md` to record Phase 1 as complete with the date and any notes.

```bash
git add docs/migration.md
git commit -m "Mark Phase 1 complete — ct-dns and ct-tunnel operational"
```

---

## Phase 2: NVR (Independent Disk)

### Task 4: Create ct-nvr on proxmoxmain

**Target:** LXC container running Frigate with iGPU passthrough and nvr-data storage.

**Important:** This CT must be **privileged** for `/dev/dri` device access. Alternatively, an unprivileged CT can work with manual cgroup device rules — but privileged is simpler and Frigate is a trusted workload.

- [ ] **Step 1: Identify the nvr-data volume for the CT**

```bash
ssh root@192.168.3.2 'pvesm list nvr-data'
```

Currently `nvr-data:vm-100-disk-0` is assigned to VM 100. We need to allocate a new volume or, after stopping Frigate on blvckserver, reassign this one.

Strategy: create the CT with its own boot disk, and bind-mount the nvr-data disk's content from the host.

First, check if the nvr-data disk can be mounted on the host:
```bash
ssh root@192.168.3.2 'lvs | grep nvr'
```

Since nvr-data is LVM thin and currently attached to VM 100 as sata0, we'll mount the NVR data differently:
- Stop Frigate on blvckserver first (the VM still runs, just Frigate stops)
- Create a host mount point for the LVM volume
- Bind-mount into the CT

- [ ] **Step 2: Stop Frigate on blvckserver**

```bash
ssh blvckserver 'docker stop frigate'
```

- [ ] **Step 3: Create the LXC container (privileged)**

```bash
ssh root@192.168.3.2 'pct create 104 local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
  --hostname ct-nvr \
  --cores 2 \
  --memory 4096 \
  --swap 1024 \
  --storage local-lvm \
  --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.3.7/24,gw=192.168.3.1,firewall=1 \
  --nameserver 192.168.3.5 \
  --features nesting=1,keyctl=1 \
  --onboot 1 \
  --start 0'
```

Note: no `--unprivileged 1` — this creates a privileged CT.

- [ ] **Step 4: Configure iGPU passthrough and NVR storage**

The nvr-data LVM volume is currently passed through to VM 100 as sata0. To make it available to the CT, we need to mount the filesystem on the Proxmox host first.

```bash
ssh root@192.168.3.2 'bash -c "
  # Create mount point
  mkdir -p /mnt/nvr-data

  # Find the LVM device for the nvr volume that was used by VM 100
  # The disk inside the VM had partition sda1 with UUID 7f747e2e-...
  # We need to mount the raw LVM volume and access the partition inside
  # Since this is a raw disk image passed as sata0, it contains a partition table
  
  # Map the partitions inside the LVM volume
  LVPATH=\$(lvs --noheadings -o lv_path nvr-data/vm-100-disk-0 | tr -d \" \")
  kpartx -av \$LVPATH
"'
```

Then mount the partition:
```bash
ssh root@192.168.3.2 'bash -c "
  # The partition mapping will be something like /dev/mapper/nvr--data-vm--100--disk--0p1
  # Find it:
  ls /dev/mapper/ | grep nvr.*disk.*0p
  
  # Mount it
  mount /dev/mapper/nvr--data-vm--100--disk--0p1 /mnt/nvr-data
  
  # Verify data is there
  ls /mnt/nvr-data/
"'
```

Expected: should see `config/`, `data/`, `lost+found/`.

Now add the bind mount and device passthrough to the CT config:
```bash
ssh root@192.168.3.2 'bash -c "
  # Add NVR storage bind mount
  echo \"mp0: /mnt/nvr-data,mp=/mnt/nvr-data\" >> /etc/pve/lxc/104.conf
  
  # Add iGPU device passthrough
  echo \"lxc.cgroup2.devices.allow: c 226:128 rwm\" >> /etc/pve/lxc/104.conf
  echo \"lxc.mount.entry: /dev/dri/renderD128 dev/dri/renderD128 none bind,optional,create=file\" >> /etc/pve/lxc/104.conf
"'
```

- [ ] **Step 5: Start CT and install Docker**

```bash
ssh root@192.168.3.2 'pct start 104'
ssh root@192.168.3.2 'pct exec 104 -- bash -c "
  apt-get update &&
  apt-get install -y ca-certificates curl gnupg &&
  install -m 0755 -d /etc/apt/keyrings &&
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc &&
  chmod a+r /etc/apt/keyrings/docker.asc &&
  echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \$(. /etc/os-release && echo \$VERSION_CODENAME) stable\" > /etc/apt/sources.list.d/docker.list &&
  apt-get update &&
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
"'
```

- [ ] **Step 6: Verify iGPU is accessible inside CT**

```bash
ssh root@192.168.3.2 'pct exec 104 -- ls -la /dev/dri/'
```

Expected: `renderD128` visible.

```bash
ssh root@192.168.3.2 'pct exec 104 -- docker run --rm --device /dev/dri/renderD128 busybox ls -la /dev/dri/'
```

Expected: device accessible inside Docker container. **This validates the iGPU-in-LXC approach.**

- [ ] **Step 7: Verify NVR data is mounted**

```bash
ssh root@192.168.3.2 'pct exec 104 -- ls /mnt/nvr-data/'
```

Expected: `config/`, `data/`, `lost+found/`.

- [ ] **Step 8: Write the Frigate compose file locally**

Create `stacks/ct-nvr/docker-compose.yml`:

```yaml
services:
  frigate:
    image: ghcr.io/blakeblackshear/frigate:stable
    container_name: frigate
    restart: unless-stopped
    privileged: true
    shm_size: 256mb
    ports:
      - "8971:8971"
      - "8554:8554"
      - "8555:8555/tcp"
      - "8555:8555/udp"
    environment:
      FRIGATE_RTSP_PASSWORD: "password"
      TZ: Europe/Rome
    volumes:
      - /mnt/nvr-data/config:/config
      - /mnt/nvr-data/data:/media/frigate
      - type: tmpfs
        target: /tmp/cache
        tmpfs:
          size: 1000000000
    devices:
      - /dev/dri/renderD128:/dev/dri/renderD128
```

- [ ] **Step 9: Deploy and start Frigate**

```bash
ssh root@192.168.3.2 'pct exec 104 -- mkdir -p /opt/stacks/ct-nvr'
scp stacks/ct-nvr/docker-compose.yml root@192.168.3.2:/tmp/docker-compose-nvr.yml
ssh root@192.168.3.2 'pct push 104 /tmp/docker-compose-nvr.yml /opt/stacks/ct-nvr/docker-compose.yml'
ssh root@192.168.3.2 'pct exec 104 -- bash -c "cd /opt/stacks/ct-nvr && docker compose up -d"'
```

- [ ] **Step 10: Verify Frigate is running**

```bash
# Check container health
ssh root@192.168.3.2 'pct exec 104 -- docker ps'

# Check Frigate UI
curl -s -o /dev/null -w "%{http_code}" http://192.168.3.7:8971

# Check logs for camera connections and detector
ssh root@192.168.3.2 'pct exec 104 -- docker logs frigate 2>&1 | tail -20'
```

Expected: container healthy, UI returns 200, logs show cameras connecting and detector initializing.

Verify MQTT connection to Home Assistant (192.168.3.10:1883) is established:
```bash
ssh root@192.168.3.2 'pct exec 104 -- docker logs frigate 2>&1 | grep -i mqtt'
```

- [ ] **Step 11: Set up SSH access to ct-nvr**

```bash
ssh root@192.168.3.2 'pct exec 104 -- bash -c "
  mkdir -p /root/.ssh &&
  chmod 700 /root/.ssh
"'
ssh root@192.168.3.2 "pct exec 104 -- bash -c \"echo '$(cat ~/.ssh/id_ed25519.pub)' >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys\""
ssh -o StrictHostKeyChecking=accept-new root@192.168.3.7 'hostname'
```

- [ ] **Step 12: Make NVR host mount persistent**

Add the kpartx mapping and mount to a systemd unit or script so it survives reboots:

```bash
ssh root@192.168.3.2 'bash -c "
  cat > /etc/systemd/system/mnt-nvr-data.service << EOF
[Unit]
Description=Mount NVR data LVM volume
Before=pve-guests.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c \"kpartx -av /dev/nvr-data/vm-100-disk-0 && sleep 1 && mount /dev/mapper/nvr--data-vm--100--disk--0p1 /mnt/nvr-data\"
ExecStop=/bin/bash -c \"umount /mnt/nvr-data && kpartx -dv /dev/nvr-data/vm-100-disk-0\"

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable mnt-nvr-data.service
"'
```

- [ ] **Step 13: Commit**

```bash
git add stacks/ct-nvr/docker-compose.yml
git commit -m "Add ct-nvr Frigate stack with iGPU passthrough — Phase 2"
```

---

### Task 5: Phase 2 validation

- [ ] **Step 1: Full Frigate verification**

Check all three cameras are streaming:
```bash
curl -s http://192.168.3.7:8971/api/stats | python3 -m json.tool | head -30
```

Check object detection is using the iGPU (inference speed should be <100ms):
```bash
ssh root@192.168.3.7 'docker logs frigate 2>&1 | grep -i "detector\|inference"'
```

- [ ] **Step 2: Verify old Frigate is stopped**

```bash
ssh blvckserver 'docker ps --format "{{.Names}}" | grep frigate'
```

Expected: no output.

- [ ] **Step 3: Remove sata0 passthrough from VM 100 (optional)**

This can wait until Phase 3 decommission. If you want to do it now:

```bash
ssh root@192.168.3.2 'qm set 100 --delete sata0'
```

Note: only do this after confirming Frigate works perfectly on ct-nvr.

- [ ] **Step 4: Update migration docs**

Update `docs/migration.md` to record Phase 2 as complete.

```bash
git add docs/migration.md
git commit -m "Mark Phase 2 complete — ct-nvr operational with iGPU passthrough"
```

---

## Phase 3: Storage Cutover + Remaining Services

### Task 6: Reclaim HDDs and set up host-level mergerfs

**Target:** Mount the 4TB and 1TB HDDs on proxmoxmain host, set up mergerfs pool.

**Downtime:** blvckserver must be stopped. All services still running on it (media, photos, samba, immich, plus any not-yet-stopped containers) will be offline until migration completes.

- [ ] **Step 1: Stop blvckserver**

```bash
ssh root@192.168.3.2 'qm stop 100'
```

Wait for clean shutdown:
```bash
ssh root@192.168.3.2 'qm wait 100 --timeout 120'
```

- [ ] **Step 2: Remove HDD passthrough from VM config**

```bash
ssh root@192.168.3.2 'qm set 100 --delete sata1'
ssh root@192.168.3.2 'qm set 100 --delete sata2'
```

Verify:
```bash
ssh root@192.168.3.2 'qm config 100 | grep sata'
```

Expected: only sata0 (nvr-data) if not already removed, or no sata lines.

- [ ] **Step 3: Identify the HDDs on the Proxmox host**

```bash
ssh root@192.168.3.2 'lsblk -f | grep -A1 "^sd[ab]"'
```

Look for:
- `sda` → partition `sda1` with UUID `f635f68d-36ec-450c-bfb3-ac76cd9bdf96` (4TB, was cloud-2)
- `sdb` → partition `sdb1` with UUID `64d9ab64-2f08-4456-9e70-5373cebea014` (1TB, was cloud-1)

Note: device letters may differ from what the VM saw. Use UUIDs.

- [ ] **Step 4: Install mergerfs on proxmoxmain**

```bash
ssh root@192.168.3.2 'bash -c "
  # Download latest mergerfs .deb for Debian
  MERGERFS_VERSION=\$(curl -s https://api.github.com/repos/trapexit/mergerfs/releases/latest | grep tag_name | cut -d\\\"  -f4)
  curl -LO \"https://github.com/trapexit/mergerfs/releases/download/\${MERGERFS_VERSION}/mergerfs_\${MERGERFS_VERSION}.debian-trixie_amd64.deb\"
  dpkg -i mergerfs_*.deb
  rm mergerfs_*.deb
  mergerfs --version
"'
```

Expected: mergerfs version printed.

- [ ] **Step 5: Create mount points and mount HDDs**

```bash
ssh root@192.168.3.2 'bash -c "
  mkdir -p /mnt/cloud-1 /mnt/cloud-2 /mnt/cloud
  
  # Mount by UUID
  mount UUID=64d9ab64-2f08-4456-9e70-5373cebea014 /mnt/cloud-1
  mount UUID=f635f68d-36ec-450c-bfb3-ac76cd9bdf96 /mnt/cloud-2
  
  # Verify
  ls /mnt/cloud-1/volumes/
  ls /mnt/cloud-2/volumes/
"'
```

Expected: both show `volumes/` directory with service subdirectories.

- [ ] **Step 6: Set up mergerfs pool**

```bash
ssh root@192.168.3.2 'mergerfs -o allow_other,fsname=cloud,category.create=mfs,use_ino,dropcacheonclose=true,cache.files=partial /mnt/cloud-1:/mnt/cloud-2 /mnt/cloud'
```

Verify:
```bash
ssh root@192.168.3.2 'df -h /mnt/cloud && ls /mnt/cloud/volumes/'
```

Expected: ~4.5TB pool, all service directories visible.

- [ ] **Step 7: Add to fstab for persistence**

```bash
ssh root@192.168.3.2 'bash -c "
  cat >> /etc/fstab << EOF

# Data disks
UUID=64d9ab64-2f08-4456-9e70-5373cebea014  /mnt/cloud-1  ext4  defaults  0 0
UUID=f635f68d-36ec-450c-bfb3-ac76cd9bdf96  /mnt/cloud-2  ext4  defaults  0 0

# mergerfs pool
/mnt/cloud-1:/mnt/cloud-2  /mnt/cloud  fuse.mergerfs  allow_other,fsname=cloud,category.create=mfs,use_ino,dropcacheonclose=true,cache.files=partial  0 0
EOF
"'
```

- [ ] **Step 8: Verify data integrity**

Spot-check some known paths:
```bash
ssh root@192.168.3.2 'bash -c "
  echo \"Movies dir:\"; ls /mnt/cloud/volumes/mediaserver/movies/ | head -5
  echo \"Immich data:\"; ls /mnt/cloud/volumes/mediaserver/immich/data/ | head -5
  echo \"Samba config:\"; ls /mnt/cloud/volumes/samba/config/
"'
```

- [ ] **Step 9: Commit**

```bash
git commit -m "Phase 3a: HDDs reclaimed, mergerfs pool active on proxmoxmain host" --allow-empty
```

---

### Task 7: Create ct-media on proxmoxmain

**Target:** LXC container running the full media stack with iGPU passthrough and mergerfs storage.

- [ ] **Step 1: Create the LXC container**

```bash
ssh root@192.168.3.2 'pct create 105 local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
  --hostname ct-media \
  --cores 4 \
  --memory 8192 \
  --swap 2048 \
  --storage local-lvm \
  --rootfs local-lvm:16 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.3.8/24,gw=192.168.3.1,firewall=1 \
  --nameserver 192.168.3.5 \
  --features nesting=1,keyctl=1 \
  --onboot 1 \
  --start 0'
```

- [ ] **Step 2: Configure storage bind mounts and iGPU**

```bash
ssh root@192.168.3.2 'bash -c "
  cat >> /etc/pve/lxc/105.conf << EOF
mp0: /mnt/cloud/volumes/mediaserver,mp=/mnt/mediaserver
lxc.cgroup2.devices.allow: c 226:128 rwm
lxc.mount.entry: /dev/dri/renderD128 dev/dri/renderD128 none bind,optional,create=file
EOF
"'
```

- [ ] **Step 3: Start CT and install Docker**

```bash
ssh root@192.168.3.2 'pct start 105'
ssh root@192.168.3.2 'pct exec 105 -- bash -c "
  apt-get update &&
  apt-get install -y ca-certificates curl gnupg &&
  install -m 0755 -d /etc/apt/keyrings &&
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc &&
  chmod a+r /etc/apt/keyrings/docker.asc &&
  echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \$(. /etc/os-release && echo \$VERSION_CODENAME) stable\" > /etc/apt/sources.list.d/docker.list &&
  apt-get update &&
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
"'
```

- [ ] **Step 4: Verify storage and iGPU**

```bash
ssh root@192.168.3.2 'pct exec 105 -- bash -c "ls /mnt/mediaserver/ && ls /dev/dri/"'
```

Expected: mediaserver subdirectories visible, `renderD128` present.

- [ ] **Step 5: Write the media stack compose file locally**

Create `stacks/ct-media/docker-compose.yml`:

```yaml
services:
  jellyfin:
    image: lscr.io/linuxserver/jellyfin:latest
    container_name: jellyfin
    restart: unless-stopped
    ports:
      - "8096:8096"
      - "8920:8920"
      - "1900:1900/udp"
      - "7359:7359/udp"
    environment:
      PUID: "1000"
      PGID: "1000"
      TZ: Europe/Rome
      DOCKER_MODS: linuxserver/mods:jellyfin-opencl-intel
    volumes:
      - /mnt/mediaserver/jellyfin/config:/config
      - /mnt/mediaserver/movies:/data/movies
      - /mnt/mediaserver/series:/data/tvshows
    devices:
      - /dev/dri/renderD128:/dev/dri/renderD128

  sonarr:
    image: lscr.io/linuxserver/sonarr:latest
    container_name: sonarr
    restart: unless-stopped
    ports:
      - "8989:8989"
    environment:
      PUID: "1000"
      PGID: "1000"
      TZ: Europe/Rome
    volumes:
      - /mnt/mediaserver/sonarr/config:/config
      - /mnt/mediaserver/series:/tv
      - /mnt/mediaserver/downloads:/downloads

  radarr:
    image: lscr.io/linuxserver/radarr:latest
    container_name: radarr
    restart: unless-stopped
    ports:
      - "7878:7878"
    environment:
      PUID: "1000"
      PGID: "1000"
      TZ: Europe/Rome
    volumes:
      - /mnt/mediaserver/radarr/config:/config
      - /mnt/mediaserver/movies:/movies
      - /mnt/mediaserver/downloads:/downloads

  deluge:
    image: lscr.io/linuxserver/deluge:latest
    container_name: deluge
    restart: unless-stopped
    ports:
      - "8112:8112"
      - "6881:6881"
      - "6881:6881/udp"
      - "58846:58846"
      - "58946:58946"
    environment:
      PUID: "1000"
      PGID: "1000"
      TZ: Europe/Rome
    volumes:
      - /mnt/mediaserver/deluge/config:/config
      - /mnt/mediaserver/downloads:/downloads

  prowlarr:
    image: lscr.io/linuxserver/prowlarr:latest
    container_name: prowlarr
    restart: unless-stopped
    ports:
      - "9696:9696"
    environment:
      PUID: "1000"
      PGID: "1000"
      TZ: Europe/Rome
    volumes:
      - /mnt/mediaserver/prowlarr/config:/config
      - /mnt/mediaserver/downloads:/downloads

  flaresolverr:
    image: ghcr.io/flaresolverr/flaresolverr:latest
    container_name: flaresolverr
    restart: unless-stopped
    ports:
      - "8191:8191"
    environment:
      TZ: Europe/Rome
```

- [ ] **Step 6: Deploy and start media stack**

```bash
ssh root@192.168.3.2 'pct exec 105 -- mkdir -p /opt/stacks/ct-media'
scp stacks/ct-media/docker-compose.yml root@192.168.3.2:/tmp/docker-compose-media.yml
ssh root@192.168.3.2 'pct push 105 /tmp/docker-compose-media.yml /opt/stacks/ct-media/docker-compose.yml'
ssh root@192.168.3.2 'pct exec 105 -- bash -c "cd /opt/stacks/ct-media && docker compose up -d"'
```

- [ ] **Step 7: Verify media stack**

```bash
# All containers running
ssh root@192.168.3.2 'pct exec 105 -- docker ps'

# Jellyfin UI accessible with media library
curl -s -o /dev/null -w "%{http_code}" http://192.168.3.8:8096

# Sonarr accessible
curl -s -o /dev/null -w "%{http_code}" http://192.168.3.8:8989

# Radarr accessible
curl -s -o /dev/null -w "%{http_code}" http://192.168.3.8:7878

# Deluge accessible
curl -s -o /dev/null -w "%{http_code}" http://192.168.3.8:8112

# Prowlarr accessible
curl -s -o /dev/null -w "%{http_code}" http://192.168.3.8:9696
```

Expected: all return 200 (or 302 redirect). Jellyfin should show existing libraries since it's reading the same config and media paths.

- [ ] **Step 8: Set up SSH access**

```bash
ssh root@192.168.3.2 'pct exec 105 -- bash -c "
  mkdir -p /root/.ssh && chmod 700 /root/.ssh
"'
ssh root@192.168.3.2 "pct exec 105 -- bash -c \"echo '$(cat ~/.ssh/id_ed25519.pub)' >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys\""
ssh -o StrictHostKeyChecking=accept-new root@192.168.3.8 'hostname'
```

- [ ] **Step 9: Commit**

```bash
git add stacks/ct-media/docker-compose.yml
git commit -m "Add ct-media stack — jellyfin, sonarr, radarr, deluge, prowlarr, flaresolverr"
```

---

### Task 8: Create ct-photos on proxmoxmain

**Target:** LXC container running Immich with iGPU passthrough.

- [ ] **Step 1: Create the LXC container**

```bash
ssh root@192.168.3.2 'pct create 106 local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
  --hostname ct-photos \
  --cores 4 \
  --memory 8192 \
  --swap 2048 \
  --storage local-lvm \
  --rootfs local-lvm:16 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.3.9/24,gw=192.168.3.1,firewall=1 \
  --nameserver 192.168.3.5 \
  --features nesting=1,keyctl=1 \
  --onboot 1 \
  --start 0'
```

- [ ] **Step 2: Configure storage and iGPU**

```bash
ssh root@192.168.3.2 'bash -c "
  cat >> /etc/pve/lxc/106.conf << EOF
mp0: /mnt/cloud/volumes/mediaserver/immich,mp=/mnt/immich
lxc.cgroup2.devices.allow: c 226:128 rwm
lxc.mount.entry: /dev/dri/renderD128 dev/dri/renderD128 none bind,optional,create=file
EOF
"'
```

- [ ] **Step 3: Start CT and install Docker**

```bash
ssh root@192.168.3.2 'pct start 106'
ssh root@192.168.3.2 'pct exec 106 -- bash -c "
  apt-get update &&
  apt-get install -y ca-certificates curl gnupg &&
  install -m 0755 -d /etc/apt/keyrings &&
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc &&
  chmod a+r /etc/apt/keyrings/docker.asc &&
  echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \$(. /etc/os-release && echo \$VERSION_CODENAME) stable\" > /etc/apt/sources.list.d/docker.list &&
  apt-get update &&
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
"'
```

- [ ] **Step 4: Write the Immich compose file locally**

Create `stacks/ct-photos/docker-compose.yml`:

```yaml
services:
  immich-server:
    image: ghcr.io/immich-app/immich-server:release
    container_name: immich_server
    restart: unless-stopped
    ports:
      - "2283:2283"
    environment:
      DB_HOSTNAME: immich_postgres
      DB_USERNAME: postgres
      DB_DATABASE_NAME: immich
      DB_PASSWORD: "${DB_PASSWORD}"
      REDIS_HOSTNAME: immich_redis
      TZ: Europe/Rome
    volumes:
      - /mnt/immich/data:/usr/src/app/upload
    depends_on:
      - immich-postgres
      - immich-redis

  immich-machine-learning:
    image: ghcr.io/immich-app/immich-machine-learning:release
    container_name: immich_machine_learning
    restart: unless-stopped
    environment:
      TZ: Europe/Rome
    volumes:
      - /mnt/immich/cache:/cache
    devices:
      - /dev/dri/renderD128:/dev/dri/renderD128

  immich-postgres:
    image: tensorchord/pgvecto-rs:pg14-v0.2.0
    container_name: immich_postgres
    restart: unless-stopped
    environment:
      POSTGRES_PASSWORD: "${DB_PASSWORD}"
      POSTGRES_USER: postgres
      POSTGRES_DB: immich
      POSTGRES_INITDB_ARGS: "--data-checksums"
    volumes:
      - /mnt/immich/db:/var/lib/postgresql/data

  immich-redis:
    image: redis:6.2-alpine
    container_name: immich_redis
    restart: unless-stopped
    healthcheck:
      test: redis-cli ping || exit 1
```

Create `stacks/ct-photos/.env.example`:

```
DB_PASSWORD=your-immich-db-password-here
```

- [ ] **Step 5: Deploy and start Immich**

```bash
ssh root@192.168.3.2 'pct exec 106 -- mkdir -p /opt/stacks/ct-photos'
scp stacks/ct-photos/docker-compose.yml stacks/ct-photos/.env.example root@192.168.3.2:/tmp/
ssh root@192.168.3.2 'pct push 106 /tmp/docker-compose.yml /opt/stacks/ct-photos/docker-compose.yml'

# Create .env with actual password on the CT
ssh root@192.168.3.2 "pct exec 106 -- bash -c 'echo \"DB_PASSWORD=<redacted>\" > /opt/stacks/ct-photos/.env'"

ssh root@192.168.3.2 'pct exec 106 -- bash -c "cd /opt/stacks/ct-photos && docker compose up -d"'
```

- [ ] **Step 6: Verify Immich**

```bash
# All containers running and healthy
ssh root@192.168.3.2 'pct exec 106 -- docker ps'

# UI accessible
curl -s -o /dev/null -w "%{http_code}" http://192.168.3.9:2283

# Check logs for ML initialization
ssh root@192.168.3.2 'pct exec 106 -- docker logs immich_machine_learning 2>&1 | tail -10'
```

Expected: UI returns 200, ML logs show model loading. Photo library should appear since it's reading the same data directory.

- [ ] **Step 7: Set up SSH access**

```bash
ssh root@192.168.3.2 'pct exec 106 -- bash -c "mkdir -p /root/.ssh && chmod 700 /root/.ssh"'
ssh root@192.168.3.2 "pct exec 106 -- bash -c \"echo '$(cat ~/.ssh/id_ed25519.pub)' >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys\""
ssh -o StrictHostKeyChecking=accept-new root@192.168.3.9 'hostname'
```

- [ ] **Step 8: Commit**

```bash
git add stacks/ct-photos/docker-compose.yml stacks/ct-photos/.env.example
git commit -m "Add ct-photos Immich stack with iGPU passthrough"
```

---

### Task 9: Create ct-files on proxmoxmain

**Target:** LXC container running Samba + FileBrowser for file access.

- [ ] **Step 1: Create the LXC container**

```bash
ssh root@192.168.3.2 'pct create 107 local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
  --hostname ct-files \
  --cores 1 \
  --memory 1024 \
  --swap 512 \
  --storage local-lvm \
  --rootfs local-lvm:4 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.3.11/24,gw=192.168.3.1,firewall=1 \
  --nameserver 192.168.3.5 \
  --features nesting=1,keyctl=1 \
  --onboot 1 \
  --start 0'
```

- [ ] **Step 2: Configure storage bind mount**

```bash
ssh root@192.168.3.2 'bash -c "
  echo \"mp0: /mnt/cloud,mp=/mnt/cloud\" >> /etc/pve/lxc/107.conf
"'
```

- [ ] **Step 3: Start CT and install Docker**

```bash
ssh root@192.168.3.2 'pct start 107'
ssh root@192.168.3.2 'pct exec 107 -- bash -c "
  apt-get update &&
  apt-get install -y ca-certificates curl gnupg &&
  install -m 0755 -d /etc/apt/keyrings &&
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc &&
  chmod a+r /etc/apt/keyrings/docker.asc &&
  echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \$(. /etc/os-release && echo \$VERSION_CODENAME) stable\" > /etc/apt/sources.list.d/docker.list &&
  apt-get update &&
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
"'
```

- [ ] **Step 4: Write the files stack compose locally**

Create `stacks/ct-files/docker-compose.yml`:

```yaml
services:
  samba:
    image: dperson/samba
    container_name: samba
    restart: unless-stopped
    ports:
      - "139:139"
      - "445:445"
    volumes:
      - /mnt/cloud/volumes/samba/config:/etc/samba
      - /mnt/cloud/volumes/samba/data:/mount
      - /mnt/cloud/volumes/samba/users:/var/lib/samba
    environment:
      TZ: Europe/Rome

  filebrowser:
    image: filebrowser/filebrowser:latest
    container_name: filebrowser
    restart: unless-stopped
    ports:
      - "8080:80"
    volumes:
      - /mnt/cloud:/srv
      - filebrowser-db:/database
    environment:
      FB_DATABASE: /database/filebrowser.db
      FB_ROOT: /srv

volumes:
  filebrowser-db:
```

- [ ] **Step 5: Deploy and start**

```bash
ssh root@192.168.3.2 'pct exec 107 -- mkdir -p /opt/stacks/ct-files'
scp stacks/ct-files/docker-compose.yml root@192.168.3.2:/tmp/docker-compose-files.yml
ssh root@192.168.3.2 'pct push 107 /tmp/docker-compose-files.yml /opt/stacks/ct-files/docker-compose.yml'
ssh root@192.168.3.2 'pct exec 107 -- bash -c "cd /opt/stacks/ct-files && docker compose up -d"'
```

- [ ] **Step 6: Verify Samba**

```bash
# From blvckmain, test SMB connection
smbclient -L //192.168.3.11 -N 2>&1 | head -10
```

- [ ] **Step 7: Verify FileBrowser**

```bash
curl -s -o /dev/null -w "%{http_code}" http://192.168.3.11:8080
```

Expected: 200. Default login is `admin`/`admin` — change on first login.

Browse to `http://192.168.3.11:8080` in a browser and verify files are visible.

- [ ] **Step 8: Set up SSH access**

```bash
ssh root@192.168.3.2 'pct exec 107 -- bash -c "mkdir -p /root/.ssh && chmod 700 /root/.ssh"'
ssh root@192.168.3.2 "pct exec 107 -- bash -c \"echo '$(cat ~/.ssh/id_ed25519.pub)' >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys\""
ssh -o StrictHostKeyChecking=accept-new root@192.168.3.11 'hostname'
```

- [ ] **Step 9: Commit**

```bash
git add stacks/ct-files/docker-compose.yml
git commit -m "Add ct-files Samba + FileBrowser stack"
```

---

### Task 10: Decommission blvckserver and final cleanup

- [ ] **Step 1: Final verification of all services**

```bash
# ct-dns
dig @192.168.3.5 google.com +short

# ct-tunnel
ssh root@192.168.3.6 'docker logs cloudflared 2>&1 | grep -i connected | tail -1'

# ct-nvr
curl -s -o /dev/null -w "%{http_code}" http://192.168.3.7:8971

# ct-media
curl -s -o /dev/null -w "%{http_code}" http://192.168.3.8:8096
curl -s -o /dev/null -w "%{http_code}" http://192.168.3.8:8989
curl -s -o /dev/null -w "%{http_code}" http://192.168.3.8:7878

# ct-photos
curl -s -o /dev/null -w "%{http_code}" http://192.168.3.9:2283

# ct-files
curl -s -o /dev/null -w "%{http_code}" http://192.168.3.11:8080
smbclient -L //192.168.3.11 -N 2>&1 | head -5
```

All should return 200 or valid responses.

- [ ] **Step 2: Verify all CTs autostart on boot**

```bash
for id in 102 103 104 105 106 107; do
  echo "CT $id: $(ssh root@192.168.3.2 "pct config $id 2>/dev/null | grep onboot" || ssh root@192.168.3.3 "pct config $id 2>/dev/null | grep onboot")"
done
```

Expected: all show `onboot: 1`.

- [ ] **Step 3: Destroy blvckserver VM**

```bash
ssh root@192.168.3.2 'qm destroy 100 --purge'
```

This removes the VM and its boot disk from local-lvm, freeing ~233GB of NVMe space.

- [ ] **Step 4: Clean up stale data from mergerfs pool**

```bash
ssh root@192.168.3.2 'bash -c "
  # List stale directories
  for d in gitlab firefly firefly-iii minecraft minecraft-fabric minecraft-new satisfactory rocket_chat supersonic-data homeassistant photprism plex temp; do
    if [ -d \"/mnt/cloud/volumes/\$d\" ]; then
      echo \"Removing: /mnt/cloud/volumes/\$d ($(du -sh /mnt/cloud/volumes/\$d 2>/dev/null | cut -f1))\"
      rm -rf \"/mnt/cloud/volumes/\$d\"
    fi
  done
"'
```

- [ ] **Step 5: Update documentation**

Update `CLAUDE.md` to remove blvckserver entry and add the 6 CTs.
Update `docs/migration.md` to mark all phases complete.
Update `docs/services.md` to reflect the new architecture.
Update `docs/hardware.md` to reflect reclaimed storage.

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "Complete blvckserver decomposition — all services migrated to 6 LXC containers"
```

- [ ] **Step 7: Push to remote**

```bash
git push origin master
```
