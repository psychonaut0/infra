# ESPHome (ct-tools)

ESPHome dashboard runs on **ct-tools** (`192.168.3.15:6052`, `http://esphome.lan`) via
`../docker-compose.yml`. Device configs live in `config/` (bind-mounted to `/config`).

## Secret handling

Device YAMLs use `!secret` refs for everything sensitive (API encryption key, OTA
password, fallback-AP password, WiFi). Real values live in `config/secrets.yaml`, which is
**gitignored** — this repo is public. The live `secrets.yaml` on the box, plus the whole
`/opt/stacks/ct-tools` tree, is backed up nightly to B2 by ct-backup, so credentials
survive a CT loss even though they're not in git.

If you add a new device, keep secrets in `secrets.yaml` and reference them with `!secret` —
never inline them, or they'll be published on push.

## Reaching devices on the IoT VLAN

The ESPHome devices live on the IoT VLAN (`192.168.2.0/24`); ct-tools is on
`192.168.3.15`. Making the dashboard see and manage them across that boundary needs four
things, all in place as of 2026-07-07:

1. **Firewall** — ct-tools is in the UniFi allow-exception into `192.168.2.0/24` (mirrors
   ct-ha). See the ct-tools note in the top-level `CLAUDE.md`.
2. **`use_address:` per device** — mDNS (`<name>.local`) can't cross the VLAN, so each
   device's `wifi:` block pins the IP (e.g. `use_address: 192.168.2.128`). This is what the
   CLI/dashboard use to connect for logs/OTA.
