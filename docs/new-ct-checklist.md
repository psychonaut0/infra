# New CT Checklist

Every step needed to take a new LXC container from "provisioned" to "fully
integrated". Written after the ct-workout audit (2026-06-05) found four
post-deploy steps silently skipped — the CT ran fine for days with **zero
backups** and zero monitoring while every committed file looked correct.

The recurring failure mode: *committed ≠ deployed*. Most integration points
below have a repo half and a live half; the checklist calls out the live half
explicitly.

## 1. Provision (Proxmox)

- [ ] CT on **proxmoxmain** (proxmoxnode only with explicit permission)
- [ ] Unprivileged unless device passthrough is needed; Debian 13 template
- [ ] Docker-in-LXC config: `lxc.apparmor.profile: unconfined`,
      `lxc.mount.auto: proc:rw sys:rw`, `features: nesting=1,keyctl=1`
- [ ] `onboot: 1`, static IP on vmbr0 (gw 192.168.3.1, DNS 192.168.3.5),
      rootfs on `local-lvm`
- [ ] Description comment in `/etc/pve/lxc/<vmid>.conf` (`#ct-name - role`)

## 2. Base host

- [ ] SSH key auth for root (cluster authorized_keys)
- [ ] **`apt install rsync`** — provides `/usr/bin/rrsync`, which
      `backup-dispatch.sh` execs. Without it every `.env`/full-stack backup
      pull fails (rsync error 12) while pg dumps still work, so the failure is
      easy to miss. This bit ct-workout.
- [ ] infra CLI: `curl -fsSL http://infra-bin.lan/install.sh | sh`

## 3. Stack

- [ ] `/opt/stacks/ct-<name>/docker-compose.yml`, mirrored at
      `stacks/ct-<name>/` in this repo
- [ ] App images pinned to `:sha-<short>`; third-party images version-pinned
      for stateful services
- [ ] `restart: unless-stopped` + healthchecks on every long-lived service
- [ ] Secrets in `.env` (gitignored) with a committed `.env.example`
      (placeholders only); key material in `secrets/` (gitignored), mode 0600
- [ ] Containers run as a dedicated non-root UID where the image allows it
- [ ] portainer-agent service on :9001 (standard block)

## 4. Fleet registration

- [ ] `stacks/hosts.yaml` — IP entry
- [ ] `cli/internal/discover/fleet.json` — IP + service→CT mappings
- [ ] **Cut a release**: fleet.json is *embedded at build time*, so without a
      new tag every host keeps the old fleet. `git tag vX.Y.Z && git push
      origin vX.Y.Z` → CI builds the Release → infra-mirror syncs (≤5 min) →
      `infra update -y` on each host. Workstations with a repo checkout read
      fleet.json live and mask this gap — **prove it from a CT**:
      `ssh root@<any-ct> 'infra ls | grep <name>'`

## 5. DNS

- [ ] `infra dns add <name>.lan http://<ip>:<port>` per exposed service
      (writes Caddyfile + Pi-hole together); verify with `infra dns ls`
      and an end-to-end `curl http://<name>.lan/...`

## 6. Backups (repo half AND live half)

- [ ] `pre-backup.sh`: CT_IPS entry; `FULL_STACK_CTS` if state lives outside
      Docker volumes (bind-mounted config, `secrets/`); pg/sqlite dump calls
      for databases
- [ ] `backup-dispatch.sh`: dump cases gated by `ALLOW_*` flags
- [ ] On the new CT: `/usr/local/bin/backup-dispatch.sh` installed,
      `/etc/backup-dispatch.conf` with the `ALLOW_*` flags +
      `ALLOW_RSYNC_PATHS="/opt/stacks"`, forced-command key in
      `/root/.ssh/authorized_keys`
- [ ] **Deploy to ct-backup**: scp both scripts to
      `root@192.168.3.13:/usr/local/bin/` — `infra deploy` does NOT cover
      ct-backup
- [ ] **Prove it**: `systemctl start backup.service` on ct-backup, then check
      `/var/log/backup/pre-backup.log` has the new CT with no WARN lines, and
      `restic ls latest | grep <name>` shows the dumps/files with sane sizes

## 7. Monitoring & management

- [ ] Gatus: endpoints in `stacks/ct-mgmt/gatus/config.yaml` (tier per
      criticality, telegram alerts) → `infra deploy gatus` → checks green on
      http://status.lan
- [ ] Dashboard: entry in **both** `dashboard-src/server.js` (services array)
      and `dashboard-src/src/services.js` (sections; `ping` string must match
      server.js exactly — it's the join key) + icon in `public/icons/`.
      Then rebuild: `infra deploy dashboard` recreates **without** rebuilding —
      use `cd /opt/stacks/ct-mgmt && docker compose up -d --build dashboard`
- [ ] Portainer: register the environment (UI → Environments → Add → Agent →
      `<ip>:9001`) **within 72h** of the agent starting — after that the agent
      shuts its API down and needs `docker restart portainer-agent` first

## 8. Documentation

- [ ] `CLAUDE.md`: `### ct-<name>` section, proxmoxmain **CTs:** list,
      Network Layout SSH tree, **Services** prose line (keep placeholders —
      the file is sanitized for public release)
- [ ] `docs/hardware.md`: LXC boot-disk allocation row (VMID, LV, size, data%)
- [ ] Stack `README.md` for anything with manual steps — include a **restore**
      runbook, not just setup (e.g. plain `pg_dump` loses cluster roles)
