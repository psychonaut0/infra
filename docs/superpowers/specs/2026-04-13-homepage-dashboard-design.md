# Homepage Dashboard — Design Spec

## Overview

Configure the Homepage dashboard (running on ct-mgmt at `http://home.lan`) as the primary launcher for all home lab services. Keep it fast to use from desktop (primary) and mobile. Show light health-check and live-stat widgets for the services where that info matters, but keep most cards as simple launch tiles.

## Audience & Use Case

- **Single user** (no family mode, no simplified view)
- **Primary device:** desktop (4-column rows). Mobile works via Homepage's responsive default.
- **Primary purpose:** quick launcher. Secondary: light health checks and at-a-glance live data on frequently-used services.

## Layout

Single-page scrollable dashboard, dark theme (slate), title "Home Lab", with:

- Search bar at top → Google
- Time/date widget at top
- Service sections (ordered by frequency of use)
- Bookmarks section (2 groups side by side)
- System resources widget at bottom (ct-mgmt CPU/RAM/disk)

### Service Sections

Sections are ordered by access frequency. Each section renders as a row with cards.

1. **Quick Access** — daily services
   - Jellyfin
   - Immich
   - Home Assistant

2. **Often** — weekly/frequently-used
   - Portainer
   - Proxmox
   - Frigate

3. **Infrastructure** — admin/network
   - Pi-hole
   - Proxmox-node
   - Cloudflare Tunnel (link to Cloudflare Zero Trust)

4. **Media Tools** — less-used but useful to have
   - Sonarr
   - Radarr
   - Deluge
   - Prowlarr
   - FlareSolverr

5. **Files**
   - FileBrowser

### Bookmarks

Two groups side by side:

**Dev**
- GitHub → `https://github.com/psychonaut0`
- Bitbucket → `https://bitbucket.org`

**Infra**
- Cloudflare → `https://one.dash.cloudflare.com`
- UniFi → `https://192.168.1.1`
- Tailscale → `https://login.tailscale.com`
- Njalla → `https://njal.la`

## Widgets (Live Data)

Enable full widgets only on these 6 services. Everything else remains as a plain launch tile with a description.

| Service | Widget Data | Auth Method |
|---------|-------------|-------------|
| Proxmox (main + node) | CPU, RAM, disk, VM/CT count | API token (user@pam with custom token) |
| Jellyfin | Now-playing count, library sizes | API key |
| Immich | Photo + video counts | API key |
| Home Assistant | Entity counts + 2-3 key sensors | Long-lived access token |
| Pi-hole | Queries blocked today, adlists | API token |
| Frigate | Camera status, recent events | None (public endpoint) |

API tokens stored in `/opt/stacks/ct-mgmt/homepage/.env` on the CT (not in git). The repo keeps `.env.example` as documentation. Services reference tokens via `{{HOMEPAGE_VAR_*}}` substitution.

### Widgets explicitly skipped

- Sonarr, Radarr, Deluge, Prowlarr — rarely used interactively; queue/count info doesn't drive action
- Cloudflare Tunnel, Portainer — launch tiles only
- Per-CT Docker socket integration — Proxmox widget already covers CT/VM counts

## System Monitoring

Proxmox widgets cover infrastructure monitoring (both nodes' CPU/RAM/disk, VM/CT counts). No separate monitoring layer (no glances, no Docker socket proxies). A single `resources` widget at the bottom shows ct-mgmt's own CPU/RAM/disk so the dashboard host itself is visible.

## Settings

- **Title:** Home Lab
- **Theme:** dark
- **Color:** slate
- **Header style:** clean
- **Layout:** row-based per section
  - Quick Access: 3 columns
  - Often: 3 columns
  - Infrastructure: 3 columns
  - Media Tools: 5 columns
  - Files: 1 column

## Access Control

Homepage itself requires no login. Since `home.lan` resolves only on the LAN and requires Tailscale from outside, this is acceptable. Future consideration: put Homepage behind Authelia if access model changes.

## File Structure

No new files; rewrite existing Homepage config files.

```
stacks/ct-mgmt/homepage/
  settings.yaml     # Title, theme, layout settings
  services.yaml     # All service sections with widgets
  bookmarks.yaml    # Dev + Infra bookmark groups
  widgets.yaml      # Search, resources, time/date
  .env.example      # Template documenting required vars
```

The actual `.env` with real tokens lives only on ct-mgmt at `/opt/stacks/ct-mgmt/homepage/.env` (compose loads it into the container).

## Out of Scope

- Multi-user modes or role-based service hiding
- Authentication in front of Homepage
- Monitoring rebuild (Grafana/Prometheus) — separate future project
- Custom themes or CSS overrides
