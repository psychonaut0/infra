# Home Assistant configuration

Home Assistant Container runs on ct-ha, with `/opt/stacks/ct-ha/config` mounted
at `/config`. The initial YAML baseline was copied from HA 2026.10.4 on
2026-11-04, then simplified during the customization cleanup below. Compose
pins that version so an ordinary service deployment does not also upgrade HA.

Private household requirements, routine design and future voice/doorway projects
are recorded in `docs/home-assistant-design.local.md` (gitignored).

## Planned enhancements

Early to mid future projects, after the core household routines:

- Voice control with optional AI assistance, aimed at making everyday actions
  accessible without opening the app. Reuse the routines behind dashboard
  buttons. Hardware, activation method and processing location are deferred.
- A relay for remotely opening the doorway without reaching the intercom.
  Door/intercom compatibility, hardware and activation will be designed later.
  The proposed opening action is explicit and authenticated; voice access needs
  a separate decision before implementation.

## Configuration ownership

| Configuration | Source of truth | How to change it |
| --- | --- | --- |
| YAML configuration, two dashboards and the camera power helper | This directory's reviewed `config/` files | Edit, diff, validate, apply, then reload/restart |
| UI-created dashboards/helpers, integration registrations, entity/device/area settings | HA's APIs and internal storage | Use REST configuration flows or WebSocket management commands |
| HTTP/proxy settings | HA's network settings/API | Since 2026.10 the live values are in `.storage/http`; the ignored legacy YAML block has been removed |
| HACS themes, frontend assets, custom integrations | HACS/live config | Manage through HACS; preserve on deploy and restore from backup |
| Secrets, credentials, recorder database, user sessions | Live CT and private backups | Never commit them |

The Git ignore rules allow the four core YAML files, `dashboards/casa.yaml`,
`dashboards/impostazioni.yaml` and `tools/camera_power.py` under `config/`.
Do not force-add that directory, export raw `.storage`, or inline credentials.
Use `!secret` references and keep their values in live `config/secrets.yaml`.
Adding a new managed file requires updating the allowlist and the helper's
managed-file list, plus checking its includes and validation dependencies.

## Customization cleanup — 2026-11-04

The experimental Material You theme/utilities, floor3d card and Material Symbols
integration were uninstalled through HA/HACS. Browser Mod's stale resource and
the external Figtree font resource were removed. Room, label and entity icons
were replaced with built-in `mdi:` icons. The Material You sidebar panel and
frontend module injection were removed from YAML; the native frontend is used.
HACS and Frigate remain installed.

The 26 Material You helpers and unused `input_select.stanza` room selector were
removed through their APIs, along with their now-empty labels. At the owner's
request, `input_boolean.camera_switch` and both legacy camera automations were
also removed so camera/security routines could be designed afresh. The household
foundation below adds new manual routines and verified camera power controls;
an intrusion alarm is not configured yet. Camera devices and the Frigate integration
remain available; this cleanup does not issue camera power/recording commands.

The private pre-cleanup configuration archive (YAML, internal settings and
installed plugin files; recorder DB excluded) is on ct-ha at
`/opt/stacks/ct-ha/.cleanup-backups/20261104-customization-cleanup/config.tar.gz`.
It is inside the nightly full-stack backup tree. Individual YAML deployments
also retain the previous files in `/config/.infra-backups/`.

## Edit and deploy YAML

From the repository root, with Python 3 and SSH access to ct-ha:

```sh
python scripts/ha-config.py diff
# Edit the tracked YAML after reviewing any live/UI changes.
python scripts/ha-config.py check
python scripts/ha-config.py apply
```

`diff` compares live files to the repository: exit 0 means identical, 2 means
different, and 1 means the check failed. Its output can contain private values
if someone has added inline secrets on the CT; review before copying into Git.
Reconcile UI edits into the repository before applying: `apply` intentionally
makes the allowlisted live files match Git.

`check` copies configuration dependencies, secrets and registries to a private
temporary directory inside the existing container, overlays the proposed files,
and runs the installed HA configuration checker there. It excludes recorder
databases, logs, caches and backup archives. It does not reload HA or write to
live `.storage`; the temporary copy is deleted afterwards. Checker output may
contain private configuration details, so do not commit it without review.
This verifies HA's configuration schemas, not whether an automation's logic or
device references achieve the intended behavior.

