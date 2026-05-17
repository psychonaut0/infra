# Public Portfolio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up `portfolio.<PERSONAL_DOMAIN>` as a public Next.js site, built from a brand-new dedicated repo (`portfolio`) that publishes a container image to GHCR, and deployed in a new dedicated LXC (`ct-portfolio`, VMID 113) on proxmoxmain, served publicly through the existing Cloudflare Tunnel and on the LAN via Caddy.

**Architecture:** Two independent repos, joined only by an image tag (no submodules).
- **`portfolio` repo** (new, at `~/Documents/personal/projects/portfolio/`, pushed to `github.com/psychonaut0/portfolio`): Next.js 15 App Router + TypeScript scaffold, multi-stage Dockerfile producing a `node:22-alpine` standalone image, GitHub Actions workflow that publishes `ghcr.io/psychonaut0/portfolio:{latest,sha-<short>,v<semver>}` on push to main / tags. Public package — no pull credentials required on the deploy target.
- **`infra` repo** (existing, this repo): new `stacks/ct-portfolio/` Compose stack that pulls the GHCR image; new `ct-portfolio` LXC on proxmoxmain at 192.168.3.16 following the existing unprivileged-Docker-LXC pattern; LAN access via `infra dns add portfolio.lan`; public access via a new Public Hostname on the existing remote-managed Cloudflare Tunnel running on `ct-tunnel`.
- **Deploy loop:** push to `portfolio` main → CI builds & pushes image → `infra deploy ct-portfolio` (or `docker compose pull && up -d` on ct-portfolio) brings the new version up. Rollback = pin a `sha-<short>` tag in the compose file.

**Tech Stack:** Next.js 15 (App Router, TypeScript, `output: 'standalone'`), pnpm via Corepack, Node 22 Alpine runtime, Docker Engine CE + Compose v2, GitHub Container Registry (GHCR), GitHub Actions (`docker/build-push-action@v6` with buildx + gha cache), Proxmox LXC (unprivileged Debian 13), existing `infra` CLI for DNS/deploy, existing Caddy on ct-mgmt, existing Cloudflare Tunnel on ct-tunnel, existing Gatus on ct-mgmt.

**Git identity for commits (infra repo):** Match existing repo history — `psy <psychonaut0@users.noreply.github.com>`. If `git commit` fails with "Author identity unknown", prepend the commit with:
```bash
GIT_AUTHOR_NAME=psy GIT_AUTHOR_EMAIL=psychonaut0@users.noreply.github.com \
GIT_COMMITTER_NAME=psy GIT_COMMITTER_EMAIL=psychonaut0@users.noreply.github.com \
git commit -m "..."
```

**Domain note:** `<PERSONAL_DOMAIN>` is the placeholder used in the public-facing `CLAUDE.md`. The real value is in `CLAUDE.local.md` (gitignored). Substitute mentally where required.

---

## Task 1: Pre-flight checks

**Goal:** Verify every precondition before touching anything. If any check fails, stop and resolve before continuing.

**Files:** None (verification only)

- [ ] **Step 1: RAM headroom on proxmoxmain**

Run from blvckmain:
```bash
ssh proxmoxmain 'free -h'
```
Expected: `available` column ≥ **2 GB** (CT uses 1 GB; leave headroom).

- [ ] **Step 2: local-lvm free space**

```bash
ssh proxmoxmain 'pvesm status'
```
Expected: row `local-lvm` shows `Avail` ≥ **10 GB** (CT rootfs is 8 GB).

- [ ] **Step 3: VMID 113 unused**

```bash
ssh proxmoxmain 'pct list | awk "{print \$1}" | grep -w 113 || echo free'
```
Expected: outputs `free`. If it outputs `113`, stop — VMID clash.

- [ ] **Step 4: IP 192.168.3.16 unused**

```bash
ping -c1 -W1 192.168.3.16 || echo "no-reply"
```
Expected: `no-reply`.

- [ ] **Step 5: Debian 13 LXC template present**

```bash
ssh proxmoxmain 'pveam list local | grep debian-13-standard'
```
Expected: at least one line, e.g. `local:vztmpl/debian-13-standard_13.0-1_amd64.tar.zst`. Record the exact filename — Task 5 needs it. If absent, download with `ssh proxmoxmain 'pveam update && pveam download local debian-13-standard_13.0-1_amd64.tar.zst'` (substitute the latest version from `pveam available | grep debian-13-standard`).

