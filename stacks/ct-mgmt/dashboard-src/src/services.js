const I = '/icons';

export const sections = [
  {
    name: 'Quick Access',
    services: [
      { name: 'Home Assistant', desc: 'Automation', href: 'https://homeassistant.lan', ping: 'http://192.168.3.10:8123', icon: `${I}/home-assistant.svg` },
      { name: 'Jellyfin', desc: 'Media server', href: 'http://jellyfin.lan', ping: 'http://192.168.3.8:8096', icon: `${I}/jellyfin.svg` },
      { name: 'Immich', desc: 'Photos', href: 'https://immich.lan', ping: 'http://192.168.3.9:2283', icon: `${I}/immich.svg` },
    ],
  },
  {
    name: 'Often',
    services: [
      { name: 'Portainer', desc: 'Container management', href: 'http://portainer.lan', ping: 'http://192.168.3.12:9000', icon: `${I}/portainer.svg` },
      { name: 'Proxmox', desc: 'Hypervisor', href: 'https://proxmox.lan', ping: 'https://192.168.3.2:8006', icon: `${I}/proxmox.svg` },
      { name: 'Frigate', desc: 'NVR', href: 'https://nvr.lan', ping: 'http://192.168.3.7:5000', icon: `${I}/frigate.svg` },
    ],
  },
  {
    name: 'Infrastructure',
    services: [
      { name: 'Pi-hole', desc: 'DNS & ad blocking', href: 'http://dns.lan', ping: 'http://192.168.3.5', icon: `${I}/pi-hole.svg` },
      { name: 'Proxmox Node', desc: 'Secondary node', href: 'https://proxmox-node.lan', ping: 'https://192.168.3.3:8006', icon: `${I}/proxmox.svg` },
      { name: 'FileBrowser', desc: 'File management', href: 'http://files.lan', ping: 'http://192.168.3.11:8080', icon: `${I}/filebrowser.svg` },
      { name: 'Gatus', desc: 'Uptime monitoring', href: 'http://status.lan', ping: 'http://192.168.3.12:8080', icon: `${I}/gatus.svg` },
      { name: 'ESPHome', desc: 'IoT firmware', href: 'http://esphome.lan', ping: 'http://192.168.3.15:6052', icon: `${I}/esphome.svg` },
      { name: 'Backup', desc: 'Restic status', href: 'http://backup.lan', ping: 'http://192.168.3.13', icon: `${I}/backup.svg` },
    ],
  },
  {
    name: 'Media Tools',
    services: [
      { name: 'Sonarr', desc: 'TV series', href: 'http://192.168.3.8:8989', ping: 'http://192.168.3.8:8989', icon: `${I}/sonarr.svg` },
      { name: 'Radarr', desc: 'Movies', href: 'http://192.168.3.8:7878', ping: 'http://192.168.3.8:7878', icon: `${I}/radarr.svg` },
      { name: 'Deluge', desc: 'Downloads', href: 'http://192.168.3.8:8112', ping: 'http://192.168.3.8:8112', icon: `${I}/deluge.svg` },
      { name: 'Prowlarr', desc: 'Indexers', href: 'http://192.168.3.8:9696', ping: 'http://192.168.3.8:9696', icon: `${I}/prowlarr.svg` },
      { name: 'FlareSolverr', desc: 'CF bypass', href: 'http://192.168.3.8:8191', ping: 'http://192.168.3.8:8191', icon: `${I}/flaresolverr.svg` },
    ],
  },
];

export const bookmarks = [
  {
    name: 'Dev',
    links: [
      { name: 'GitHub', href: 'https://github.com/psychonaut0', icon: `${I}/github.svg` },
      { name: 'Bitbucket', href: 'https://bitbucket.org', icon: `${I}/bitbucket.svg` },
    ],
  },
  {
    name: 'Infra',
    links: [
      { name: 'Cloudflare', href: 'https://one.dash.cloudflare.com', icon: `${I}/cloudflare.svg` },
      { name: 'UniFi', href: 'https://192.168.1.1', icon: `${I}/ubiquiti.svg` },
      { name: 'Tailscale', href: 'https://login.tailscale.com', icon: `${I}/tailscale.svg` },
      { name: 'Njalla', href: 'https://njal.la', icon: `${I}/globe.svg` },
    ],
  },
];