3. **Ping-based dashboard status** — `ESPHOME_DASHBOARD_USE_PING=true` in
   `../docker-compose.yml` (the default mDNS status can't cross the VLAN).
4. **Unprivileged ICMP in the LXC** — the dashboard pings via `icmplib`, which needs
   `net.ipv4.ping_group_range` to allow the container's GID. Set on the ct-tools host in
   `/etc/sysctl.d/99-esphome-ping.conf` as `0 65534` (upper bound must stay within the
   unprivileged-LXC user-namespace gid map — `2147483647` is rejected with `Invalid
   argument`).

Adopting a device in Home Assistant is *slow* because of the same mDNS gap: the
ESPHome config flow's host step takes roughly **two minutes** before it returns
the encryption-key prompt, since HA tries to resolve `<name>.local` first and
must wait for that to time out. It is not stuck — give it 3+ minutes before
concluding anything is wrong. Driving the flow over the REST API needs the
client timeout raised accordingly, or it fails while the flow succeeds
server-side.

Notes:
- The dashboard's online dot only refreshes **while the dashboard is open in a browser**
  (it pings on demand for active subscribers) — that's normal ESPHome behavior, not a fault.
- The dashboard's stored ping address (`config/.esphome/storage/<name>.yaml.json`) is
  written from `use_address` **on compile**. If you add `use_address` to an existing device,
  recompile it once (or the dot stays offline until you do).

## Do not compile on ct-tools

**Build firmware on the workstation and push OTA. Never run `esphome compile`
— or trigger a dashboard build — inside the `esphome` container here.**

On 2026-09-18 a single esp-idf build for an ESP32-WROOM-32 target exhausted the
CT's 2 vCPU / 2048MB and wedged it solid. It still answered ping, but `sshd`
could no longer fork (`Connection timed out during banner exchange`) and
`pct exec 110` hung too — the same signature this repo documents for the ct-dev
memory outage. Recovery needed a forced restart from the hypervisor:

```sh
ssh proxmoxmain 'pct stop 110; sleep 3; pct start 110'
```

ct-tools is sized for the dashboard's *editing and OTA* role, not for
compilation. The dashboard UI offers an Install button that compiles here, so
this is easy to trigger by accident.

Build instead on a workstation:

```sh
uv tool install esphome                 # isolated, no system packages
esphome run <device>.yaml --device /dev/ttyUSB0   # first flash, over USB
esphome run <device>.yaml               # subsequent flashes, over OTA
```

Serial access on Arch needs the `uucp` group. `sg` does not exist there, so use
`newgrp uucp` with a heredoc, or log out and back in after `usermod -aG uucp`.
The device YAML uses `!secret`, so copy `secrets.yaml` from this CT next to it
first — and delete it afterwards; it is gitignored but the repo is public.

The one thing a dashboard-side compile does that a workstation build cannot is
write `use_address` into `config/.esphome/storage/<name>.yaml.json`, which is
what drives the dashboard's online dot. Without it the dot stays grey while the
device works normally. That is cosmetic — do not wedge the container for it.
Raise the CT's resources first if you really want it.

## Tracked devices

| File | Device | Hardware | Status |
|---|---|---|---|
| `config/presence-sensor-1.yaml` | Presence sensor 1 | ESP32-WROOM-32 + LD2420 mmWave | Live, dashboard-managed |
| `config/archive/thermostat.yaml` | Thermostat (#1) | bk72xx (Beken, reflashed Tuya) | **Base stub only** — see below |

## LD2420 wiring — read before building another one

The HLK-LD2420's `OT1`/`OT2` labels do not mean what the datasheet implies. On
the **V2.1** board running firmware **v1.6.1**, verified on hardware 2026-09-18:

| Module pin | Actual function | ESP32-WROOM-32 |
|---|---|---|
| `3V3` / `GND` | power | `3V3` / `GND` |
| **`OT2`** | **UART TX** (module → ESP) | `GPIO16` |
| `RX` | UART RX (ESP → module) | `GPIO17` |
| **`OT1`** | digital presence output | `GPIO4` |

Wire `OT1` as TX — the documented reading — and you get a device that joins
WiFi, reports healthy, and never receives one valid frame. The failure is
silent: ESPHome logs `Reply frame too long` / `Communication failed` and the
firmware version reads `v0.0.0`.

Two diagnostics that settle it quickly:

- **Add `debug:` to the `uart:` block** and compare `>>>` against `<<<`. If the
  received bytes are your own transmitted bytes, TX and RX are shorted —
  typically two jumpers sharing a breadboard row, or an unpowered module
  bridging the lines through its clamp diodes. Silence instead means nothing is
  driving RX at all.
- **Watch the `OT1` GPIO binary sensor.** It's a plain digital pin, independent
  of baud rate and protocol. If it never toggles when you move in front of the
  module, the problem is power or contact — not configuration.

Also pin `logger: hardware_uart:` explicitly. On the ESP32-C3 the obvious UART
pins (`GPIO20`/`GPIO21`) *are* UART0, so the console and the radar contend for
one peripheral. On the WROOM-32, `GPIO16`/`GPIO17` are UART2 and the console
stays on `GPIO1`/`GPIO3`, which is why this board is the easier target.

The earlier ESP32-C3 configs (`presence-sensor.yaml`, `presence-sensor-2.yaml`)
were removed on 2026-09-18 when the fleet standardised on the WROOM-32.

## The thermostats — important

Home Assistant (ct-ha) integrates **8 thermostats** (`Thermostat`, `Thermostat 2`–`8`) on
the IoT subnet `192.168.2.x` via the ESPHome native API. **Their functional source YAML no
longer exists anywhere on this infra.** A running ESPHome device stores only compiled
firmware, never its source, so the configs cannot be recovered from the devices.

- The only artifact is `config/archive/thermostat.yaml` — a bare adoption stub for device
  #1 (base + wifi + api key + OTA password, **no `climate:` block**). It is not the
  functional config.
- #2–#8 have no saved source at all. They were flashed from something no longer on the
  machines (a community YAML / another PC / web flasher).

### To edit or rebuild a thermostat config you must recreate it from scratch, and:

1. **Network:** ct-tools is on `192.168.3.x`; thermostats are on the isolated IoT VLAN
   `192.168.2.x`. mDNS discovery won't cross subnets and OTA from ct-tools is likely
   firewalled — only ct-ha has granted IoT access. Reaching them for OTA needs a
   firewall/route change (or flashing from a host with IoT access).
2. **OTA password:** needed to push firmware OTA. Recoverable **only for #1** (in the
   stub / `secrets.yaml`). HA stores each device's *API encryption key* but **not** the OTA
   password, so #2–#8 require a **one-time serial re-flash** (USB-TTL to the bk7252) to set
   a known password before they can be dashboard-managed.

They work fine in HA today and need none of this unless you want to change one.
