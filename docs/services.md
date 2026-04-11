# Services Inventory

All services run on **blvckserver** (VMID 100) as Docker containers, managed via Portainer CE.

Docker version: 28.4.0
Docker Compose plugin: **not installed** (neither v1 nor v2)
Compose files live inside Portainer's volume at `/data/compose/<id>/`.

## Running Stacks

### Network & Infrastructure

| Container | Image | Ports | Notes |
|-----------|-------|-------|-------|
| pihole | pihole/pihole:latest | 53/tcp+udp, 67/udp, 82→80 | DNS server, healthy |
| wireguard | lscr.io/linuxserver/wireguard:latest | 51820/udp | VPN |
| nginx-proxy-manager_app_1 | jc21/nginx-proxy-manager:latest | 80, 81, 443 | Reverse proxy |
| nginx-proxy-manager_db_1 | jc21/mariadb-aria:latest | — | NPM database |
| cloudflare-ddns_cloudflare-ddns_1 | oznu/cloudflare-ddns:latest | — | **RESTARTING** |

### Media Server

| Container | Image | Ports | Notes |
|-----------|-------|-------|-------|
| jellyfin | lscr.io/linuxserver/jellyfin:latest | 8096, 8920, 1900/udp, 7359/udp | Media streaming, uses iGPU |
| sonarr | lscr.io/linuxserver/sonarr:latest | 8989 | TV series management |
| radarr | lscr.io/linuxserver/radarr:latest | 7878 | Movie management |
| deluge | lscr.io/linuxserver/deluge:latest | 8112, 6881, 58846, 58946 | Torrent client |
| jackett | lscr.io/linuxserver/jackett:latest | 9117 | Torrent indexer proxy |
| prowlarr | lscr.io/linuxserver/prowlarr:latest | 9696 | Indexer manager |
| flaresolverr | ghcr.io/flaresolverr/flaresolverr:latest | 8191 | Cloudflare bypass for indexers |

### Photos & Media Management

| Container | Image | Ports | Notes |
|-----------|-------|-------|-------|
| immich_server | ghcr.io/immich-app/immich-server:release | 2284→2283 | Photo management, healthy |
| immich-machine-learning | ghcr.io/immich-app/immich-machine-learning:release | — | ML inference, healthy |
| immich_postgres | tensorchord/pgvecto-rs:pg14-v0.2.0 | 5432 (internal) | Immich database, healthy |
| immich_redis | redis:6.2-alpine | 6379 (internal) | Immich cache, healthy |

### NVR / Security

| Container | Image | Ports | Notes |
|-----------|-------|-------|-------|
| frigate | ghcr.io/blakeblackshear/frigate:stable | 8971, 8554-8555 | NVR, uses iGPU, healthy |

### Monitoring

| Container | Image | Ports | Notes |
|-----------|-------|-------|-------|
| grafana | grafana/grafana:latest | 3000 | Dashboards |
| prometheus | prom/prometheus:latest | — | **RESTARTING** |
| node_exporter | quay.io/prometheus/node-exporter:latest | 9100 (internal) | Host metrics |

### Applications

| Container | Image | Ports | Notes |
|-----------|-------|-------|-------|
| nextcloud_v2-nextcloud-app-1 | nextcloud:latest | — | **RESTARTING** |
| nextcloud_v2-nextcloud-db-1 | mariadb:10.6 | 3306 (internal) | Nextcloud database |
| owntracks-recorder | owntracks/recorder | 8083 | Location tracking backend |
| owntracks-frontend | owntracks/frontend | 8001→80 | Location tracking UI |
| portfolio-server | portfolio-strapi-portfolio-server | 1337 | Portfolio CMS (Strapi) |
| portfolio-frontend-portfolio-client-1 | portfolio-frontend-portfolio-client | 4000→3000 | Portfolio frontend |
| portfolio-strapi-portfolio-db-1 | postgres:12.0-alpine | 5432 | Portfolio database |
| samba | dperson/samba | 139, 445 | File sharing, healthy |

### Management

| Container | Image | Ports | Notes |
|-----------|-------|-------|-------|
| portainer | portainer/portainer-ce:sts | 8000, 9443 | Container management UI |

## Unhealthy / Crash-Looping

| Container | Issue | Since |
|-----------|-------|-------|
| nextcloud_v2-nextcloud-app-1 | Restart loop | Ongoing |
| prometheus | Restart loop | Ongoing |
| cloudflare-ddns_cloudflare-ddns_1 | Restart loop | Ongoing |

## Stopped / Stale Containers (removed 2025-04-11)

Previously had 10 stopped containers (minecraft servers, cadvisor, orphaned test builds) — all removed during cleanup.

## Docker Networks

| Network | Driver | Used By |
|---------|--------|---------|
| backend | bridge | General backend |
| mediaserver | bridge | Media stack |
| nginx-proxy-network | bridge | Reverse proxy |
| pihole_default | bridge | Pi-hole |
| frigate_default | bridge | Frigate |
| owntracks_default | bridge | OwnTracks |
| wireguard_default | bridge | WireGuard |
| samba_default | bridge | Samba |
| cloudflare-ddns_default | bridge | DDNS |

## Storage Volumes (named, in use)

Key `local-persist` volumes tied to active stacks:
- `immich_immich-data`, `immich_immich-cache`, `immich_immich-db`
- `mediaserver_*` (jellyfin-config, sonarr-config, radarr-config, deluge-config, jackett-config, prowlarr-config, downloads, movies, music, series)
- `frigate_frigate-config`, `frigate_frigate-data`
- `monitoring_grafana-data`, `monitoring_prometheus-data`
- `nextcloud_v2_nextcloud-app`, `nextcloud_v2_nextcloud-db`
- `nginx-proxy-manager_nginx-data`, `nginx-proxy-manager_nginx-db`, `nginx-proxy-manager_nginx-encryption`
- `owntracks_owntracks-data`
- `pihole_pihole-data`, `pihole_pihole-dns`
- `portfolio-strapi_portfolio-db`, `portfolio-strapi_portfolio-server`
- `portfolio-frontend_portfolio-client` / `portfolio_frontend_portfolio-client`
- `samba_samba-config`, `samba_samba-data`, `samba_samba-users`
- `wireguard_wireguard-config`
- `portainer_data`

Stale named volumes (from decommissioned services, still on disk):
- `gitlab_gitlab-*` — GitLab CE (migrated elsewhere)
- `firefly-iii_*`, `firefly_*` — Firefly III (finance tracker)
- `rocket_chat_*` — Rocket.Chat
- `satisfactory_*` — Satisfactory game server
- `minecraft_*`, `minecraft-fabric_*` — Minecraft servers
- `photprism_*` — PhotoPrism (replaced by Immich)
- `plex_*` — Plex (replaced by Jellyfin)