`apply` repeats validation, refuses to overwrite YAML changed during that check,
backs up the existing managed files under `/config/.infra-backups/<timestamp>/`,
and replaces only changed managed files. Its private `manifest.json` records
which files existed before deployment, including new dashboard/helper paths. Ordinary write failures restore files
already replaced. Individual replacements are atomic; the multi-file update is
not a transaction across power loss. Avoid simultaneous UI edits or deployments.
Runtime state, secrets and HACS files are preserved. Backups are private and
remain in the full-stack nightly backup; remove old copies deliberately when
no longer needed.

Applying saves files only. To activate changes to automations, scripts or scenes,
call the corresponding `automation.reload`, `script.reload` or `scene.reload`
action through the REST API. For example, after an automation-only change:

```sh
python - <<'PY'
from pathlib import Path
import urllib.request
import yaml
c = yaml.safe_load((Path.home() / '.config/infra/homeassistant.yml').read_text())
request = urllib.request.Request(
    c['url'].rstrip('/') + '/api/services/automation/reload',
    data=b'{}', method='POST',
    headers={'Authorization': 'Bearer ' + c['token'], 'Content-Type': 'application/json'},
)
with urllib.request.urlopen(request, timeout=30) as response:
    print('Automation reload:', response.status)
PY
```

That API example uses locally installed PyYAML and the private credential file;
the YAML diff/check/apply helper itself needs only Python's standard library.
For changes requiring a restart, use `infra restart homeassistant` in a suitable
maintenance window. Check API health, entity availability and automation traces
after activation; do not test camera/heating behavior by triggering actions
unless that behavior is part of the requested change.

Use `infra deploy homeassistant` for the Compose/image lifecycle. The updated
CLI excludes the entire HA `config/` tree from generic stack synchronization,
so a service deploy cannot bypass staged YAML validation or overwrite private
runtime files. Use the updated CLI when deploying from a checkout containing
these files; older CLI builds do not have that exclusion.

## API work by agents

The existing private credentials are in `~/.config/infra/homeassistant.yml`
(`url` and `token`, mode 0600). The AI-tools token currently belongs to the
owner/admin account. Load it in memory; never print it or put it in Git or
command-line arguments. HA is reachable over the LAN/Tailscale; MCP is optional
and currently not enabled.

Use authenticated REST reads and WebSocket registry/helper/dashboard reads to
discover the current schema and IDs before writing. Keep API changes scoped to
the requested behavior, and reread the affected objects afterwards. API-managed
settings are backed up but are **not yet declarative Git configuration**;
do not claim the Git-managed files can rebuild the whole home.

## Rollback and recovery

For a configuration rollback, restore the desired Git version of the managed files, run
`check`, then `apply` and the appropriate reload/restart. If the repository is
unavailable, previous live YAML is in the private `.infra-backups` directory;
inspect the selected backup before copying its files back and activating them.

Full recovery still needs the nightly full-stack backup (including `.storage`,
`secrets.yaml`, HACS assets and Mosquitto credentials) plus the consistent
recorder SQLite backup. Follow `docs/recovery.md`; Git alone is not a full HA
backup. After restoring private runtime state, reconcile the restored YAML with
the intended Git revision before starting HA.

