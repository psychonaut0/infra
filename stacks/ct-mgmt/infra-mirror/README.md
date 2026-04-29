# infra-mirror

Pulls the latest GitHub Release of `psychonaut0/infra` (CLI tags `v*`) and
re-publishes the binaries to a LAN URL behind ct-mgmt's Caddy. Hosts use
`https://infra-bin.lan/manifest.json` to detect new versions and
`https://infra-bin.lan/linux/<arch>/infra` to download.

## Architecture

```
GH Actions release.yml  →  GH Release (private)
                                │  (PAT-auth poll)
                                ▼
                         ct-mgmt: sync.sh
                          (systemd timer, 5 min)
                                │
                                ▼
                         /var/www/infra-bin/  ←─ bind-mounted into caddy CT as /srv/infra-bin
                                │
                                ▼
                         Caddy → http://infra-bin.lan
```

## First-time deploy on ct-mgmt

These steps are manual and run once on a fresh host. The repo is the source
of truth for `sync.sh`, `install.sh`, and the units.

````sh
# 1. From your workstation, copy the artifacts to ct-mgmt.
scp stacks/ct-mgmt/infra-mirror/sync.sh         ct-mgmt:/opt/infra-mirror/sync.sh
scp stacks/ct-mgmt/infra-mirror/install.sh      ct-mgmt:/opt/infra-mirror/install.sh
scp stacks/ct-mgmt/infra-mirror/infra-mirror.service ct-mgmt:/etc/systemd/system/
scp stacks/ct-mgmt/infra-mirror/infra-mirror.timer   ct-mgmt:/etc/systemd/system/

# 2. SSH to ct-mgmt and finalize.
ssh ct-mgmt
mkdir -p /opt/infra-mirror /etc/infra-mirror /var/www/infra-bin
chmod 0755 /opt/infra-mirror/sync.sh /opt/infra-mirror/install.sh
apt-get install -y jq curl   # if not already present

# Drop the fine-grained PAT (Contents:Read, Metadata:Read on psychonaut0/infra).
install -m 0600 /dev/stdin /etc/infra-mirror/token <<<'<paste-PAT-here>'

systemctl daemon-reload
systemctl enable --now infra-mirror.timer
systemctl start infra-mirror.service     # one-shot first sync
journalctl -u infra-mirror -n 50         # confirm "published vX.Y.Z"
````

## Caddy bind-mount

`stacks/ct-mgmt/docker-compose.yml` bind-mounts `/var/www/infra-bin` into the
caddy container as `/srv/infra-bin:ro`. The Caddyfile site block:

```caddy
http://infra-bin.lan {
    root * /srv/infra-bin
    file_server
    encode gzip
}
```

After the first deploy, `cd /opt/stacks/ct-mgmt && docker compose up -d caddy`
on ct-mgmt to apply the new mount and config.

## DNS

Add a Pi-hole local record on ct-dns: `infra-bin.lan → 192.168.3.12`
(Settings → Local DNS → DNS Records).

## Refreshing on changes

When `sync.sh` or `install.sh` is updated in the repo, rerun the matching
`scp` line and `systemctl start infra-mirror.service` to pick it up. The
units rarely change; if they do, also run `systemctl daemon-reload`.

## Verifying

From any LAN host:

```sh
curl -s https://infra-bin.lan/manifest.json | jq
```

A fresh CT can bootstrap with:

```sh
curl -fsSL https://infra-bin.lan/install.sh | sh
```

## Troubleshooting

- `journalctl -u infra-mirror -f` — live log of the sync runs.
- `systemctl list-timers infra-mirror.timer` — next/last fire times.
- 401 from GH API → PAT invalid or scope insufficient. Regenerate at
  github.com/settings/tokens, replace `/etc/infra-mirror/token`.
- Caddy 404 → check the bind mount is mounted (`docker exec caddy ls /srv/infra-bin`)
  and the Caddyfile site block exists.