- [ ] **Step 6: Cloudflare Tunnel reachable + remote-managed**

```bash
ssh ct-tunnel 'docker ps --format "{{.Names}}\t{{.Status}}" | grep cloudflared'
```
Expected: `cloudflared    Up <duration>`. The tunnel is remote-managed (TUNNEL_TOKEN); ingress rules are configured in the Cloudflare Zero Trust dashboard (Task 11), not in a local config file.

- [ ] **Step 7: GitHub CLI authenticated as psychonaut0**

```bash
gh auth status
```
Expected: logged in as `psychonaut0` with `repo` and `workflow` scopes. If `read:packages`/`write:packages` is missing, `gh auth refresh -s write:packages,read:packages`.

- [ ] **Step 8: Local Docker available on blvckmain**

```bash
docker --version && docker buildx version
```
Expected: Docker CE ≥ 26, buildx present. Required for Task 3 local image test.

- [ ] **Step 9: pnpm/corepack available on blvckmain**

```bash
corepack --version && node --version
```
Expected: corepack ≥ 0.30, Node ≥ 20. Corepack ships with Node; pnpm is shimmed via Corepack so no global install is needed.

---

## Task 2: Scaffold the `portfolio` repo

**Goal:** Create a fresh Next.js 15 project, set it up for standalone output, replace the boilerplate page with a minimal placeholder, and commit. No Docker yet — that's Task 3.

**Files:**
- Create: `~/Documents/personal/projects/portfolio/` (whole tree)
- Modify: `~/Documents/personal/projects/portfolio/next.config.ts`
- Modify: `~/Documents/personal/projects/portfolio/app/page.tsx`

- [ ] **Step 1: Scaffold the app**

```bash
cd ~/Documents/personal/projects
pnpm create next-app@latest portfolio \
  --typescript --eslint --tailwind --app \
  --src-dir false --import-alias "@/*" --use-pnpm \
  --turbopack false
```
Expected: a `portfolio/` directory with `app/`, `package.json`, `next.config.ts`, `tsconfig.json`, no errors. If `--turbopack false` is rejected by a newer scaffold, drop the flag — Turbopack is fine in dev.

- [ ] **Step 2: Enable standalone output**

Edit `~/Documents/personal/projects/portfolio/next.config.ts` to:
```typescript
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
};

export default nextConfig;
```

- [ ] **Step 3: Replace the scaffold homepage with a minimal placeholder**

Overwrite `~/Documents/personal/projects/portfolio/app/page.tsx` with:
```tsx
export default function Home() {
  return (
    <main className="min-h-screen flex items-center justify-center p-8">
      <div className="text-center">
        <h1 className="text-4xl font-semibold tracking-tight">Francesco Barbano</h1>
        <p className="mt-4 text-neutral-500">Portfolio — under construction.</p>
      </div>
    </main>
  );
}
```

- [ ] **Step 4: Local sanity check**

```bash
cd ~/Documents/personal/projects/portfolio
pnpm install
pnpm build
```
Expected: `pnpm build` completes with `Generating static pages` and `.next/standalone/` is produced. If `.next/standalone/` is missing, recheck Step 2.

- [ ] **Step 5: Initialize git and make the initial commit**

```bash
cd ~/Documents/personal/projects/portfolio
git init -b main
git add -A
git commit -m "feat: scaffold Next.js 15 portfolio with standalone output"
```
Expected: a single commit on `main`. If author identity is unknown, set with `git config user.name "psy"` and `git config user.email "psychonaut0@users.noreply.github.com"`.

---

## Task 3: Dockerize the portfolio

**Goal:** Add a multi-stage Dockerfile that produces a small Node 22 Alpine runtime image, plus `.dockerignore` and a `compose.yml` for local testing. Verify image builds and the container serves 200 OK on `/`.

**Files:**
- Create: `~/Documents/personal/projects/portfolio/Dockerfile`
- Create: `~/Documents/personal/projects/portfolio/.dockerignore`
- Create: `~/Documents/personal/projects/portfolio/compose.yml`

- [ ] **Step 1: Write the Dockerfile**

