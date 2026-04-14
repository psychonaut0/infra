# Homepage Dashboard — Design Spec

## Overview

Custom React static dashboard served by Caddy on ct-mgmt at `http://home.lan`. Built locally, static output deployed to ct-mgmt. Neumorphic dark UI with health check pings.

## Tech Stack

- React (Vite build → static HTML/JS/CSS)
- Neumorphism CSS (custom, no framework)
- Health checks via `fetch()` to each service URL
- Served by Caddy (already running on ct-mgmt) — no extra container

## Layout

```
| QUICK ACCESS                     | OFTEN                           |
| [Home Assistant - full width]    | [Portainer - full width]        |
| [Jellyfin]      [Immich]        | [Proxmox]      [Frigate]        |
|----------------------------------------------------------------------|
| INFRASTRUCTURE                                                       |
| [Pi-hole]    [Proxmox Node]    [FileBrowser]                        |
|----------------------------------------------------------------------|
| MEDIA TOOLS                                                          |
| [Sonarr] [Radarr] [Deluge] [Prowlarr] [FlareSolverr]               |
|----------------------------------------------------------------------|
| BOOKMARKS                                                            |
| Dev: GitHub, Bitbucket  |  Infra: Cloudflare, UniFi, Tailscale, Njalla |
```

## Neumorphism Style

- Background: `#2a2a3c` (dark purple-gray)
- Cards: same background color, raised with dual box-shadows (dark below-right, light above-left)
- Hover: shadows compress slightly
- Active/click: inset shadows (pressed effect)
- Search bar: inset neumorphic
- Status dots: small inset circle, green/red
- Section headers: uppercase, small, dimmed text
- Font: system sans-serif, light weight

## Services

### Quick Access
| Service | URL | Ping |
|---------|-----|------|
| Home Assistant | http://homeassistant.lan | http://192.168.3.10:8123 |
| Jellyfin | http://jellyfin.lan | http://192.168.3.8:8096 |
| Immich | http://immich.lan | http://192.168.3.9:2283 |

### Often
| Service | URL | Ping |
|---------|-----|------|
| Portainer | http://portainer.lan | http://192.168.3.12:9000 |
| Proxmox | https://proxmox.lan | https://192.168.3.2:8006 |
| Frigate | http://nvr.lan | http://192.168.3.7:5000 |

### Infrastructure
| Service | URL | Ping |
|---------|-----|------|
| Pi-hole | http://dns.lan | http://192.168.3.5 |
| Proxmox Node | https://proxmox-node.lan | https://192.168.3.3:8006 |
| FileBrowser | http://files.lan | http://192.168.3.11:8080 |

### Media Tools
| Service | URL | Ping |
|---------|-----|------|
| Sonarr | http://192.168.3.8:8989 | same |
| Radarr | http://192.168.3.8:7878 | same |
| Deluge | http://192.168.3.8:8112 | same |
| Prowlarr | http://192.168.3.8:9696 | same |
| FlareSolverr | http://192.168.3.8:8191 | same |

### Bookmarks
| Group | Links |
|-------|-------|
| Dev | GitHub (github.com/psychonaut0), Bitbucket (bitbucket.org) |
| Infra | Cloudflare (one.dash.cloudflare.com), UniFi (192.168.1.1), Tailscale (login.tailscale.com), Njalla (njal.la) |

## Health Check Behavior

- On page load: fetch each ping URL with `mode: 'no-cors'`
- Success (any response) → green dot
- Failure (timeout/network error) → red dot
- Re-check every 60 seconds
- No API keys needed — just reachability

## Deployment

- Build locally: `npm run build` → `dist/` folder
- Deploy: `scp dist/* root@192.168.3.12:/opt/stacks/ct-mgmt/dashboard/`
- Caddy serves from a volume mount or inline file_server
- Source lives in `stacks/ct-mgmt/dashboard-src/` in the infra repo

## What to remove

- Homepage container (ghcr.io/gethomepage/homepage)
- Homer container (b4bz/homer)
- `stacks/ct-mgmt/homepage/` config directory
- `stacks/ct-mgmt/homer/` config directory
- `stacks/ct-mgmt/dashy/` config directory
