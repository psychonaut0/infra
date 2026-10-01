# infra

Home-lab infrastructure as code. Documents and provisions a two-node Proxmox
cluster running fifteen LXC containers behind a UniFi gateway, along with a
small Go CLI (`infra`) for day-to-day fleet operations.

This repo is opened for reference and portfolio purposes. It's tightly
tailored to one set of hardware and won't run as-is on yours — read it as a
worked example. Values like `<PERSONAL_DOMAIN>` and `<PUBLIC_IP>` are
deliberate redactions, not template variables.

## Stack at a glance

- **Hypervisor:** Proxmox VE, two-node cluster (`proxmoxmain`, `proxmoxnode`).
  DNS, monitoring and home automation live on the smaller node so they
  survive an outage of the main one.
- **Workloads:** all in LXC, each container's stack lives in `stacks/ct-<name>/`
  (Docker Compose where it makes sense; native systemd where it doesn't).
- **GPU:** one GTX 1650 SUPER shared across three LXC containers — NVDEC/NVENC
  for Frigate and Jellyfin, CUDA for Immich's machine learning.
- **Services:** media (Jellyfin + *arr + Deluge + Prowlarr), photos (Immich),
  NVR (Frigate), file server (Samba + copyparty web drive), home automation
  (Home Assistant + Mosquitto + ESPHome), AI chat (Open WebUI over
  OpenRouter), DNS (Pi-hole), reverse proxy (Caddy), monitoring (Gatus +
  Telegram alerts), off-site backups (restic to Backblaze B2), Minecraft
  servers, a portfolio site, a workout-tracker backend, an always-on remote
  dev container, and a custom Home Lab dashboard.
- **Public access:** Cloudflare Tunnel for HTTP(S), so no web service needs an
  inbound port; a single port-forward for Minecraft.
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
AGENTS.md              Detailed fleet map (agent context; CLAUDE.md points here)
```

## The `infra` CLI

Replaces ad-hoc SSH for the common chores. A few of the subcommands:

| Task                              | Command                                |
| --------------------------------- | -------------------------------------- |
| Service → CT mapping              | `infra ls`                             |
| Container state across the fleet  | `infra status`                         |
| Proxmox CT overview               | `infra ct status`                      |
| Tail a service's logs             | `infra logs <service>`                 |
| Restart / redeploy                | `infra restart <service>` / `infra deploy <service>` |
| Add/remove a `<name>.lan` service | `infra dns add <name>.lan <upstream>`  |
| Audit Cloudflare Tunnel drift     | `infra tunnel diff`                    |
| Self-update from LAN mirror       | `infra update`                         |

Build locally with `cd cli && make install`. Design notes in
`docs/superpowers/specs/`.

## License

MIT — see [LICENSE](LICENSE).