Create `~/Documents/personal/projects/portfolio/Dockerfile`:
```dockerfile
# syntax=docker/dockerfile:1.7

FROM node:22-alpine AS deps
WORKDIR /app
RUN apk add --no-cache libc6-compat
COPY package.json pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile

FROM node:22-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN corepack enable && pnpm build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
RUN addgroup --system --gid 1001 nodejs \
 && adduser --system --uid 1001 nextjs
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
```

- [ ] **Step 2: Write `.dockerignore`**

Create `~/Documents/personal/projects/portfolio/.dockerignore`:
```
node_modules
.next
.git
.gitignore
Dockerfile
compose.yml
.dockerignore
.env*.local
npm-debug.log*
.DS_Store
README.md
```

- [ ] **Step 3: Write a local `compose.yml`**

Create `~/Documents/personal/projects/portfolio/compose.yml`:
```yaml
services:
  portfolio:
    build: .
    image: ghcr.io/psychonaut0/portfolio:dev
    container_name: portfolio
    restart: unless-stopped
    ports:
      - "3000:3000"
    environment:
      NODE_ENV: production
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:3000/"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
```

- [ ] **Step 4: Build and run locally to verify**

```bash
cd ~/Documents/personal/projects/portfolio
docker build -t ghcr.io/psychonaut0/portfolio:dev .
docker run --rm -d --name portfolio-test -p 3000:3000 ghcr.io/psychonaut0/portfolio:dev
sleep 5
curl -fsS -o /dev/null -w "%{http_code}\n" http://localhost:3000/
docker rm -f portfolio-test
```
Expected: `200`. If non-200, `docker logs portfolio-test` to debug before continuing.

- [ ] **Step 5: Commit**

```bash
cd ~/Documents/personal/projects/portfolio
git add Dockerfile .dockerignore compose.yml
git commit -m "feat: multi-stage Dockerfile + compose for Next.js standalone"
```

---

## Task 4: GitHub Actions — build & push to GHCR

**Goal:** Add a CI workflow that builds the image on every push to `main` and on tags `v*`, and publishes to `ghcr.io/psychonaut0/portfolio` with tags `latest` (main only), `sha-<short>`, `v<semver>` (tags).

**Files:**
- Create: `~/Documents/personal/projects/portfolio/.github/workflows/build.yml`
- Create: `~/Documents/personal/projects/portfolio/README.md`

- [ ] **Step 1: Write the workflow**

Create `~/Documents/personal/projects/portfolio/.github/workflows/build.yml`:
```yaml
name: build-and-push

on:
  push:
    branches: [main]
    tags: ['v*']
  pull_request:
    branches: [main]
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4

      - uses: docker/setup-buildx-action@v3

      - name: Login to GHCR
        if: github.event_name != 'pull_request'
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Image metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/psychonaut0/portfolio
          tags: |
            type=ref,event=branch
            type=sha,prefix=sha-,format=short
            type=raw,value=latest,enable={{is_default_branch}}
            type=semver,pattern={{version}}

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          push: ${{ github.event_name != 'pull_request' }}
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- [ ] **Step 2: Add a minimal README**

Create `~/Documents/personal/projects/portfolio/README.md`:
```markdown
# portfolio

Personal portfolio site — Next.js 15 (App Router, standalone output).

Deployed to `portfolio.<PERSONAL_DOMAIN>` from `ghcr.io/psychonaut0/portfolio:latest`
on `ct-portfolio` in the home-lab fleet (see the `infra` repo).

## Local dev

```bash
pnpm install
pnpm dev
```

## Build the container

```bash
docker compose build
docker compose up -d
curl -fsS http://localhost:3000/
```

## CI / release

Every push to `main` builds and publishes `:latest` + `:sha-<short>`.
Tags `vX.Y.Z` publish `:vX.Y.Z`. Deploy is pinned by the image tag in the
infra repo's `stacks/ct-portfolio/docker-compose.yml`.
```

- [ ] **Step 3: Commit**

```bash
cd ~/Documents/personal/projects/portfolio
git add .github/workflows/build.yml README.md
git commit -m "ci: build & push to GHCR on main + tags"
```

---

## Task 5: Create the GitHub repo + push + verify image

**Goal:** Create the public GitHub repo, push `main`, watch the workflow succeed, mark the resulting package public so the LAN deploy doesn't need a pull token.

**Files:** None (remote operations).

- [ ] **Step 1: Create the GitHub repo**

```bash
cd ~/Documents/personal/projects/portfolio
gh repo create psychonaut0/portfolio --public --source=. --remote=origin --description "Personal portfolio (Next.js)"
```
Expected: prints `https://github.com/psychonaut0/portfolio`. If the repo already exists, `gh repo edit psychonaut0/portfolio --visibility public` then `git remote add origin git@github.com:psychonaut0/portfolio.git`.

