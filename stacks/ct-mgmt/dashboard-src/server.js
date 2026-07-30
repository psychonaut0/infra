import { join } from "path";
import { h } from "preact";
import { renderToString } from "preact-render-to-string";
import App from "./src/App.jsx";

const ROOT = import.meta.dir;
const DIST = join(ROOT, "dist");

// Read built index.html as template once at startup
const indexTemplate = await Bun.file(join(DIST, "index.html")).text();

// --- Service health checks ---

const services = [
  { name: "Home Assistant", ping: "http://192.168.3.10:8123" },
  { name: "Jellyfin", ping: "http://192.168.3.8:8096" },
  { name: "Immich", ping: "http://192.168.3.9:2283" },
  { name: "Workout", ping: "http://192.168.3.17:8080" },
  { name: "Open WebUI", ping: "http://192.168.3.18:8080/health" },
  { name: "Portainer", ping: "http://192.168.3.12:9000" },
  { name: "Proxmox", ping: "https://192.168.3.2:8006" },
  { name: "Frigate", ping: "http://192.168.3.7:5000" },
  { name: "Pi-hole", ping: "http://192.168.3.5" },
  { name: "Proxmox Node", ping: "https://192.168.3.3:8006" },
  { name: "FileBrowser", ping: "http://192.168.3.11:8080" },
  { name: "Gatus", ping: "http://192.168.3.12:8080" },
  { name: "ESPHome", ping: "http://192.168.3.15:6052" },
  { name: "Backup", ping: "http://192.168.3.13" },
  { name: "Sonarr", ping: "http://192.168.3.8:8989" },
  { name: "Radarr", ping: "http://192.168.3.8:7878" },
  { name: "Deluge", ping: "http://192.168.3.8:8112" },
  { name: "Prowlarr", ping: "http://192.168.3.8:9696" },
  { name: "FlareSolverr", ping: "http://192.168.3.8:8191" },
];

let healthCache = {};

async function checkService(url) {
  try {
    const res = await fetch(url, {
      signal: AbortSignal.timeout(5000),
      tls: { rejectUnauthorized: false },
    });
    return res.ok || res.status > 0;
  } catch {
    return false;
  }
}

async function checkAllHealth() {
  const results = await Promise.all(
    services.map(async (svc) => [svc.ping, await checkService(svc.ping)])
  );
  healthCache = Object.fromEntries(results);
}

checkAllHealth();
setInterval(checkAllHealth, 60_000);

// --- Proxmox resource monitoring ---

const PVE_URL = process.env.PVE_API_URL || "https://192.168.3.2:8006";
const PVE_TOKEN_ID = process.env.PVE_API_TOKEN_ID;
const PVE_TOKEN_SECRET = process.env.PVE_API_TOKEN_SECRET;
const PVE_AUTH = PVE_TOKEN_ID && PVE_TOKEN_SECRET
  ? `PVEAPIToken=${PVE_TOKEN_ID}=${PVE_TOKEN_SECRET}`
  : null;

let resourceCache = { nodes: [], storage: [] };

async function fetchResources() {
  if (!PVE_AUTH) return;
  try {
    const res = await fetch(`${PVE_URL}/api2/json/cluster/resources`, {
      headers: { Authorization: PVE_AUTH },
      tls: { rejectUnauthorized: false },
      signal: AbortSignal.timeout(10_000),
    });
    const { data } = await res.json();

    const nodes = [];
    const storage = [];

    for (const r of data) {
      if (r.type === "node" && r.status === "online") {
        nodes.push({
          name: r.node,
          cpu: Math.round((r.cpu || 0) * 100),
          maxcpu: r.maxcpu,
          memUsed: r.mem,
          memTotal: r.maxmem,
          memPercent: r.maxmem ? Math.round((r.mem / r.maxmem) * 100) : 0,
        });
      }

      if (r.type === "storage" && r.storage === "local-lvm" && r.node === "proxmoxmain") {
        storage.push({
          name: "SSD",
          used: r.disk,
          total: r.maxdisk,
          free: r.maxdisk - r.disk,
          percent: r.maxdisk ? Math.round((r.disk / r.maxdisk) * 100) : 0,
        });
      }

      if (r.type === "storage" && r.storage === "cloud" && r.node === "proxmoxmain") {
        storage.push({
          name: "Cloud",
          used: r.disk,
          total: r.maxdisk,
          free: r.maxdisk - r.disk,
          percent: r.maxdisk ? Math.round((r.disk / r.maxdisk) * 100) : 0,
        });
      }
    }

    nodes.sort((a, b) => (a.name === "proxmoxmain" ? -1 : 1));
    resourceCache = { nodes, storage };
  } catch (e) {
    console.error("Proxmox API error:", e.message);
  }
}

fetchResources();
setInterval(fetchResources, 30_000);

// --- SSR ---

function renderPage() {
  const initial = { health: healthCache, resources: resourceCache };
  const appHtml = renderToString(h(App, { initial }));
  const dataScript = `<script>window.__INITIAL_DATA__=${JSON.stringify(initial).replace(/</g, "\\u003c")}</script>`;
  return indexTemplate
    .replace('<div id="root"></div>', `<div id="root">${appHtml}</div>`)
    .replace("</head>", `${dataScript}</head>`);
}

// --- HTTP server ---

const MIME = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
};

Bun.serve({
  port: 3000,
  async fetch(req) {
    const { pathname } = new URL(req.url);

    if (pathname === "/api/status") {
      return Response.json(
        { health: healthCache, resources: resourceCache },
        { headers: { "Cache-Control": "no-cache" } }
      );
    }

    // Static assets (anything with a file extension)
    if (pathname.includes(".")) {
      const filePath = join(DIST, pathname);
      const file = Bun.file(filePath);
      if (await file.exists()) {
        const ext = filePath.slice(filePath.lastIndexOf("."));
        return new Response(file, {
          headers: { "Content-Type": MIME[ext] || "application/octet-stream" },
        });
      }
      return new Response("Not Found", { status: 404 });
    }

    // SSR for all other routes
    return new Response(renderPage(), {
      headers: { "Content-Type": "text/html; charset=utf-8" },
    });
  },
});

console.log("Dashboard running on :3000 (SSR enabled)");
