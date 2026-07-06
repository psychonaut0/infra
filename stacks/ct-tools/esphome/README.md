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

## Tracked devices

| File | Device | Hardware | Status |
|---|---|---|---|
| `config/presence-sensor.yaml` | Presence sensor | ESP32-C3 + LD2420 mmWave | Live, dashboard-managed |
| `config/archive/thermostat.yaml` | Thermostat (#1) | bk72xx (Beken, reflashed Tuya) | **Base stub only** — see below |

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
