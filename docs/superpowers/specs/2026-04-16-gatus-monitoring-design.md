# Gatus Monitoring — Design Spec

## Overview

Deploy Gatus as a monitoring + alerting layer on ct-mgmt, alongside the existing dashboard, Portainer, and Caddy. Monitors all 14 services across 3 priority tiers. Alerts via Telegram on failure and recovery. Status page exposed at `http://status.lan`.

## Architecture

Gatus runs as a new Docker service in `stacks/ct-mgmt/docker-compose.yml`. It reaches all services via their LAN IPs (same as the dashboard does today). Telegram notifications go out directly from the container via the Telegram Bot API.

```
ct-mgmt (192.168.3.12)
├── caddy          (80/443)  — reverse proxy for .lan services
├── portainer      (9000/9443) — container management
├── dashboard      (3000)    — custom Preact SSR dashboard (home.lan)
└── gatus          (8080)    — monitoring + alerting (status.lan)
```

## Monitoring Tiers

### Tier 1 — Critical (30s interval, alert after 2 consecutive failures)

| Service | URL | Check |
|---------|-----|-------|
| Home Assistant | http://192.168.3.10:8123 | HTTP status 200-399 |
| Frigate | http://192.168.3.7:5000 | HTTP status > 0 |
| Immich | http://192.168.3.9:2283 | HTTP status 200-399 |
| Jellyfin | http://192.168.3.8:8096 | HTTP status 200-399 |
| Caddy | http://caddy:80 | HTTP status 200-399 |

### Tier 2 — Important (60s interval, alert after 3 consecutive failures)

| Service | URL | Check |
|---------|-----|-------|
| Portainer | http://portainer:9000 | HTTP status 200-399 |
| Proxmox Main | https://192.168.3.2:8006 | HTTP status 200-399 (TLS skip verify) |
| Proxmox Node | https://192.168.3.3:8006 | HTTP status 200-399 (TLS skip verify) |
| Pi-hole | http://192.168.3.5 | HTTP status 200-399 |
| FileBrowser | http://192.168.3.11:8080 | HTTP status 200-399 |

### Tier 3 — Background (120s interval, alert after 3 consecutive failures)

| Service | URL | Check |
|---------|-----|-------|
| Sonarr | http://192.168.3.8:8989 | HTTP status 200-399 |
| Radarr | http://192.168.3.8:7878 | HTTP status 200-399 |
| Deluge | http://192.168.3.8:8112 | HTTP status 200-399 |
| Prowlarr | http://192.168.3.8:9696 | HTTP status 200-399 |
| FlareSolverr | http://192.168.3.8:8191 | HTTP status 200-399 |

## Alerting

- **Provider:** Telegram Bot API
- **Credentials:** `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` from `gatus/.env` (gitignored)
- **Behavior:** Alert on failure, alert on recovery ("service X is back up")
- **Setup:** User creates bot via @BotFather, provides token + chat ID

## Status Page

- Gatus built-in UI exposed at `http://status.lan`
- Caddy route: `reverse_proxy gatus:8080`
- Pihole DNS: `status.lan` → `192.168.3.12`

## Dashboard Relationship

The existing custom dashboard at `home.lan` continues to run its own 60s health-check loop independently. No changes to dashboard code. Gatus and the dashboard serve different purposes: dashboard is the live overview, Gatus is alerting + history.

## Files Changed

**New files:**
- `stacks/ct-mgmt/gatus/config.yaml` — full Gatus config (monitors, alerting, UI settings)
- `stacks/ct-mgmt/gatus/.env.example` — placeholder for Telegram credentials

**Modified files:**
- `stacks/ct-mgmt/docker-compose.yml` — add `gatus` service
- `stacks/ct-mgmt/Caddyfile` — add `status.lan` route

**Not in git:**
- `stacks/ct-mgmt/gatus/.env` — real Telegram token + chat ID

## Deploy Steps

1. Write all config files locally, commit + push to GitHub
2. Sync updated files to ct-mgmt (`/opt/stacks/ct-mgmt/`)
3. Add pihole DNS entry: `status.lan` → `192.168.3.12`
4. Run `docker compose up -d` on ct-mgmt (pulls Gatus image, recreates Caddy for new config)
5. User creates Telegram bot, provides token + chat ID
6. Write `.env` on ct-mgmt with Telegram credentials
7. Restart Gatus to pick up credentials
8. Send test alert to verify Telegram delivery