- [ ] **Step 2: Push main**

```bash
git push -u origin main
```

- [ ] **Step 3: Watch the workflow**

```bash
gh run watch
```
Expected: `build-and-push` succeeds within ~3–5 min. If it fails, `gh run view --log-failed` and fix before continuing.

- [ ] **Step 4: Verify image lands in GHCR**

```bash
gh api -X GET /users/psychonaut0/packages/container/portfolio/versions \
  --jq '.[0:5] | .[] | {created: .created_at, tags: .metadata.container.tags}'
```
Expected: at least one version with `tags` containing `latest` and `sha-<short>`.

- [ ] **Step 5: Mark the package public (required for unauthenticated pull from ct-portfolio)**

Web UI: <https://github.com/users/psychonaut0/packages/container/portfolio/settings> → "Change package visibility" → **Public** → confirm.

Verify from blvckmain (no auth):
```bash
docker logout ghcr.io 2>/dev/null || true
docker pull ghcr.io/psychonaut0/portfolio:latest
```
Expected: pull succeeds without credentials. If it asks for auth, the package is still private — re-check the settings page.

---

## Task 6: Provision `ct-portfolio` (VMID 113) on proxmoxmain

**Goal:** Create the unprivileged Debian 13 LXC at 192.168.3.16, apply the Docker-in-LXC config (AppArmor unconfined + proc/sys rw + nesting+keyctl), start it, and confirm SSH works via the existing keys.

**Files:**
- Modify: `/etc/pve/lxc/113.conf` on proxmoxmain (Docker compatibility append).

- [ ] **Step 1: Create the CT**

Use the template filename recorded in Task 1 Step 5. Example:
```bash
ssh proxmoxmain "pct create 113 local:vztmpl/debian-13-standard_13.0-1_amd64.tar.zst \
  --hostname ct-portfolio \
  --description 'ct-portfolio — Next.js portfolio (public via Cloudflare Tunnel)' \
  --cores 1 \
  --memory 1024 \
  --swap 256 \
  --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,firewall=1,gw=192.168.3.1,ip=192.168.3.16/24,type=veth \
  --nameserver 192.168.3.5 \
  --ostype debian \
  --features nesting=1,keyctl=1 \
  --unprivileged 1 \
  --onboot 1 \
  --ssh-public-keys /root/.ssh/authorized_keys \
  --start 0"
```
Expected: `Logical volume "vm-113-disk-0" created.` and no errors.

- [ ] **Step 2: Append Docker-in-LXC compatibility to the CT config**

```bash
ssh proxmoxmain "cat >> /etc/pve/lxc/113.conf <<'EOF'
lxc.mount.auto: proc:rw sys:rw
lxc.apparmor.profile: unconfined
EOF"
ssh proxmoxmain "pct config 113"
```
Expected: `pct config 113` output includes `lxc.mount.auto: proc:rw sys:rw` and `lxc.apparmor.profile: unconfined`.

- [ ] **Step 3: Start the CT**

```bash
ssh proxmoxmain 'pct start 113'
sleep 5
ssh proxmoxmain 'pct status 113'
```
Expected: `status: running`.

- [ ] **Step 4: Verify network + SSH**

```bash
ping -c2 -W1 192.168.3.16
ssh -o StrictHostKeyChecking=accept-new ct-portfolio 'hostname && ip -4 addr show eth0 | grep inet'
```
Expected: ping succeeds; SSH prints `ct-portfolio` and `inet 192.168.3.16/24`. If SSH fails, check `pct exec 113 -- cat /root/.ssh/authorized_keys` on proxmoxmain.

---

## Task 7: Wire `ct-portfolio` into the infra repo

