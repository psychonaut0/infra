# infra

Home-lab infrastructure as code. Documents and provisions a small Proxmox
cluster running about a dozen LXC containers behind a UniFi gateway, along
with a small Go CLI (`infra`) for day-to-day fleet operations.

This repo is opened for reference and portfolio purposes. It's tightly
tailored to one set of hardware and won't run as-is on yours — read it as a
worked example.

## Stack at a glance

- **Hypervisor:** Proxmox VE, two-node cluster (`proxmoxmain`, `proxmoxnode`).
- **Workloads:** all in LXC, each container's stack lives in `stacks/ct-<name>/`
  (Docker Compose where it makes sense; native systemd where it doesn't).
- **Services:** media (Jellyfin + *arr + Deluge + Prowlarr), photos (Immich),
  NVR (Frigate with iGPU passthrough), home automation (Home Assistant +
  Mosquitto), DNS (Pi-hole), reverse proxy (Caddy), monitoring (Gatus +
  Telegram alerts), off-site backups (restic to Backblaze B2), Cloudflare
  Tunnel, Minecraft servers, custom Home Lab dashboard.
- **Storage:** mergerfs pool on the primary node, bind-mounted into the CTs
  that consume it.
- **CLI:** `cli/` — Go binary distributed via a LAN release mirror.
  Source-of-truth for the common fleet operations.

## Layout

```
cli/                   Go CLI for fleet operations
stacks/<ct-name>/      Per-container Compose stacks + configs
scripts/               Misc utility scripts (CT bootstrap, daemon configs, ...)
docs/                  Hardware notes, design specs, implementation plans
CLAUDE.md              Detailed fleet map (used as Claude Code context)
```

## The `infra` CLI

Replaces ad-hoc SSH for the common chores. A few of the subcommands:

| Task                              | Command                                |
| --------------------------------- | -------------------------------------- |
| Service → CT mapping              | `infra ls`                             |
| Container state across the fleet  | `infra status`                         |
| Tail a service's logs             | `infra logs <service>`                 |
| Restart / redeploy                | `infra restart <service>` / `infra deploy <service>` |
| Add/remove a `<name>.lan` service | `infra dns add <name>.lan <upstream>`  |
| Self-update from LAN mirror       | `infra update`                         |

Build locally with `cd cli && make install`. Design notes in
`docs/superpowers/specs/`.

## License

MIT — see [LICENSE](LICENSE).