Reference: [HA configuration validation](https://www.home-assistant.io/docs/configuration/)
and [HA registries/storage](https://contributors.home-assistant.io/architecture/core/registries/).


## Household foundation

The family dashboard is `/casa-famiglia/casa`, with Casa, Stanze, Clima and
Sicurezza views and ten room subviews. `/casa-impostazioni/routine` is a separate
admin-only dashboard for routine settings. Both use native cards and YAML.
The built-in overview stays available for reference.

Buongiorno, Buonanotte, Usciamo and Rientro are shared scripts, suitable for
future voice/physical triggers. A shared runner verifies the camera and shutter
results before reporting completion; a new request replaces the previous run.
Camera and shutter tasks run in parallel, so motor commands do not wait for
camera startup. Their response variables and visible results are separate.
Every button press reapplies the camera policy, even when the household mode
already matches. Presence is manual until phone tracking is
commissioned. Initial presence is Da verificare: it does not issue camera power
commands. Guests prevent Usciamo from declaring the house empty. Setting Night
is the last person's explicit confirmation that everyone is in bed; the
22:45 reminder never sets Night itself. The 07:30 morning reminder also requires
a button press: it never opens shutters or changes cameras by itself. Both
reminders are HA notifications; phone delivery is not configured. User edits
to times/options survive restarts because helpers restore their state.

Night shutter targets are 15%, confirmed by the owner for all five shutters;
morning is 100%. Both options are enabled for subsequent button presses.
Deployment does not replay a routine. Night includes office, kitchen, master bedroom
and both guest-bedroom shutters. Morning includes office, kitchen and master;
the guest bedroom retains its direct controls until its wake routine is agreed.
Commands are followed by position checks, with a two-percentage-point tolerance
and a 90-second deadline. Missing devices, invalid targets and shutters that do
not arrive are named in the result. Already-correct stationary shutters are not
moved again. Physical shutter travel still needs observation on first use;
commissioning exercised motor commands only against simulated devices.
Usciamo and Rientro never move shutters. Heating cards directly control the
existing thermostats; automatic setbacks and predictive heating remain pending
chosen targets, phone tracking and measured floor response.

The camera power helper uses the existing API key via the controller API because
this HA version's native UniFi Network integration requires username/password.
It loads `/config/.camera-power.json` (0600, ignored), pins the gateway's TLS
certificate before sending the key, checks live client-to-port bindings, and
changes only `poe_mode` on the three configured camera ports. It preserves the
full remaining port overrides, serializes its own writes, and rejects detected
concurrent configuration changes. UniFi offers no atomic compare-and-swap here;
avoid simultaneous switch-port edits. Reverify mappings after recabling.

Credentials and the private mapping are backed up with the full stack. Never
add them to Git. A changed gateway certificate needs deliberate pin review.
The API key itself can have broader controller privileges than the helper's
three-port allowlist; access to its private file must remain restricted.

Camera policy pauses Frigate before cutting PoE for Home/awake. Night or Away
restores PoE, waits for RTSP readiness, then resumes Frigate processing. Privacy
attiva requires explicit PoE-off mode, zero-power telemetry and unreachable RTSP
for all three cameras; failed, offline or stale controller telemetry never becomes a privacy claim. Expected
camera unavailability while power is off is normal. The camera policy is
reconciled after mode changes, HA startup/controller recovery and every five
minutes. There is no alarm arming, siren, doorway opening or automatic phone
presence in this release. Test privacy, recovery and camera startup before
using automatic household presence. Turning off cameras still attempts physical
power-off if Frigate's pause action fails. Video readiness requires all three
streams to report fresh positive frame rates, all cameras enabled, and all
recording switches on. This verifies live input and recording configuration,
not that every segment has been successfully written to NVR storage.
Frigate reports statistics every 60 seconds here; video startup allows two
minutes for a fresh frame-rate report after RTSP becomes reachable.

Casa retains the four tabs and shows a compact set of routine controls, a live
step, separate camera/shutter results and the final check time. Open or unknown
door/window states produce a warning without blocking the routine. A failed
camera check does not prevent shutter actions. Unexpected interruption is
reported by a watchdog after a stopped script or HA restart. The background
camera reconciler yields while a manual routine is running.

Run local checks with:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s scripts -p 'test_*.py' -v
python scripts/ha-config.py check
python scripts/check_ha_routines.py
```

The last command runs the proposed script YAML using HA's installed script
integration in a temporary, empty runtime inside the existing image. All device,
power, notification and log services are simulated. It never calls the live HA
API or credentials, and never moves devices. The 12 scenarios cover Night,
Morning, repeated presses, guests, open windows, power failure, Frigate failure,
stuck shutters, missing video, invalid targets, unknown presence and a newer
routine replacing a running one. Timeouts are shortened only in the test copy.


Commissioning on 2026-11-04 verified zero PoE power and unreachable RTSP for all
three cameras, then restored all three streams to approximately 5 fps. Existing
port configuration was preserved, apart from controller normalization of empty
list fields. An HA restart preserved the editable settings. Full switch reboot
behavior remains untested. The pre-foundation configuration backup is at
`/opt/stacks/ct-ha/.cleanup-backups/household-foundation.KzJQr3/config.tar.gz`.

After commissioning, the owner confirmed someone was home and awake. Rientro
set In casa; all three camera ports were verified off with zero watts and
unreachable RTSP. The dashboard showed Privacy attiva.