**Goal:** Add the CT to the fleet inventory (`stacks/hosts.yaml`) so the `infra` CLI can reach it, and create the Compose stack pointing at the GHCR image. No deploy yet.

**Files:**
- Modify: `/home/psy/Documents/personal/infra/stacks/hosts.yaml`
- Create: `/home/psy/Documents/personal/infra/stacks/ct-portfolio/docker-compose.yml`

- [ ] **Step 1: Add ct-portfolio to hosts.yaml**

Append to `/home/psy/Documents/personal/infra/stacks/hosts.yaml`, keeping alphabetical-by-IP order (after ct-tools at 192.168.3.15):
```yaml
ct-portfolio:
  ip: 192.168.3.16
```

- [ ] **Step 2: Write the stack compose file**

Create `/home/psy/Documents/personal/infra/stacks/ct-portfolio/docker-compose.yml`:
```yaml
services:
  portfolio:
    image: ghcr.io/psychonaut0/portfolio:latest
    container_name: portfolio
    restart: unless-stopped
    ports:
      - "3000:3000"
    environment:
      NODE_ENV: production
      TZ: "Europe/Rome"
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:3000/"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s

  portainer-agent:
    image: portainer/agent:latest
    container_name: portainer-agent
    restart: unless-stopped
    ports:
      - "9001:9001"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /var/lib/docker/volumes:/var/lib/docker/volumes
```

- [ ] **Step 3: Rebuild the `infra` CLI so the embedded host snapshot picks up ct-portfolio**

```bash
cd /home/psy/Documents/personal/infra/cli
make install
infra ls | grep -i portfolio || echo "expected once Task 8 deploys"
infra ct status 2>&1 | grep -i portfolio || true
```
Expected: `make install` succeeds; the host appears in CLI views once it's reachable. (Service won't show in `infra ls` until the stack is deployed in Task 8.)

- [ ] **Step 4: Commit (infra repo)**

```bash
cd /home/psy/Documents/personal/infra
git add stacks/hosts.yaml stacks/ct-portfolio/docker-compose.yml
git commit -m "ct-portfolio: add fleet entry + Compose stack pulling GHCR image"
```

---

## Task 8: Bootstrap ct-portfolio (Docker install + first deploy)

**Goal:** Run the standard `scripts/bootstrap-ct.sh` against ct-portfolio. It installs Docker, applies `docker-daemon.json`, copies the stack into `/opt/stacks/ct-portfolio/`, and runs `docker compose up -d`. Verify the container is healthy and serves the page on the LAN.

**Files:** None local (operations on proxmoxmain + ct-portfolio).

- [ ] **Step 1: Sync the infra repo to proxmoxmain (the bootstrap script reads from `$INFRA_REPO`, default `/root/infra`)**

```bash
ssh proxmoxmain 'test -d /root/infra && (cd /root/infra && git pull) || git clone <REPO_URL> /root/infra'
ssh proxmoxmain 'cd /root/infra && git log -1 --oneline'
```
Replace `<REPO_URL>` with whatever proxmoxmain's existing clone uses. Expected: the last commit matches what was just pushed locally (the one from Task 7 Step 4). If the infra repo isn't pushed yet, do `git push` from blvckmain first or use `rsync -a --delete /home/psy/Documents/personal/infra/ proxmoxmain:/root/infra/` as a fallback.

- [ ] **Step 2: Run bootstrap-ct.sh**

```bash
ssh proxmoxmain '/root/infra/scripts/bootstrap-ct.sh ct-portfolio'
```
Expected: script runs through "installing Docker engine", "applying /etc/docker/daemon.json", "copying stack files", "docker compose up -d", and exits 0. The `pnpm/Next.js` image is pulled fresh from GHCR.

- [ ] **Step 3: Verify the container is up and healthy**

```bash
ssh ct-portfolio 'docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Image}}"'
```
Expected:
```
NAMES              STATUS                   IMAGE
portfolio          Up X (healthy)           ghcr.io/psychonaut0/portfolio:latest
portainer-agent    Up X                     portainer/agent:latest
```
If `unhealthy` or `Restarting`, run `ssh ct-portfolio 'docker logs portfolio --tail 50'` and resolve before proceeding.

- [ ] **Step 4: Curl the container directly from blvckmain**

