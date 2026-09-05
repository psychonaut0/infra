# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Source for the Home Lab dashboard shown at `http://home.lan` (Caddy → container port 3000). The built image runs as the `dashboard` service in `stacks/ct-mgmt/docker-compose.yml` on ct-mgmt (192.168.3.12). It is a Preact SPA with a Bun-based SSR server.

## Runtime architecture

Two-stage. Vite builds a client bundle into `dist/`; Bun serves requests at runtime:

- **`server.js`** (Bun) — reads `dist/index.html` as a template once at startup, renders `App.jsx` via `preact-render-to-string`, and injects the resulting HTML + an `__INITIAL_DATA__` script tag into the template. Also exposes `/api/status` returning the same data as JSON.
- **`src/main.jsx`** — on the client, hydrates if the root has SSR children, otherwise does a plain render. Reads `window.__INITIAL_DATA__` for the first paint.
- **`src/useHealthChecks.js` / `src/useResources.js`** — poll `/api/status` every 60s / 30s after hydration.

Consequence: `bun run dev` (Vite) has **no SSR and no `/api/status`** — it only serves the client bundle, and polling fetches will 404. For end-to-end work you need `vite build && bun server.js` (or run via Docker).

## Two sources of truth for services

Services live in **both** files and must be kept in sync when adding or removing one:

- `server.js` — `services` array. Only needs `name` + `ping` URL. This is what `checkAllHealth()` hits from inside the CT (LAN IPs, not `.lan` names) and caches.
- `src/services.js` — `sections` array with `name`, `desc`, `href`, `ping`, `icon`. The `ping` value is the **join key** — `App.jsx` looks up `health[svc.ping]` to colour the status dot. If the two `ping` strings don't match exactly, the dot stays grey.

Icons live in `public/icons/*.svg` and are served statically from `dist/icons/` after build.

## Proxmox resource stats

`fetchResources()` hits `${PVE_API_URL}/api2/json/cluster/resources` with a `PVEAPIToken=...` header. Token creds come from `.env` (gitignored; see `.env.example`). Without them, `PVE_AUTH` is `null`, `fetchResources` is a no-op, and the `SystemStats` block at the top of the page renders nothing.

Node list is sorted so `proxmoxmain` always renders first. Storage is hard-filtered to `local-lvm` (labelled "SSD") and `cloud` (labelled "Cloud") on `proxmoxmain` only — adding a new pool requires editing `server.js:91-109`.

## Health-check semantics

`checkService()` considers any response with `res.ok || res.status > 0` as "up" (any HTTP response at all, even 401/403, counts — the goal is reachability, not auth). TLS verification is disabled (`rejectUnauthorized: false`) because the Proxmox UI and a few other services use self-signed certs. Timeout 5s per service. Checks run in parallel on a 60s interval.

## Commands

```bash
bun install                # install deps (uses bun.lock)
bun run dev                # Vite dev server — client-only, no SSR / no /api
bun run build              # produce dist/
bun server.js              # run SSR server (requires prior build)
```

Deploy via the parent stack — from the repo root:
```bash
infra deploy dashboard     # rebuilds image on ct-mgmt and restarts
```
The Dockerfile is a two-stage build: `bun run build` to produce `dist/`, then a production-deps-only runtime stage that runs `bun server.js` on port 3000.

## Styling

Tailwind with a custom "neu" (neumorphic) palette + shadows in `tailwind.config.js`. Common classes: `bg-neu-bg`, `shadow-neu-raised`, `rounded-neu`. `Bar` in `App.jsx` switches colour at 70% / 90% thresholds.