```bash
curl -fsS -o /dev/null -w "%{http_code}\n" http://192.168.3.16:3000/
```
Expected: `200`.

---

## Task 9: Add `portfolio.lan` LAN access via Caddy + Pi-hole

**Goal:** Use the existing `infra dns add` workflow to expose `http://portfolio.lan` on the LAN through Caddy on ct-mgmt, with a matching Pi-hole DNS record. This is for internal browsing/testing and is independent of the public exposure path.

**Files:** Modified by `infra dns add`:
- Modify: `stacks/ct-mgmt/Caddyfile` (one new `http://portfolio.lan { ... }` block appended)
- Modify: live `02-infra-dns.conf` inside the pihole container on ct-dns

- [ ] **Step 1: Add the LAN hostname**

```bash
cd /home/psy/Documents/personal/infra
infra dns add portfolio.lan http://192.168.3.16:3000
```
Expected: command appends the Caddy block, scp's the Caddyfile to ct-mgmt, recreates Caddy, writes the dnsmasq record, reloads Pi-hole, and prints `Added portfolio.lan (http) → 192.168.3.16:3000; DNS → 192.168.3.12`.

- [ ] **Step 2: Verify resolution**

```bash
dig +short @192.168.3.5 portfolio.lan
```
Expected: `192.168.3.12`.

- [ ] **Step 3: Verify Caddy serves the page**

```bash
curl -fsS -H "Host: portfolio.lan" -o /dev/null -w "%{http_code}\n" http://192.168.3.12/
curl -fsS -o /dev/null -w "%{http_code}\n" http://portfolio.lan/
```
Expected: both `200`. The second only works on hosts using Pi-hole DNS (which blvckmain does).

- [ ] **Step 4: Verify there's no drift**

```bash
infra dns ls | grep portfolio
```
Expected: row `portfolio.lan   http   192.168.3.16:3000   192.168.3.12   ok`.

- [ ] **Step 5: Commit the Caddyfile change**

```bash
cd /home/psy/Documents/personal/infra
git add stacks/ct-mgmt/Caddyfile
git commit -m "ct-mgmt: add portfolio.lan reverse proxy"
```

---

## Task 10: Add Gatus health checks for portfolio

**Goal:** Add monitoring entries for the LAN-direct endpoint (catches container/CT problems) and the public endpoint (catches tunnel/CF problems). Background tier — no Telegram page-out for short blips.

**Files:**
- Modify: `/home/psy/Documents/personal/infra/stacks/ct-mgmt/gatus/config.yaml`

- [ ] **Step 1: Add the two endpoints in the appropriate tier**

Edit `stacks/ct-mgmt/gatus/config.yaml`. In the existing background (tier 3) section, append:
```yaml
  - name: Portfolio (LAN)
    group: background
    url: "http://192.168.3.16:3000/"
    interval: 60s
    conditions:
      - "[CONNECTED] == true"
      - "[STATUS] == 200"
    alerts:
      - type: telegram
        failure-threshold: 5
        success-threshold: 2
        send-on-resolved: true

  - name: Portfolio (public)
    group: background
    url: "https://portfolio.<PERSONAL_DOMAIN>/"
    interval: 5m
    conditions:
      - "[CONNECTED] == true"
      - "[STATUS] == 200"
      - "[CERTIFICATE_EXPIRATION] > 168h"
    alerts:
      - type: telegram
        failure-threshold: 3
        success-threshold: 2
        send-on-resolved: true
```
(If `config.yaml` doesn't have a background tier yet, place these under a `# --- Tier 3: Background ---` comment at the end. Substitute `<PERSONAL_DOMAIN>` with the value from `CLAUDE.local.md`.)

The public check will start failing **after** Task 11 lands the tunnel ingress — that's intentional; the Telegram alert threshold (3 failures × 5m = 15 min) gives ample bake-in time.

- [ ] **Step 2: Deploy the Gatus config**

```bash
cd /home/psy/Documents/personal/infra
infra deploy gatus
```
Expected: scp's config.yaml to ct-mgmt and recreates the Gatus container. `infra status --ct ct-mgmt` shows gatus `Up`.

- [ ] **Step 3: Verify in the Gatus UI**

Visit <http://status.lan/> → confirm two new rows appear under background. "Portfolio (LAN)" should be green within 60 s. "Portfolio (public)" will be red until Task 11 — that's expected.

- [ ] **Step 4: Commit**

```bash
cd /home/psy/Documents/personal/infra
git add stacks/ct-mgmt/gatus/config.yaml
git commit -m "gatus: monitor portfolio LAN + public endpoints"
```

---

## Task 11: Expose `portfolio.<PERSONAL_DOMAIN>` via Cloudflare Tunnel

**Goal:** Add a Public Hostname to the existing remote-managed Cloudflare Tunnel that routes `portfolio.<PERSONAL_DOMAIN>` → `http://192.168.3.16:3000`. CF auto-creates the DNS CNAME (`portfolio → <tunnel-id>.cfargotunnel.com`).

**Files:** None local — config lives in the Cloudflare Zero Trust dashboard (remote-managed tunnel).

- [ ] **Step 1: Open the tunnel in the CF Zero Trust dashboard**

Visit <https://one.dash.cloudflare.com/> → **Networks** → **Tunnels** → click the tunnel that ct-tunnel runs (the one whose `TUNNEL_TOKEN` is in `ct-tunnel:/opt/stacks/ct-tunnel/.env`).

- [ ] **Step 2: Add a Public Hostname**

In the tunnel's **Public Hostname** tab → **Add a public hostname**:
- **Subdomain:** `portfolio`
- **Domain:** `<PERSONAL_DOMAIN>` (i.e. `ncsp.dev`)
- **Path:** *(empty)*
- **Service:** **Type** = `HTTP`, **URL** = `192.168.3.16:3000`
- **Additional application settings → HTTP Settings → HTTP Host Header:** leave empty (Next.js doesn't care; the standalone server binds 0.0.0.0:3000).
- **TLS settings:** leave defaults.

Save. Cloudflare auto-creates the DNS CNAME `portfolio.<PERSONAL_DOMAIN>` → `<tunnel-id>.cfargotunnel.com` (proxied/orange-cloud — that's correct here; the public path is HTTPS).

- [ ] **Step 3: Verify the DNS CNAME exists**

```bash
dig +short portfolio.<PERSONAL_DOMAIN> CNAME
```
Expected: a single `<tunnel-id>.cfargotunnel.com.` line. If empty, wait 30 s and retry; if still empty, the hostname didn't save — repeat Step 2.

- [ ] **Step 4: Verify the public site responds**

```bash
curl -fsS -o /dev/null -w "HTTP %{http_code} | TLS %{ssl_verify_result}\n" https://portfolio.<PERSONAL_DOMAIN>/
curl -fsS https://portfolio.<PERSONAL_DOMAIN>/ | grep -i "Francesco Barbano"
```
Expected: `HTTP 200 | TLS 0`; grep matches.

- [ ] **Step 5: Verify Gatus public check turns green**

Visit <http://status.lan/> → "Portfolio (public)" should turn green within ~5 minutes (one polling interval after the hostname goes live).

---

## Task 12: Document in CLAUDE.md and finalize

**Goal:** Update the infra repo's `CLAUDE.md` so the fleet docs match reality, list the new public endpoint in the "Upstream / External Access" section, add the CT entry, add the service line, and commit. No code changes.

**Files:**
- Modify: `/home/psy/Documents/personal/infra/CLAUDE.md`

- [ ] **Step 1: Add ct-portfolio entry under Network & Devices**

After the existing `### ct-tools` block in `CLAUDE.md`, insert:
```markdown
### ct-portfolio (LXC — VMID 113 on proxmoxmain)
- **IP:** 192.168.3.16
- **User:** root
- **OS:** Debian 13 (Trixie), unprivileged LXC
- **SSH:** Port 22, key-based auth (no password)
- **Resources:** 1 vCPU, 1024MB RAM, 256MB swap, 8GB disk
- **Role:** Public-facing portfolio site at `portfolio.<PERSONAL_DOMAIN>`. Runs a Next.js 15 standalone container built from the separate `psychonaut0/portfolio` GitHub repo, distributed via `ghcr.io/psychonaut0/portfolio`.
- **Stack:** `/opt/stacks/ct-portfolio/docker-compose.yml` (local copy: `stacks/ct-portfolio/`). The application source lives in a **separate** repo (`psychonaut0/portfolio`); this repo only owns the deploy descriptor. Roll forward = bump the image tag in the compose file (or just `docker compose pull && up -d` for `:latest`); roll back = pin a `:sha-<short>` tag.
- **Ports:** 3000 (Next.js HTTP, also LAN entry via Caddy at http://portfolio.lan and public entry via Cloudflare Tunnel)
- **Config notes:** AppArmor unconfined + proc/sys rw mount for Docker compatibility. Stateless container — no persistent volumes. Public exposure handled entirely by the existing cloudflared tunnel on ct-tunnel (no port-forward, no UniFi changes).
```

- [ ] **Step 2: Add ct-portfolio row to the SSH topology block**

In the `## Network Layout` section, add to the `blvckmain` block (keep IP-ascending order, between ct-tools and ct-backup):
```markdown
  ├── ssh ct-portfolio   → 192.168.3.16:22   (root, key auth)
```

- [ ] **Step 3: Add the public endpoint to the "Public endpoints" list**

In the `## Upstream / External Access` section, under "Public endpoints currently exposed via UniFi port-forward" — actually no: portfolio uses **Cloudflare Tunnel**, not UniFi PF. Add a new bullet (or new sub-list) immediately below the UniFi PF list:
```markdown
- **Public endpoints currently exposed via Cloudflare Tunnel (ct-tunnel):**
  - `portfolio.<PERSONAL_DOMAIN>` (HTTPS) → `192.168.3.16:3000` (portfolio on ct-portfolio)
```

- [ ] **Step 4: Add the service line under `## Services`**

In the `## Services` section (alphabetical), insert near the others:
```markdown
Portfolio site runs on ct-portfolio (https://portfolio.<PERSONAL_DOMAIN> publicly, http://portfolio.lan on LAN). Stateless Next.js 15 standalone container pulled from `ghcr.io/psychonaut0/portfolio:latest`. Source repo: `github.com/psychonaut0/portfolio` (separate from infra).
```

- [ ] **Step 5: Commit**

```bash
cd /home/psy/Documents/personal/infra
git add CLAUDE.md
git commit -m "CLAUDE.md: document ct-portfolio + public portfolio endpoint"
```

- [ ] **Step 6: Final end-to-end verification**

Run this consolidated check from blvckmain:
```bash
echo "=== LAN (direct) ===" && curl -fsS -o /dev/null -w "%{http_code}\n" http://192.168.3.16:3000/
echo "=== LAN (Caddy)  ===" && curl -fsS -o /dev/null -w "%{http_code}\n" http://portfolio.lan/
echo "=== Public       ===" && curl -fsS -o /dev/null -w "%{http_code}\n" https://portfolio.<PERSONAL_DOMAIN>/
echo "=== Health       ===" && ssh ct-portfolio 'docker inspect --format "{{.State.Health.Status}}" portfolio'
echo "=== Image tag    ===" && ssh ct-portfolio 'docker inspect --format "{{.Config.Image}}" portfolio'
```
Expected output:
```
=== LAN (direct) === 200
=== LAN (Caddy)  === 200
=== Public       === 200
=== Health       === healthy
=== Image tag    === ghcr.io/psychonaut0/portfolio:latest
```

---

## Done criteria (recap)

1. `github.com/psychonaut0/portfolio` exists, is public, has a passing `build-and-push` workflow on `main`.
2. `ghcr.io/psychonaut0/portfolio:latest` is publicly pullable without auth.
3. `ct-portfolio` (VMID 113, 192.168.3.16) is running on proxmoxmain, healthy, and serves Next.js on port 3000.
4. `http://portfolio.lan/` resolves via Pi-hole and is proxied by Caddy to the container.
5. `https://portfolio.<PERSONAL_DOMAIN>/` returns 200 through the existing Cloudflare Tunnel.
6. Both Gatus checks (LAN + public) are green on `http://status.lan/`.
7. `infra` repo `CLAUDE.md` reflects the new CT, service, and public endpoint.
8. Rolling out a new portfolio version is: push to `portfolio` main → CI builds → `ssh ct-portfolio 'cd /opt/stacks/ct-portfolio && docker compose pull && docker compose up -d'` (or `infra deploy ct-portfolio` once it picks up the new stack — see Task 7 Step 3).
