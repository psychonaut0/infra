# BLVCKFlow (ROG Flow Z13) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring an ASUS ROG Flow Z13 (2025) into service as `BLVCKFlow` — Windows 11 + CachyOS dual boot with Secure Boot enabled, running the fleet's Hyprland desktop, with full SSH and `infra` reach.

**Architecture:** Three phases. **Phase 1** (Tasks 1–9) is pure repo work done *now* on BLVCKSmall, so the Z13 has everything to pull on first boot. **Phase 2** (Tasks 10–17) is the physical build — Windows prep, a two-stage CachyOS install that converts to XBOOTLDR after Calamares, Secure Boot key enrolment, hardware enablement, desktop bring-up. **Phase 3** (Tasks 18–20) onboards it into the fleet.

**Tech Stack:** CachyOS (Arch), systemd-boot + XBOOTLDR, sbctl, ext4, Hyprland, AGS v3 (GTK4/TSX), GNU Stow, asusctl, Tailscale, Go `infra` CLI.

**Spec:** `docs/superpowers/specs/2026-08-09-blvckflow-z13-setup-design.md`

## Global Constraints

- **Secure Boot stays ENABLED.** Riot Vanguard requires it plus TPM 2.0 on Windows 11; disabling it breaks League, which is the only reason Windows is retained.
- **`sbctl enroll-keys --microsoft`** — the `--microsoft` flag is mandatory. Without Microsoft's KEK/db, `bootmgfw.efi` stops being trusted and Windows will not boot.
- **The factory ESP (p1) is never reformatted.** It holds the Windows bootloader.
- **XBOOTLDR must be FAT32.** GPT type GUID `bc13c2ff-59e6-4262-a352-b275fd6f7172`; `sfdisk` type alias `xbootldr`.
- **Windows partition: 200 GiB. Swap: 64 GiB. XBOOTLDR: 1 GiB.** Root takes the remainder (~665 GiB).
- **Baseline kernel cmdline is `root=UUID=… rw resume=UUID=…` and nothing else.** No `dcdebugmask`, `sg_display`, `cwsr_enable`, `abmlevel`, `gttsize`, `disable_aspm`, or `amd_pstate=` — each is a workaround for a specific symptom, applied only after observing that symptom.
- **Never write `/etc/kernel/cmdline`** — unread on a stock CachyOS install. The cmdline lives in the `options` line of `/boot/loader/entries/*.conf`. Verify with `cat /proc/cmdline`, never by reading the config back.
- **`mkinitcpio -P` rebuilds initramfs only** — it does not apply cmdline changes or regenerate loader entries.
- **Hostname is exactly `BLVCKFlow`** (capital B-L-V-C-K, capital F). `ags/lib/host.ts` compares `GLib.get_host_name()` against this string.
- **`gptfdisk`/`sgdisk` is not installed on CachyOS.** Use `sfdisk`.
- **This repo (`infra`) is public and sanitized.** No real domain, public IP, or tailnet suffix in any committed file — those live in the gitignored `CLAUDE.local.md`.
- **Commit style:** short imperative subject with a conventional-commit prefix, explanatory body. No co-author lines.

---

## Phase 1 — Repo preparation (on BLVCKSmall, before touching the Z13)

### Task 1: Land the existing dotfiles working tree

The dotfiles repo has eight modified files and one untracked script. Tasks 2–8 edit some of the same files, so this must be cleared first or the diffs tangle.

**Files:**
- Commit: `ags/.config/ags/widget/ControlPanel.tsx`, `foot/.config/foot/foot.ini`, `hypr/.config/hypr/hosts/BLVCKSmall.conf`, `hypr/.config/hypr/hyprland/app_keybinds.conf`, `hypr/.config/hypr/hyprland/colors.conf`, `hypr/.config/hypr/hyprpaper.conf`, `ssh/.ssh/config`, `zsh/.zshrc`, `scripts/scripts/brightness.sh`

**Depends on:** nothing.
**Provides:** a clean `git status` in `~/dotfiles`.

- [ ] **Step 1: Review what is uncommitted**

```bash
cd ~/dotfiles && git status --short && git diff --stat
```

Expected: 8 modified files, `?? scripts/scripts/brightness.sh`.

- [ ] **Step 2: Commit the brightness script and its binds**

`brightness.sh` is untracked but already referenced by `BLVCKSmall.conf` — a broken reference if only one lands.

```bash
cd ~/dotfiles
git add scripts/scripts/brightness.sh hypr/.config/hypr/hosts/BLVCKSmall.conf
git commit -m "feat(scripts): add brightness.sh, wire BLVCKSmall brightness keys to it"
```

- [ ] **Step 3: Commit the theme regeneration as one unit**

```bash
cd ~/dotfiles
git add foot/.config/foot/foot.ini hypr/.config/hypr/hyprland/colors.conf hypr/.config/hypr/hyprpaper.conf
git commit -m "chore(theme): regen matugen output for new wallpaper"
```

- [ ] **Step 4: Commit the remaining independent changes**

```bash
cd ~/dotfiles
git add ags/.config/ags/widget/ControlPanel.tsx
git commit -m "fix(ags): floor brightness slider at 1% so the panel never goes fully dark"
git add hypr/.config/hypr/hyprland/app_keybinds.conf
git commit -m "fix(hypr): drop Super+F2 monitor-disable bind"
git add ssh/.ssh/config
git commit -m "feat(ssh): add ct-workout host alias"
git add zsh/.zshrc
git commit -m "feat(zsh): add Android SDK paths and kilocode nvm lazy-cmd"
```

- [ ] **Step 5: Verify the tree is clean**

```bash
cd ~/dotfiles && git status --short
```

Expected: **no output**.

---

### Task 2: Capability flags in the AGS shell

`host.ts` exports host-identity booleans and six sites branch on them. `BLVCKFlow` needs every branch currently gated on `isBLVCKSmall` — it has a backlight, an accelerometer, and an on-screen keyboard. Adding `|| isBLVCKFlow` at each site will rot; derive capability flags instead so host #4 only ever edits `host.ts`.

**Files:**
- Modify: `ags/.config/ags/lib/host.ts`
- Modify: `ags/.config/ags/widget/ControlPanel.tsx:7,28,31,842,862`
- Modify: `ags/.config/ags/widget/WallpaperButton.tsx:5,72`
- Modify: `ags/.config/ags/widget/Volume.tsx:7`

**Depends on:** Task 1.
**Provides:** `isBLVCKFlow`, `hasBacklight`, `hasRotation`, `hasOSK` exported from `lib/host.ts`.

- [ ] **Step 1: Rewrite `lib/host.ts`**

Replace the whole file with:

```ts
import GLib from "gi://GLib"

export const hostname = GLib.get_host_name()
export const isBLVCKMain = hostname === "BLVCKMain"
export const isBLVCKSmall = hostname === "BLVCKSmall"
export const isBLVCKFlow = hostname === "BLVCKFlow"

// Capability flags. Widgets MUST branch on these, not on host identity, so a
// new host only ever touches this file.
export const hasBacklight = isBLVCKSmall || isBLVCKFlow  // brightnessctl present
export const hasRotation = isBLVCKSmall || isBLVCKFlow   // iio-sensor-proxy + auto-rotate.sh
export const hasOSK = isBLVCKSmall || isBLVCKFlow        // wvkbd installed
```

- [ ] **Step 2: Update the ControlPanel import (line 7)**

Replace:

```ts
import { isBLVCKSmall } from "../lib/host"
```

with:

```ts
import { hasBacklight, hasRotation } from "../lib/host"
```

- [ ] **Step 3: Widen the two 3-second pollers (lines 28 and 31)**

These are the zombie-spawners the dotfiles CLAUDE.md warns about — the `: [{ as: ... }] as any` stubs exist so BLVCKMain never spawns them. BLVCKFlow wants the real thing.

Line 28, replace `isBLVCKSmall` with `hasBacklight`:

```ts
const [brightness, refreshBright, setBrightness] = hasBacklight
  ? pollState("brightnessctl -m | cut -d, -f4 | tr -d '%'", 3000)
  : [{ as: () => "", subscribe: () => {}, peek: () => "" }, () => {}, () => {}] as any
```

Line 31, replace `isBLVCKSmall` with `hasRotation`:

```ts
const [rotateEnabled, refreshRotate] = hasRotation
  ? pollState("[ -f /tmp/auto-rotate.pid ] && kill -0 $(cat /tmp/auto-rotate.pid) 2>/dev/null && echo yes || echo no", 3000)
  : [{ as: () => "", subscribe: () => {}, peek: () => "" }, () => {}] as any
```

- [ ] **Step 4: Update the two render-side gates (lines 842 and 862)**

Line 842 selects the 2x2 toggle grid that contains the Rotate button:

```tsx
            {hasRotation ? (
```

Line 862 gates the brightness slider:

```tsx
            {hasBacklight && (
```

- [ ] **Step 5: Update `WallpaperButton.tsx` (lines 5 and 72)**

Despite the filename, this file's default export is `BottomPanel`. Line 5:

```ts
import { hasOSK } from "../lib/host"
```

Line 72:

```tsx
          {hasOSK && (
```

- [ ] **Step 6: Drop the dead import in `Volume.tsx` (line 7)**

`isBLVCKMain` is imported and never used — only referenced in a comment. Delete the line:

```ts
import { isBLVCKMain } from "../lib/host"
```

- [ ] **Step 7: Verify it compiles**

```bash
cd ~/.config/ags && ags bundle app.tsx /tmp/ags-check.js
```

Expected: no output and exit 0. If it names a missing symbol, a rename was missed.

- [ ] **Step 8: Verify the shell still runs on this host**

```bash
ags quit; sleep 1; setsid -f ags run ~/.config/ags >/dev/null 2>&1; sleep 3; pgrep -x gjs
```

Expected: a PID. Open the control panel and confirm the brightness slider and Rotate toggle are still present (BLVCKSmall has both capabilities, so behaviour must be unchanged).

- [ ] **Step 9: Commit**

```bash
cd ~/dotfiles
git add ags/.config/ags/lib/host.ts ags/.config/ags/widget/ControlPanel.tsx ags/.config/ags/widget/WallpaperButton.tsx ags/.config/ags/widget/Volume.tsx
git commit -m "refactor(ags): branch on capabilities, not host identity

BLVCKFlow needs every branch currently gated on isBLVCKSmall — it has a
backlight, an accelerometer and an on-screen keyboard. Four call sites with
|| isBLVCKFlow would rot; hasBacklight/hasRotation/hasOSK mean a new host
only ever edits host.ts.

Also drops the unused isBLVCKMain import in Volume.tsx."
```

---

### Task 3: Make the shared scripts host-agnostic

`lid.sh` and `auto-rotate.sh` hardcode BLVCKSmall's ELAN touch id, and `lid.sh` hardcodes a 1920x1080 modeline. The Z13 is a kickstand tablet with a *detachable* cover — no hinge, no libinput `Lid Switch`, no `/proc/acpi/button/lid/LID0`. So `close`/`open` are unreachable there, but `resume` is **not**: the shared `hypridle.conf` calls `lid.sh resume` as `after_sleep_cmd` on every host.

**Files:**
- Modify: `scripts/scripts/lid.sh`
- Modify: `scripts/scripts/auto-rotate.sh`

**Depends on:** Task 1.
**Provides:** `lid.sh resume` and `auto-rotate.sh` working unchanged on BLVCKSmall and correctly on a lidless host.

- [ ] **Step 1: Rewrite `scripts/scripts/lid.sh`**

Three changes: autodetect the touch device, replace the hardcoded modeline with `hyprctl reload` (so each host re-applies its *own* monitor rules), and treat a missing lid as "open" so a lidless tablet still re-applies monitors on wake.

```bash
#!/bin/bash
# Lid switch + resume handling.
# Usage: lid.sh close    — disable eDP-1 only in clamshell (external monitor active);
#                          never disable the last remaining output
#        lid.sh open     — re-apply monitor config, resync touch transform, respawn AGS
#        lid.sh resume   — hypridle after_sleep_cmd: dpms on + re-apply monitors if the
#                          lid is open (the switch:off event can be lost during wake)
#
# `close`/`open` are only reachable on hosts that bind a Lid Switch. `resume` runs on
# EVERY host via the shared hypridle after_sleep_cmd, including lidless tablets.

TOUCH="${LID_TOUCH:-$(hyprctl devices -j | jq -r '.touch[0].name // empty')}"
LID_STATE=/proc/acpi/button/lid/LID0/state

ensure_ags() {
  pgrep -x gjs >/dev/null || setsid -f ags run ~/.config/ags >/dev/null 2>&1
}

enable_edp() {
  # Re-applies the ACTIVE host's own monitor rules (mode/position/scale).
  hyprctl reload
  [ -n "$TOUCH" ] && hyprctl keyword "device[$TOUCH]:transform" 0
  sleep 1
  ensure_ags
}

case "$1" in
  close)
    # Only disable eDP-1 when another monitor is active (clamshell mode).
    # Disabling the sole output leaves Hyprland with zero outputs -> black screen.
    if [ "$(hyprctl monitors -j | jq length)" -gt 1 ]; then
      hyprctl keyword monitor "eDP-1, disable"
    fi
    ;;
  open)
    enable_edp
    ;;
  resume)
    hyprctl dispatch dpms on
    # No lid file (tablet) counts as open.
    if [ ! -e "$LID_STATE" ] || grep -q open "$LID_STATE" 2>/dev/null; then
      enable_edp
    else
      ensure_ags
    fi
    ;;
esac
```

- [ ] **Step 2: Patch `auto-rotate.sh` device detection**

Replace line 7 (`MONITOR="eDP-1"`) with:

```bash
MONITOR="${ROTATE_MONITOR:-$(hyprctl monitors -j | jq -r '.[] | select(.name | startswith("eDP")) | .name' | head -1)}"
[ -n "$MONITOR" ] || MONITOR="eDP-1"
```

Replace line 20 (`TOUCH="elan902c:00-04f3:2dce"`) with:

```bash
TOUCH="${ROTATE_TOUCH:-$(hyprctl devices -j | jq -r '.touch[0].name // empty')}"
```

Replace the `rotate()` function so a missing touch device does not abort:

```bash
rotate() {
  hyprctl keyword monitor "$MONITOR,transform,$1"
  [ -n "$TOUCH" ] && hyprctl keyword "device[$TOUCH]:transform" "$1"
}
```

- [ ] **Step 3: Verify autodetection matches the hardcoded values on this host**

```bash
hyprctl devices -j | jq -r '.touch[0].name'
hyprctl monitors -j | jq -r '.[] | select(.name | startswith("eDP")) | .name' | head -1
```

Expected: `elan902c:00-04f3:2dce` and `eDP-1` — i.e. the autodetection reproduces exactly what was hardcoded.

- [ ] **Step 4: Verify `lid.sh resume` is non-destructive here**

```bash
sh ~/scripts/lid.sh resume && hyprctl monitors -j | jq -r '.[] | "\(.name) \(.width)x\(.height)@\(.refreshRate) scale=\(.scale)"'
```

Expected: monitors unchanged from before the call, and AGS still running (`pgrep -x gjs`).

- [ ] **Step 5: Commit**

```bash
cd ~/dotfiles
git add scripts/scripts/lid.sh scripts/scripts/auto-rotate.sh
git commit -m "fix(scripts): autodetect touch device, drop hardcoded modeline

Both scripts hardcoded BLVCKSmall's ELAN touch id, so on any other host the
touch transform silently no-ops and input stays rotated 90 degrees off after
every rotation.

lid.sh also hardcoded a 1920x1080 modeline in enable_edp, which would corrupt
a 2560x1600 panel on wake — hyprctl reload re-applies whichever host's rules
are actually active. And since the shared hypridle calls 'lid.sh resume' on
every host, a missing /proc/acpi/button/lid now counts as open rather than
falling through without re-applying monitors."
```

---

### Task 4: `$toggleMonitor` indirection for the monitor-disable bind

`app_keybinds.conf` binds `Super+F1` to disable `$mainMonitor`. On both existing hosts that is an *external* display, so it is harmless. On BLVCKFlow `$mainMonitor` is `eDP-1` — the only output when undocked — so the bind would black-screen the machine with no way back except a blind `Super+Shift+F1`.

**Files:**
- Modify: `hypr/.config/hypr/hyprland/app_keybinds.conf:42-43`
- Modify: `hypr/.config/hypr/hosts/BLVCKMain.conf`
- Modify: `hypr/.config/hypr/hosts/BLVCKSmall.conf`

**Depends on:** Task 1.
**Provides:** `$toggleMonitor`, which every host file must define and `BLVCKFlow.conf` (Task 5) relies on.

- [ ] **Step 1: Change the binds to use the indirection**

In `hypr/.config/hypr/hyprland/app_keybinds.conf`, replace:

```
bind = $mainMod, F1, exec, hyprctl keyword monitor $mainMonitor,disable
bind = $mainMod SHIFT, F1, exec, hyprctl keyword monitor $mainMonitor,enable
```

with:

```
bind = $mainMod, F1, exec, hyprctl keyword monitor $toggleMonitor,disable
bind = $mainMod SHIFT, F1, exec, hyprctl keyword monitor $toggleMonitor,enable
```

- [ ] **Step 2: Define it in `BLVCKMain.conf`**

An undefined Hyprland variable is passed through as the literal string `$toggleMonitor`, silently breaking the bind — so *every* host file must define it. Insert after line 3 (`$secondMonitor = DP-2`):

```
$toggleMonitor = $mainMonitor
```

- [ ] **Step 3: Define it in `BLVCKSmall.conf`**

Insert after line 3 (`$secondMonitor = eDP-1`):

```
$toggleMonitor = $mainMonitor
```

- [ ] **Step 4: Verify the variable resolves on this host**

```bash
hyprctl reload && hyprctl getoption monitor 2>/dev/null; hyprctl binds | grep -A2 -i 'F1' | head -20
```

Expected: no Hyprland config errors on reload (check `hyprctl reload` output is empty), and the F1 bind present.

- [ ] **Step 5: Functionally confirm the bind still works**

Press `Super+F1` then `Super+Shift+F1`. Expected: the external monitor (DP-1) disables and re-enables, exactly as before the change.

- [ ] **Step 6: Commit**

```bash
cd ~/dotfiles
git add hypr/.config/hypr/hyprland/app_keybinds.conf hypr/.config/hypr/hosts/BLVCKMain.conf hypr/.config/hypr/hosts/BLVCKSmall.conf
git commit -m "fix(hypr): route the F1 monitor toggle through \$toggleMonitor

Super+F1 disables \$mainMonitor, which is an external display on both current
hosts. On a tablet whose \$mainMonitor is the internal panel it would leave
Hyprland with zero outputs and no way back. Every host file now names which
monitor F1 is allowed to kill."
```

---

### Task 5: `BLVCKFlow.conf` and the system-config host directory

**Files:**
- Create: `hypr/.config/hypr/hosts/BLVCKFlow.conf`
- Create: `system/hosts/BLVCKFlow/etc/sddm.conf.d/10-host.conf`

**Depends on:** Task 4 (`$toggleMonitor`).
**Provides:** the host config the Z13 symlinks to as `current.conf`.

Note on scale: 2560/1.6 = 1600 and 1600/1.6 = 1000 are both exact, so Hyprland accepts 1.6. Scale 1.5 is rejected outright (2560/1.5 is not a clean divisor). The resulting ~141 logical PPI matches the other two hosts, so the AGS bar's fixed 14px font and button sizes stay proportionate across the fleet.

- [ ] **Step 1: Create `hypr/.config/hypr/hosts/BLVCKFlow.conf`**

```
# BLVCKFlow - ASUS ROG Flow Z13 (2025, GZ302), Ryzen AI Max "Strix Halo"
# Convertible tablet with a DETACHABLE keyboard cover (no hinge), used as the
# primary machine.
$mainMonitor = eDP-1
$secondMonitor = DP-1
$toggleMonitor = $secondMonitor

# Monitors
# 13.4" 2560x1600 @ 180Hz. Scale 1.6 is the only sane fractional value:
# 2560/1.6=1600 and 1600/1.6=1000 are both exact, so Hyprland accepts it, and
# ~141 logical PPI matches the effective density of the other two hosts.
# 1.5 is rejected by Hyprland (2560/1.5 is not a clean divisor).
monitor=,preferred,auto,1
monitor=$mainMonitor,2560x1600@180,0x0,1.6
monitor=$secondMonitor,preferred,auto-right,1

# Input
input {
    kb_layout = us
    kb_variant = altgr-intl
    follow_mouse = 1
    touchpad {
        natural_scroll = true
    }
    sensitivity = -0.2

    # Pin the stylus to the internal panel, or it maps across the whole layout
    # the moment an external display is attached.
    tablet {
        output = eDP-1
        transform = 0
    }
}

# Manual monitor transform (Super+M / Super+Shift+M) is deliberately absent.
# Those binds need the touchscreen device id to keep touch input in sync with
# the rotation, and that id cannot be known until the hardware boots.
# Task 17 Step 4 reads it from `hyprctl devices` and adds the binds.
# Auto-rotate (Super+R) works without them — auto-rotate.sh autodetects.

# Volume keys (wpctl — no Evo8 on this host)
bindel = ,XF86AudioRaiseVolume, exec, wpctl set-volume -l 1 @DEFAULT_AUDIO_SINK@ 5%+
bindel = ,XF86AudioLowerVolume, exec, wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-
bindel = ,XF86AudioMute, exec, wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle
bindel = ,XF86AudioMicMute, exec, wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle

# Brightness
bindel = ,XF86MonBrightnessUp, exec, sh ~/scripts/brightness.sh up
bindel = ,XF86MonBrightnessDown, exec, sh ~/scripts/brightness.sh down

# NO LID BINDS.
# The Z13 is a kickstand tablet with a magnetic detachable cover: no hinge, no
# libinput "Lid Switch", no /proc/acpi/button/lid/LID0. Do not copy
# BLVCKSmall's bindl=,switch:*:Lid Switch lines.
# If `hyprctl devices` does report a cover/tablet-mode switch, bind it to the
# on-screen keyboard — never to monitor power.

# On-screen keyboard (hidden by default, toggle with Super+K)
exec-once = sh ~/scripts/wvkbd-toggle.sh start

bind = $mainMod, K, exec, sh ~/scripts/wvkbd-toggle.sh

# Load hyprpm plugins on boot
exec-once = hyprpm reload -n

# Touch gestures (hyprgrass)
plugin {
    touch_gestures {
        sensitivity = 4.0
        long_press_delay = 400

        hyprgrass-bind = , swipe:3:l, workspace, +1
        hyprgrass-bind = , swipe:3:r, workspace, -1
        hyprgrass-bind = , swipe:3:u, exec, ags toggle launcher
        hyprgrass-bind = , swipe:3:d, exec, ags toggle notification-center
        hyprgrass-bind = , swipe:4:d, killactive
    }
}

# Auto-rotate (disabled by default, toggle with Super+R)
bind = $mainMod, R, exec, sh ~/scripts/auto-rotate.sh toggle
```

The manual-rotate binds are deliberately omitted rather than shipped with a placeholder device id: the config is then valid exactly as written, and Task 17 Step 4 adds them once the hardware can report its ELAN id. `auto-rotate.sh` autodetects (Task 3), so automatic rotation works from first boot regardless.

- [ ] **Step 2: Create the system host directory**

`system/install.sh` iterates `system/shared` then `system/hosts/$(hostname)`, and skips the host pass entirely if the directory is absent (`[[ -d "$src" ]] || continue`). The cover keyboard has no numpad.

Create `system/hosts/BLVCKFlow/etc/sddm.conf.d/10-host.conf`:

```
[General]
Numlock=none

[Theme]
CursorTheme=Adwaita
```

- [ ] **Step 3: Verify the host file parses**

Hyprland cannot validate an inactive host file directly, so check it for the two structural requirements — the monitor variables are defined before use, and braces balance:

```bash
cd ~/dotfiles
grep -n '^\$\(mainMonitor\|secondMonitor\|toggleMonitor\)' hypr/.config/hypr/hosts/BLVCKFlow.conf
awk '{o+=gsub(/{/,"{"); c+=gsub(/}/,"}")} END {print "open="o" close="c}' hypr/.config/hypr/hosts/BLVCKFlow.conf
```

Expected: three variable definitions on lines 4–6, and `open=5 close=5` (the blocks are `input`, its nested `touchpad` and `tablet`, then `plugin` and its nested `touch_gestures`).

- [ ] **Step 4: Commit**

```bash
cd ~/dotfiles
git add hypr/.config/hypr/hosts/BLVCKFlow.conf system/hosts/BLVCKFlow/
git commit -m "feat(hypr): add BLVCKFlow host config for the ROG Flow Z13

Third host: 13.4in 2560x1600@180 tablet at scale 1.6 (the only fractional
scale Hyprland accepts here that also matches the other hosts' effective
density). Internal panel is \$mainMonitor, unlike BLVCKSmall where it is the
second output — so the manual-rotate binds target \$mainMonitor and F1 is
pointed at the external via \$toggleMonitor.

No lid binds: the Z13 has a detachable cover, not a hinge. The manual-rotate
binds are omitted until the hardware can report its touchscreen id —
auto-rotate autodetects, so automatic rotation works regardless."
```

---

### Task 6: Fix the SSH config gaps

Recon found `ct-chat` missing entirely (`ssh 192.168.3.18` falls back to user `psy` and is denied — invisible until now because `infra` uses `root@<ip>` rather than the aliases), a dead `blvckserver` host, and a work key pinned on a personal machine.

**Files:**
- Modify: `ssh/.ssh/config`

**Depends on:** Task 1.
**Provides:** a host-alias list that works on a fresh machine.

- [ ] **Step 1: Confirm the gap is real**

```bash
ssh -o BatchMode=yes -o ConnectTimeout=5 192.168.3.18 hostname; echo "exit=$?"
ssh -o BatchMode=yes -o ConnectTimeout=5 root@192.168.3.18 hostname; echo "exit=$?"
```

Expected: the first fails (permission denied), the second prints `ct-chat`.

- [ ] **Step 2: Confirm `blvckserver` is dead before removing it**

```bash
nc -z -w3 192.168.3.4 22; echo "22 exit=$?"; nc -z -w3 192.168.3.4 123; echo "123 exit=$?"
```

Expected: both non-zero (no route to host). That machine was decomposed into the CT fleet.

- [ ] **Step 3: Add the `ct-chat` alias**

Append to `ssh/.ssh/config`, using tabs to match the majority style:

```
Host ct-chat
	HostName 192.168.3.18
	User root
```

- [ ] **Step 4: Remove the dead `blvckserver` block**

Delete these six lines:

```
Host blvckserver
	HostName 192.168.3.4
	User psy
	Port 123
	ControlMaster auto
	ControlPath ~/.ssh/sockets/%r@%h-%p
	ControlPersist 12h
```

- [ ] **Step 5: Unpin the work key from bitbucket.org**

`~/.ssh/id_travelware` is a work key and has no business on a personal machine. Remove the two lines from the `bitbucket.org` block:

```
	IdentityFile ~/.ssh/id_travelware
	IdentitiesOnly yes
```

- [ ] **Step 6: Verify the new alias resolves**

```bash
cd ~/dotfiles && stow -R ssh && ssh -o BatchMode=yes -o ConnectTimeout=5 ct-chat hostname
```

Expected: `ct-chat`.

- [ ] **Step 7: Commit**

```bash
cd ~/dotfiles
git add ssh/.ssh/config
git commit -m "fix(ssh): add ct-chat, drop dead blvckserver and the work key pin

ct-chat was never added — 'ssh 192.168.3.18' falls back to user psy and is
denied. It went unnoticed because the infra CLI uses root@<ip> from its
embedded fleet snapshot rather than these aliases.

blvckserver (192.168.3.4) was decomposed into the CT fleet and no longer
answers on either port. And bitbucket.org pinned id_travelware, a work key
that should not be on a personal machine."
```

---

### Task 7: Package manifests and bootstrap script

The dotfiles CLAUDE.md records required packages as prose, which is already incomplete. BLVCKMain and BLVCKSmall share 156 explicit packages; 165 are desktop-only and 49 laptop-only.

**Files:**
- Create: `system/packages/core.txt`, `system/packages/laptop.txt`, `system/packages/desktop.txt`, `system/packages/aur.txt`
- Create: `system/bootstrap.sh`

**Depends on:** Task 5 (host config must exist for the symlink step).
**Provides:** `system/bootstrap.sh`, run by Task 17.

- [ ] **Step 1: Generate the manifests from the two live hosts**

```bash
cd ~/dotfiles && mkdir -p system/packages
ssh blvckmain 'pacman -Qqe' | sort > /tmp/main-pkgs.txt
pacman -Qqe | sort > /tmp/small-pkgs.txt
pacman -Qqm | sort > /tmp/small-aur.txt
ssh blvckmain 'pacman -Qqm' | sort > /tmp/main-aur.txt

# core = installed on BOTH hosts, minus anything from the AUR
comm -12 /tmp/main-pkgs.txt /tmp/small-pkgs.txt | comm -23 - <(cat /tmp/main-aur.txt /tmp/small-aur.txt | sort -u) > system/packages/core.txt
# laptop = BLVCKSmall-only, repo packages
comm -13 /tmp/main-pkgs.txt /tmp/small-pkgs.txt | comm -23 - /tmp/small-aur.txt > system/packages/laptop.txt
# desktop = BLVCKMain-only, repo packages
comm -23 /tmp/main-pkgs.txt /tmp/small-pkgs.txt | comm -23 - /tmp/main-aur.txt > system/packages/desktop.txt
# aur = union of both hosts' foreign packages, minus -debug artefacts
cat /tmp/main-aur.txt /tmp/small-aur.txt | sort -u | grep -v -- '-debug$' > system/packages/aur.txt

wc -l system/packages/*.txt
```

Expected: four non-empty files; `core.txt` around 150 lines.

- [ ] **Step 2: Strip packages that no longer exist**

`libva-mesa-driver` and `mesa-vdpau` were folded into `mesa` and will fail the install. Remove them if the generation picked them up:

```bash
cd ~/dotfiles
sed -i '/^libva-mesa-driver$/d;/^mesa-vdpau$/d' system/packages/*.txt
grep -c '' system/packages/core.txt
```

- [ ] **Step 3: Add the Z13-specific packages to `laptop.txt`**

Append (each is verified to exist and to be needed on this hardware):

```
asusctl
iio-sensor-proxy
brightnessctl
amd-debug-tools
sbctl
linux-lts
linux-lts-headers
```

- [ ] **Step 4: Create `system/bootstrap.sh`**

```bash
#!/bin/bash
# Bootstrap a fresh host from the dotfiles repo.
# Usage: ./bootstrap.sh <core|laptop|desktop> [more sets...]
#
# Installs repo packages from system/packages/<set>.txt, then AUR packages,
# then stows every package directory, then creates the host symlink.
# Idempotent — safe to re-run.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="$(hostname)"

if [[ $EUID -eq 0 ]]; then
    echo "Run as your normal user, not root (it calls sudo where needed)."
    exit 1
fi

if [[ $# -eq 0 ]]; then
    echo "Usage: $0 <core|laptop|desktop> [more sets...]"
    exit 1
fi

# --- repo packages ---
pkgs=()
for set in "$@"; do
    file="$REPO/system/packages/$set.txt"
    [[ -f "$file" ]] || { echo "No such package set: $set"; exit 1; }
    mapfile -t -O "${#pkgs[@]}" pkgs < "$file"
done
echo "==> installing ${#pkgs[@]} repo packages"
sudo pacman -S --needed --noconfirm "${pkgs[@]}"

# --- AUR packages ---
if ! command -v paru >/dev/null && ! command -v yay >/dev/null; then
    echo "==> no AUR helper found; skipping system/packages/aur.txt"
else
    helper="$(command -v paru || command -v yay)"
    mapfile -t aur < "$REPO/system/packages/aur.txt"
    echo "==> installing ${#aur[@]} AUR packages with $helper"
    "$helper" -S --needed --noconfirm "${aur[@]}"
fi

# --- stow ---
echo "==> stowing"
cd "$REPO"
for d in */; do
    d="${d%/}"
    case "$d" in
        docs|system) continue ;;
    esac
    stow -R "$d"
done

# --- host symlink (gitignored, machine-local) ---
hostconf="$REPO/hypr/.config/hypr/hosts/$HOST.conf"
if [[ -f "$hostconf" ]]; then
    ln -sfn "$HOST.conf" "$REPO/hypr/.config/hypr/hosts/current.conf"
    echo "==> host symlink: current.conf -> $HOST.conf"
else
    echo "!! no host config for $HOST — create hypr/.config/hypr/hosts/$HOST.conf"
    exit 1
fi

echo
echo "Done. Remaining manual steps:"
echo "  sudo ./system/install.sh          # /etc configs"
echo "  sh ~/scripts/wallpaper.sh <image> # generates ags/_colors.scss"
```

- [ ] **Step 5: Make it executable and syntax-check both scripts**

```bash
cd ~/dotfiles && chmod +x system/bootstrap.sh && bash -n system/bootstrap.sh && echo "bootstrap.sh OK"
```

Expected: `bootstrap.sh OK`.

- [ ] **Step 6: Dry-run the package resolution without installing**

```bash
cd ~/dotfiles
mapfile -t p < system/packages/core.txt
pacman -Sp --print-format '%n' "${p[@]}" >/dev/null && echo "core.txt fully resolvable"
```

Expected: `core.txt fully resolvable`. If pacman names an unknown package, remove it from the manifest — it is an AUR or dropped package that leaked into the repo list.

- [ ] **Step 7: Commit**

```bash
cd ~/dotfiles
git add system/packages/ system/bootstrap.sh
git commit -m "feat(system): capture package manifests and a bootstrap script

The 'packages required on each host' list in CLAUDE.md was prose and already
incomplete. These manifests are generated from what the two live hosts
actually have: core is the 156-package intersection, laptop and desktop are
the role-specific remainders, aur is the union of foreign packages.

bootstrap.sh installs a chosen set of manifests, stows every package dir, and
creates the gitignored host symlink — so host #4 is a procedure rather than
archaeology."
```

---

### Task 8: Update the dotfiles CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (in `~/dotfiles`)

**Depends on:** Tasks 2, 3, 5, 7.
**Provides:** accurate guidance for future sessions in that repo.

- [ ] **Step 1: Add BLVCKFlow to the Hosts section**

Replace the two-item list:

```markdown
## Hosts

- **BLVCKSmall** — 2-in-1 touchscreen laptop, Hyprland on Wayland
- **BLVCKMain** — desktop workstation (dual 2560x1440@180 monitors), audio production rig (Evo8 interface, Carla, qpwgraph)
```

with:

```markdown
## Hosts

- **BLVCKSmall** — 2-in-1 touchscreen laptop, Hyprland on Wayland
- **BLVCKMain** — desktop workstation (dual 2560x1440@180 monitors), audio production rig (Evo8 interface, Carla, qpwgraph)
- **BLVCKFlow** — ASUS ROG Flow Z13 (2025, GZ302, Strix Halo). Convertible tablet with a **detachable** keyboard cover — no hinge, so no `Lid Switch` binds. 13.4" 2560x1600@180 internal panel at **scale 1.6**, and unlike BLVCKSmall the internal panel is `$mainMonitor`. Primary machine; dual-boots Windows with Secure Boot enabled (sbctl custom keys).
```

- [ ] **Step 2: Document capability flags**

In the "Desktop shell — AGS v3.1.0" section, replace the `host.ts` bullet:

```markdown
- `host.ts` — exports `isBLVCKSmall` / `isBLVCKMain` for host-conditional code
```

with:

```markdown
- `host.ts` — exports `isBLVCKMain` / `isBLVCKSmall` / `isBLVCKFlow` **and the capability flags `hasBacklight` / `hasRotation` / `hasOSK`**. Widgets MUST branch on the capabilities, never on host identity — a new host should only ever edit this file. The `: [{ as: ... }] as any` stubs in `ControlPanel.tsx` exist so hosts without a backlight or accelerometer never spawn the 3-second shell pollers.
```

- [ ] **Step 3: Replace the prose package list**

Replace the final bullet of the Gotchas section:

```markdown
- **Packages required on each host**: `stow ags mako matugen dart-sass wvkbd hyprgrass-git hyprpaper clipse-wayland-bin hypridle hyprlock brightnessctl nm-connection-editor blueman`. BLVCKSmall also needs `wvkbd-mobintl`, `iio-sensor-proxy` (for auto-rotate).
```

with:

```markdown
- **Packages** live in `system/packages/{core,laptop,desktop,aur}.txt`, installed by `system/bootstrap.sh <sets...>`. Regenerate from live hosts rather than editing by hand. Note `wvkbd-mobintl` is **not** a package name — it is the binary built by the AUR `wvkbd` package. `libva-mesa-driver` and `mesa-vdpau` no longer exist (folded into `mesa`).
```

- [ ] **Step 4: Note the lidless-host behaviour of the shared scripts**

In the Scripts section, replace the `lid.sh` bullet:

```markdown
- `lid.sh` — BLVCKSmall lid handling: `close` disables eDP-1 only in clamshell (never the last output), `open` re-enables it, `resume` is hypridle's `after_sleep_cmd` safety net (re-enables eDP-1 if the lid-open switch event was lost during wake)
```

with:

```markdown
- `lid.sh` — `close` disables eDP-1 only in clamshell (never the last output), `open` re-applies monitors, `resume` is hypridle's `after_sleep_cmd` safety net. **`resume` runs on every host** because `hypridle.conf` is shared — so a missing `/proc/acpi/button/lid/LID0` counts as "open" and `enable_edp()` calls `hyprctl reload` rather than a hardcoded modeline. Touch device is autodetected (override with `$LID_TOUCH`), as it is in `auto-rotate.sh` (`$ROTATE_TOUCH` / `$ROTATE_MONITOR`).
```

- [ ] **Step 5: Commit**

```bash
cd ~/dotfiles
git add CLAUDE.md
git commit -m "docs: add BLVCKFlow, capability flags and the package manifests"
```

- [ ] **Step 6: Push everything**

```bash
cd ~/dotfiles && git push
```

Expected: all Phase 1 dotfiles commits on the remote, so the Z13 can clone them.

---

### Task 9: Register BLVCKFlow in the infra repo

**Files:**
- Modify: `/home/psy/Documents/personal/infra/CLAUDE.md`

**Depends on:** nothing (parallel to Tasks 1–8).
**Provides:** the fleet inventory entry.

**This repo is public and sanitized** — no tailnet suffix, real domain, or public IP in this entry.

- [ ] **Step 1: Add the device block**

In `## Network & Devices`, immediately after the `### blvckmain (Main PC)` block, insert:

```markdown
### blvckflow (ROG Flow Z13)
- **Hostname:** BLVCKFlow (MagicDNS `blvckflow` on the tailnet; no Pi-hole record — workstations are not in DNS)
- **User:** psy
- **SSH:** Port 22, key-based auth (no password)
- **Hardware:** ASUS ROG Flow Z13 (2025, GZ302), Ryzen AI Max "Strix Halo", 64GB LPDDR5X (soldered), 1TB NVMe
- **Role:** Portable primary workstation. Merges the BLVCKMain and BLVCKSmall roles.
- **OS:** Dual boot — Windows 11 (200GB, retained for Vanguard-gated games) + CachyOS (~665GB root, ext4).
- **Boot:** systemd-boot with a **1GB XBOOTLDR** partition at `/boot`; the factory ESP at `/efi` is untouched and still holds the Windows bootloader. **Secure Boot is ENABLED** with custom keys via `sbctl` (Microsoft keys retained) — Vanguard requires it, and Secure Boot is firmware-global so it cannot be off for Linux only. `systemd-boot-manager` is deliberately **removed** (it only knows the ESP path and fails on every kernel update in this layout); `systemd-boot-update.service` is enabled in its place, and loader entries are hand-written.
- **Known hardware gaps:** no working camera (AMD ISP4 not upstream, no OV05C10 driver), no fingerprint reader on this model, no tablet-mode events on cover detach. Sustained inference load can trigger an EC power cut. See `docs/superpowers/specs/2026-08-09-blvckflow-z13-setup-design.md`.
```

- [ ] **Step 2: Add it to the Network Layout diagram**

In the ``` ``` ``` block under `## Network Layout`, insert after the `Termux (phone)` group and before `blvckmain (main PC)`:

```
blvckflow (ROG Flow Z13)
  ├── ssh blvckmain      → 192.168.1.110:22  (psy, key auth)
  └── (all ct-* below, same aliases as blvckmain)
```

- [ ] **Step 3: Verify no secrets leaked into the diff**

The real values behind CLAUDE.md's placeholders live in the gitignored `CLAUDE.local.md`. Read them from there at runtime — never write them into this file, which is committed to the public repo.

```bash
cd /home/psy/Documents/personal/infra
git diff CLAUDE.md > /tmp/blvckflow-doc.diff

# Right-hand column of CLAUDE.local.md's placeholder table = the real values.
awk -F'|' '/^\| `<[A-Z_]+>`/ { gsub(/[`[:space:]]/, "", $3); if (length($3) > 5) print $3 }' CLAUDE.local.md \
  | while read -r secret; do
      grep -qF -- "$secret" /tmp/blvckflow-doc.diff && echo "LEAK: $secret"
    done

# Plus the generic tailnet shapes, which reveal nothing on their own.
grep -nE 'ts\.net|tail[0-9a-f]{6,}' /tmp/blvckflow-doc.diff && echo "LEAK: tailnet identifier"

echo "-- scan complete: any LEAK line above blocks the commit --"
rm -f /tmp/blvckflow-doc.diff
```

Expected: only the `-- scan complete --` line, with no `LEAK:` lines above it.

- [ ] **Step 4: Commit**

```bash
cd /home/psy/Documents/personal/infra
git add CLAUDE.md
git commit -m "docs: register blvckflow in the fleet inventory

Third workstation. Records the two things that will not be obvious later:
Secure Boot is enabled with custom sbctl keys because Vanguard requires it,
and systemd-boot-manager is deliberately removed because it only knows the
ESP path and cannot see an XBOOTLDR /boot."
```

---

## Phase 2 — The machine (on the Z13)

> Everything from here runs on the new hardware. **Have a USB keyboard on hand**: the ITE keyboard controller on this model can wedge into bootloader mode, which also blocks BIOS entry. `systemctl reboot --firmware-setup` from Linux is the escape hatch that needs no function key.

### Task 10: Windows-side preparation

This runs entirely in Windows, before anything is repartitioned. Doing these in the wrong order is how the Windows install gets destroyed.

**Depends on:** nothing.
**Provides:** a shrunk, unencrypted, updated Windows and 765 GiB of free space.

- [ ] **Step 1: Complete setup and update fully**

Finish OOBE, then run Windows Update repeatedly until it reports no further updates. Reboot as prompted.

- [ ] **Step 2: Update BIOS and all firmware**

Open MyASUS / Armoury Crate → Customer Support → Live Update. Apply every firmware update offered, including the keyboard/EC firmware. Do this now, while Windows is pristine and full-size — an ASUS BIOS update later will also reassert Windows Boot Manager as `BootOrder[0]`.

- [ ] **Step 3: Export the BitLocker recovery key, then turn encryption OFF**

Windows 11 seals the volume key to the TPM with Secure Boot state as part of the measurement. Repartitioning and key enrolment both break that seal. Suspending is not enough for a permanent dual boot — decrypt fully.

```
Settings → Privacy & security → Device encryption
```

If present: first use *Back up your recovery key* → save to file **and** to the Microsoft account, then toggle Device encryption **Off** and wait for decryption to complete. On Pro editions use `manage-bde -status C:` to confirm `Percentage Encrypted: 0.0%`.

Verify in an elevated PowerShell:

```powershell
manage-bde -status C:
```

Expected: `Conversion Status: Fully Decrypted` and `Protection Status: Protection Off`.

- [ ] **Step 4: Disable Fast Startup and hibernation**

Fast Startup leaves NTFS dirty, which corrupts shared access and can damage the filesystem when Linux touches it. In an elevated PowerShell:

```powershell
powercfg /h off
```

Verify:

```powershell
powercfg /a
```

Expected: hibernation listed as unavailable.

- [ ] **Step 5: Set the iGPU VRAM carveout**

The dedicated-VRAM reservation is set from **Armoury Crate in Windows**, not the BIOS, and persists into Linux. Armoury Crate → Settings → GPU / Memory allocation.

Set it **low (0.5GB or 1GB)**. On Linux the GTT limit already defaults to 50% of RAM (~32GB), so a large static carveout only removes memory from the CPU side.

- [ ] **Step 6: Shrink the Windows partition**

Use Windows' own Disk Management (`diskmgmt.msc`) — not a Linux tool — so NTFS metadata stays consistent.

Right-click `C:` → Shrink Volume. Enter the amount to shrink so that `C:` ends at **200 GiB (204800 MB)**.

If Windows refuses to shrink far enough, unmovable files are in the way: disable System Restore, delete restore points, run `Disk Cleanup` including system files, then retry. Reboot and retry once more before resorting to defragmentation.

- [ ] **Step 7: Verify the free space**

In Disk Management, confirm roughly **765 GiB unallocated** after `C:` and the recovery partition.

- [ ] **Step 8: Note the current boot entry order**

In an elevated PowerShell:

```powershell
bcdedit /enum firmware | Select-String -Pattern "identifier|description"
```

Record the output. If the boot order is ever reset by a Windows or BIOS update, this is the reference for restoring it.

---

### Task 11: Live ISO recon and partitioning

**Depends on:** Task 10.
**Provides:** three new partitions and a GPT backup.

- [ ] **Step 1: Boot the CachyOS live ISO**

Write the current CachyOS ISO to a USB stick from another machine. Boot the Z13 with `Esc` held to reach the firmware boot menu.

This is the one point in the plan where a working keyboard at firmware stage is mandatory — there is no Linux installed yet, so the `systemctl reboot --firmware-setup` escape hatch does not exist. If the cover keyboard is unresponsive, use the USB keyboard.

If the ISO refuses to boot with Secure Boot on, disable it temporarily in the BIOS. Task 14 re-enables it, and the BitLocker seal is already gone from Task 10 Step 3, so toggling it now costs nothing.

- [ ] **Step 2: Confirm the sector size and factory ESP size**

The spec assumes a ~260MB ESP and 512b sectors. Both must be checked before sizing anything.

```bash
lsblk -o NAME,SIZE,PHY-SEC,LOG-SEC,FSTYPE,PARTTYPENAME /dev/nvme0n1
```

Expected: `nvme0n1p1` around 260M, `vfat`, `EFI System`; `LOG-SEC` 512. If the ESP is 100M that is still fine (the sd-boot binary is ~150KB). Record every partition number — later steps assume p1 = ESP.

- [ ] **Step 3: Confirm the touchscreen and WiFi work in the live environment**

Without WiFi there is no mirror, no Tailscale, and no fleet.

```bash
ip link | grep -E 'wl|enp'
nmcli device wifi list | head -5
```

Expected: a wireless interface present and networks listed. Connect with `nmtui`.

Touch the screen and confirm the pointer moves. If touch is dead in the live ISO, stop and investigate before installing — this is the machine's primary input.

- [ ] **Step 4: Back up the partition table**

The single cheapest insurance in this plan. One line, and it makes the type-code flip in Task 13 trivially reversible.

```bash
sfdisk --dump /dev/nvme0n1 > /root/gpt-before.bak
cat /root/gpt-before.bak
```

Copy this file to the USB stick or another machine — `/root` on the live ISO is a tmpfs and vanishes on reboot.

- [ ] **Step 5: Create the three new partitions**

`sgdisk` is not installed on CachyOS; `sfdisk` is. The boot partition is created as an **ESP type for now** so Calamares will accept it as its boot target; Task 13 flips it to XBOOTLDR after the install.

```bash
sfdisk --append /dev/nvme0n1 <<'EOF'
size=1G, type=uefi, name="XBOOTLDR"
size=64G, type=swap, name="swap"
type=linux, name="cachyos-root"
EOF
```

- [ ] **Step 6: Verify the resulting layout**

```bash
partprobe /dev/nvme0n1
lsblk -o NAME,SIZE,PARTTYPENAME /dev/nvme0n1
```

Expected: seven partitions. p5 = 1G `EFI System`, p6 = 64G `Linux swap`, p7 = ~665G `Linux filesystem`. Record the exact device names — if the factory layout had a different partition count, these numbers shift and **every later step must use the real numbers**.

---

### Task 12: CachyOS install (Calamares, stage 1)

Calamares cannot produce an XBOOTLDR layout — confirmed by a CachyOS developer on their own forum. So it installs normally with p5 as a conventional ESP, and Task 13 converts. This ordering is deliberate: Calamares writes `loader/entries` onto p5, which is exactly where they belong in the final layout, so they survive the conversion untouched.

**Depends on:** Task 11.
**Provides:** a booting CachyOS on p7.

- [ ] **Step 1: Launch the installer and choose manual partitioning**

Start Calamares. At the partitioning screen choose **Manual partitioning**. Do not choose "Install alongside" — it will make its own decisions about the ESP.

- [ ] **Step 2: Assign mount points**

| Partition | Action |
|---|---|
| `nvme0n1p1` (factory ESP) | **Leave completely alone.** No mount point, no format, no flags |
| `nvme0n1p3` (Windows C:) | Leave alone |
| `nvme0n1p4` (WinRE) | Leave alone |
| `nvme0n1p5` (1G) | Mount `/boot`, format **FAT32**, set the `boot`/`esp` flag |
| `nvme0n1p6` (64G) | Format **linuxswap** |
| `nvme0n1p7` (~665G) | Mount `/`, format **ext4** |

Double-check p1 has no mount point assigned before continuing. Formatting it destroys the Windows bootloader.

- [ ] **Step 3: Set the hostname exactly**

Hostname: `BLVCKFlow` — capital B-L-V-C-K, capital F. `ags/lib/host.ts` string-compares against this.

Username: `psy` (must match — several scripts hardcode `/home/psy`).

- [ ] **Step 4: Complete the install and reboot**

Let Calamares finish. Reboot when prompted, removing the USB stick.

- [ ] **Step 5: Verify both operating systems boot**

At the systemd-boot menu, confirm both a CachyOS entry and a Windows entry appear. Boot CachyOS first, then reboot and boot Windows, confirming it still reaches the desktop without asking for a BitLocker key.

- [ ] **Step 6: Record the installed state**

Back in CachyOS:

```bash
bootctl status | head -20
ls -la /boot/loader/entries/
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT /dev/nvme0n1
cat /etc/fstab
```

Record the exact loader entry filenames — Task 13 needs them, and CachyOS's naming varies between releases (`cachyos.conf`, `linux-cachyos.conf`, …).

---

### Task 13: Convert to XBOOTLDR

Flips p5 from ESP to XBOOTLDR, mounts the factory ESP at `/efi`, reinstalls sd-boot across the split, and removes the CachyOS boot manager that cannot work in this layout.

**Depends on:** Task 12.
**Provides:** the final boot architecture.

- [ ] **Step 1: Remove `systemd-boot-manager` first**

It is installed unconditionally by Calamares and hooked to every kernel and systemd upgrade. It only knows `bootctl -p` (the ESP), so once `/boot` is XBOOTLDR it dies on its own `loader/entries` existence check — a pacman hook that fails on every kernel update forever.

```bash
sudo pacman -Rns systemd-boot-manager
```

- [ ] **Step 2: Enable the replacement update mechanism**

Removing that package removes the only thing on CachyOS that runs `bootctl update`. The systemd unit that should do it ships with a preset but presets only apply at initial enablement — it is **disabled** on a real CachyOS install. (BLVCKSmall is living proof: sd-boot 257.4 running under systemd 261.)

```bash
sudo systemctl enable systemd-boot-update.service
systemctl is-enabled systemd-boot-update.service
```

Expected: `enabled`.

- [ ] **Step 3: Install the LTS kernel now, while entries are still simple**

`linux-lts` (currently 6.18.37) is well past gfx1151 enablement and is a credible rescue kernel on new silicon. With `systemd-boot-manager` gone, its loader entry must be hand-written — done in Step 9.

```bash
sudo pacman -S linux-lts linux-lts-headers
ls -la /boot/vmlinuz-* /boot/initramfs-*
```

Expected: `vmlinuz-linux-cachyos`, `initramfs-linux-cachyos.img`, `vmlinuz-linux-lts`, `initramfs-linux-lts.img`.

- [ ] **Step 4: Back up the fallback loader before it is overwritten**

`bootctl install` overwrites `EFI/BOOT/BOOTX64.EFI`, which may be Microsoft's version.

```bash
sudo mkdir -p /efi
sudo mount /dev/nvme0n1p1 /efi
sudo cp -a /efi/EFI/BOOT/BOOTX64.EFI /root/BOOTX64.EFI.factory.bak 2>/dev/null || echo "no fallback loader present"
sudo ls -la /efi/EFI/
```

Expected: an `EFI/Microsoft` directory. Confirm `/efi/EFI/Microsoft/Boot/bootmgfw.efi` exists — this is what sd-boot auto-detects for the Windows entry.

- [ ] **Step 5: Flip the partition type to XBOOTLDR**

```bash
sudo sfdisk --part-type /dev/nvme0n1 5 bc13c2ff-59e6-4262-a352-b275fd6f7172
sudo partprobe /dev/nvme0n1
lsblk -o NAME,SIZE,PARTTYPENAME /dev/nvme0n1
```

Expected: p5 now reads `Linux extended boot`. If this goes wrong, restore with `sfdisk /dev/nvme0n1 < /root/gpt-before.bak` (the backup from Task 11 Step 4).

- [ ] **Step 6: Rewrite `/etc/fstab` for the split**

Explicit fstab entries for both paths also disable `systemd-gpt-auto-generator` for both partitions, which is what we want.

```bash
blkid /dev/nvme0n1p1 /dev/nvme0n1p5 /dev/nvme0n1p6 /dev/nvme0n1p7
```

Edit `/etc/fstab`: change the existing `/boot` line to use the p5 UUID mounted at `/boot`, and add an `/efi` line for p1. Both are vfat:

```
UUID=<p1-uuid>  /efi   vfat  rw,relatime,fmask=0137,dmask=0027,codepage=437,iocharset=ascii,shortname=mixed,utf8,errors=remount-ro  0 2
UUID=<p5-uuid>  /boot  vfat  rw,relatime,fmask=0137,dmask=0027,codepage=437,iocharset=ascii,shortname=mixed,utf8,errors=remount-ro  0 2
```

Verify:

```bash
sudo systemctl daemon-reload && sudo mount -a && findmnt /efi /boot
```

Expected: both mounted, p1 on `/efi` and p5 on `/boot`.

- [ ] **Step 7: Reinstall sd-boot across the split**

```bash
sudo bootctl --esp-path=/efi --boot-path=/boot install
```

The split is: EFI binaries, `loader.conf` and `random-seed` go to the **ESP**; `loader/entries` and `EFI/Linux` go to **XBOOTLDR**. A `loader.conf` on XBOOTLDR is silently ignored.

- [ ] **Step 8: Clean the stale loader copies off XBOOTLDR**

Calamares wrote sd-boot onto p5 when it was an ESP. Those copies are now dead weight and a source of confusion.

```bash
sudo rm -rf /boot/EFI/BOOT /boot/EFI/systemd
sudo rm -f /boot/loader/loader.conf   # ignored on XBOOTLDR; the real one is on /efi
ls -la /boot /boot/loader /efi/EFI
```

Expected: `/boot` holds `loader/entries/`, the kernels and initramfs; `/efi` holds `EFI/systemd`, `EFI/BOOT`, `EFI/Microsoft`, `loader/loader.conf`.

- [ ] **Step 9: Write the loader entries**

Entry paths are relative to the **partition root**, not to `/boot`. Both entries reference stable filenames that `mkinitcpio` overwrites in place, so kernel *updates* need no regeneration — only adding or removing a kernel package does.

Get the root UUID:

```bash
blkid -s UUID -o value /dev/nvme0n1p7
```

`/boot/loader/entries/cachyos.conf`:

```
title   CachyOS
linux   /vmlinuz-linux-cachyos
initrd  /initramfs-linux-cachyos.img
options root=UUID=<p7-uuid> rw
```

`/boot/loader/entries/cachyos-lts.conf`:

```
title   CachyOS (LTS)
linux   /vmlinuz-linux-lts
initrd  /initramfs-linux-lts.img
options root=UUID=<p7-uuid> rw
```

Do **not** add a separate `initrd /amd-ucode.img` line — microcode is bundled by the `microcode` mkinitcpio hook. Delete any Calamares-generated entries that duplicate these.

- [ ] **Step 10: Write `loader.conf` on the ESP**

`default` names an explicit entry rather than `@saved`: this is a tablet, the boot menu is not touch-driven, and a single Windows boot with `@saved` would make Windows the default with possibly no usable input device to change it back. `console-mode auto` because firmware text mode on a 2560x1600 panel is frequently unreadable.

`/efi/loader/loader.conf`:

```
default cachyos.conf
timeout 10
console-mode auto
editor no
```

- [ ] **Step 11: Verify before rebooting**

```bash
bootctl status
sudo bootctl list
sudo efibootmgr -v
```

Check: `Current Boot Loader` product is the current systemd version; both CachyOS entries listed; a Windows entry present or `/efi/EFI/Microsoft/Boot/bootmgfw.efi` exists (`bootctl list` cannot show auto-detected entries from a non-booted state — assert the file instead). In `efibootmgr -v`, look for a **stale entry pointing at p5** left over from Calamares and delete it with `sudo efibootmgr -b <num> -B`.

- [ ] **Step 12: Reboot and confirm both OSes still boot**

Expected: the menu shows CachyOS, CachyOS (LTS), and Windows Boot Manager. Boot each in turn.

- [ ] **Step 13: Verify the cmdline took effect**

```bash
cat /proc/cmdline
```

Expected: `root=UUID=… rw` — read from the running kernel, never from the config file.

---

### Task 14: Enrol Secure Boot keys

**Depends on:** Task 13.
**Provides:** Secure Boot enabled with custom keys, so Vanguard works and Linux still boots.

- [ ] **Step 1: Install sbctl and check the current state**

```bash
sudo pacman -S --needed sbctl
sudo sbctl status
```

Expected: `Setup Mode: Disabled`, `Secure Boot: Disabled` (or Enabled). Key enrolment requires **Setup Mode: Enabled**.

- [ ] **Step 2: Put the firmware into Setup Mode**

Reboot into the BIOS (`systemctl reboot --firmware-setup` avoids needing a function key). Under Security → Secure Boot, choose **Delete all Secure Boot keys** / **Clear Secure Boot keys**, which puts the platform into Setup Mode. Leave Secure Boot itself enabled. Save and exit.

```bash
sudo sbctl status
```

Expected: `Setup Mode: Enabled`.

- [ ] **Step 3: Create and enrol keys — with Microsoft's retained**

The `--microsoft` flag is **mandatory**. Without it, `bootmgfw.efi` stops being trusted and Windows will not boot.

```bash
sudo sbctl create-keys
sudo sbctl enroll-keys --microsoft
sudo sbctl status
```

Expected: `Setup Mode: Disabled`, `Owner GUID` set, `Installed: sbctl is installed`.

- [ ] **Step 4: Sign every EFI binary in the boot path**

`-s` records the file in sbctl's database so `sign-all` can re-sign it later.

```bash
sudo sbctl sign -s /efi/EFI/systemd/systemd-bootx64.efi
sudo sbctl sign -s /efi/EFI/BOOT/BOOTX64.EFI
sudo sbctl sign -s /boot/vmlinuz-linux-cachyos
sudo sbctl sign -s /boot/vmlinuz-linux-lts
sudo sbctl verify
```

Expected: every listed file reports as signed. Windows' own binaries under `/efi/EFI/Microsoft` are signed by Microsoft's key and must **not** be re-signed.

- [ ] **Step 5: Ensure re-signing happens automatically on kernel updates**

Without this, the first kernel update produces an unsigned kernel and the machine stops booting.

```bash
pacman -Ql sbctl | grep -i hook
```

If a hook is listed, it is handled. If not, create `/etc/pacman.d/hooks/95-sbctl-sign.hook`:

```ini
[Trigger]
Type = Path
Operation = Install
Operation = Upgrade
Target = usr/lib/modules/*/vmlinuz
Target = usr/lib/systemd/boot/efi/*
Target = boot/vmlinuz-*

[Action]
Description = Re-signing EFI binaries for Secure Boot...
When = PostTransaction
Exec = /usr/bin/sbctl sign-all
Depends = sbctl
```

- [ ] **Step 6: Re-enable Secure Boot in firmware and verify end to end**

Reboot into the BIOS, ensure Secure Boot is **Enabled**, save and exit.

```bash
sudo sbctl status
bootctl status | grep -i "secure boot"
```

Expected: `Secure Boot: Enabled (user)` — matching BLVCKMain.

- [ ] **Step 7: Verify Windows and League still work**

Boot Windows. Confirm it reaches the desktop, then in PowerShell:

```powershell
Confirm-SecureBootUEFI
```

Expected: `True`. Launch League of Legends and confirm Vanguard initialises without VAN9001.

- [ ] **Step 8: Prove the signing hook works before trusting it**

```bash
sudo pacman -S --needed linux-lts && sudo sbctl verify
```

Expected: all files still signed after a kernel package transaction.

---

### Task 15: Hibernation

**Depends on:** Task 14 (loader entries must be signed after any change).
**Provides:** working `systemctl hibernate`.

- [ ] **Step 1: Confirm the swap partition is active and correctly sized**

```bash
swapon --show
free -h
```

Expected: a 64G partition swap active. zram may also be listed — that is fine and needs no change. systemd deliberately skips zram when choosing a hibernation target.

- [ ] **Step 2: Confirm the initramfs uses the systemd hook**

```bash
grep '^HOOKS' /etc/mkinitcpio.conf
```

Expected: `systemd` present in the array. `systemd-hibernate-resume-generator` then handles `resume=` — do **not** add the busybox `resume` hook, which belongs to a different initramfs path.

- [ ] **Step 3: Add `resume=` to both loader entries**

```bash
blkid -s UUID -o value /dev/nvme0n1p6
```

Append to the `options` line of both `/boot/loader/entries/cachyos.conf` and `/boot/loader/entries/cachyos-lts.conf`:

```
options root=UUID=<p7-uuid> rw resume=UUID=<p6-uuid>
```

No `resume_offset` — that is a swapfile concern, not a partition one.

- [ ] **Step 4: Confirm the platform supports it**

```bash
cat /sys/power/disk
cat /sys/power/state
```

Expected: `/sys/power/state` includes `disk`, and `/sys/power/disk` offers `shutdown`.

- [ ] **Step 5: Reboot and verify the cmdline**

```bash
sudo reboot
# after login:
cat /proc/cmdline
```

Expected: both `root=UUID=` and `resume=UUID=` present.

- [ ] **Step 6: Test hibernation from a TTY, not the desktop**

Switch to a TTY (`Ctrl+Alt+F3`) before testing, so a graphical failure cannot mask the result.

```bash
sudo systemctl hibernate
```

Power the machine back on. Expected: the session resumes with previously-running processes intact. Confirm:

```bash
journalctl -b 0 | grep -i "hibernat\|resume" | head -20
uptime
```

Expected: `uptime` shows time spanning the hibernation, and the journal shows a resume rather than a fresh boot.

---

### Task 16: Hardware enablement

**Depends on:** Task 15.
**Provides:** working GPU acceleration, audio, power management and battery limiting.

- [ ] **Step 1: Establish the baseline — what actually works**

```bash
# GPU
vainfo 2>&1 | head -20
# Audio
wpctl status | head -30
aplay -l
# Wireless
nmcli device status
bluetoothctl show | head -5
# Sleep states
cat /sys/power/mem_sleep
# Panel
sudo pacman -S --needed drm_info && drm_info | grep -iE 'panel|edid|make|model' | head
```

Record everything. Expected: `vainfo` names gfx1151 and lists AV1 Profile 0; `aplay -l` shows an ALC294 card; `mem_sleep` shows `[s2idle]`.

If audio output is silent, check the codec subsystem id before assuming a kernel bug — both GZ302 quirks are in mainline 7.1:

```bash
cat /proc/asound/card*/codec* | grep -i "Subsystem Id"
```

- [ ] **Step 2: Confirm the ASUS platform driver binds**

This is the single precondition for `asusd` ever starting — the udev rule keys on DMI strings and the `asus-nb-wmi` driver.

```bash
cat /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_family /sys/class/dmi/id/product_name
ls /sys/bus/platform/drivers/asus-nb-wmi/
```

Expected: vendor `ASUSTeK COMPUTER INC.`, and a device symlinked under the `asus-nb-wmi` driver directory.

- [ ] **Step 3: Install asusctl from the CachyOS repo**

It is in the default-enabled `[cachyos]` repo. No third-party repo, no GPG import. Do **not** add the `[ogc]` repo — its builds sort as newer and would silently take over on the next `-Syu`, bringing a `tlp` conflict with them.

```bash
pacman -Ss '^asusctl$'
sudo pacman -S asusctl
asusctl -v
```

Expected: version 6.3.11 or newer, with no version-mismatch warning.

- [ ] **Step 4: Resolve the platform_profile ownership conflict**

`asusd` and `power-profiles-daemon` race over `/sys/firmware/acpi/platform_profile` and CPU EPP. PPD must be **masked** — it is D-Bus activated and merely disabling it lets it restart itself.

```bash
sudo systemctl mask --now power-profiles-daemon.service
systemctl is-enabled power-profiles-daemon.service
systemctl --user enable --now asusd-user.service
systemctl status asusd.service --no-pager | head -10
```

Expected: PPD reports `masked`; `asusd.service` is active. Note `systemctl enable asusd.service` **fails** — that unit has no `[Install]` section and is udev + D-Bus activated, so it needs no enabling.

- [ ] **Step 5: Set and verify the battery charge limit**

`asusd` persists this in `/etc/asusd/asusd.ron` and re-applies it on boot, so no custom systemd unit is needed.

```bash
asusctl battery limit 80
cat /sys/class/power_supply/BAT*/charge_control_end_threshold
```

Expected: `80`.

- [ ] **Step 6: Confirm amd-pstate is already correct**

```bash
cat /sys/devices/system/cpu/amd_pstate/status
```

Expected: `active`. Do not force `guided` or `passive` — active/EPP is the default on Zen 5 and the better choice here.

- [ ] **Step 7: Verify suspend, and diagnose properly if it misbehaves**

```bash
sudo systemctl suspend
# wake the machine, then:
sudo pacman -S --needed amd-debug-tools
sudo amd_s2idle --duration 60
```

Expected: the machine resumes with WiFi and display intact. If it does not, `amd_s2idle` names the device that blocked s2idle — use that rather than guessing at unbind workarounds.

- [ ] **Step 8: Confirm the battery survives a suspend cycle**

Suspend for 30 minutes on battery, then check the drain:

```bash
cat /sys/class/power_supply/BAT*/capacity
```

Record before and after. More than a few percent per hour indicates a wake source; investigate with `cat /proc/acpi/wakeup` and disable the culprit's `power/wakeup` via a udev rule.

- [ ] **Step 9: Only now consider workaround parameters**

If — and only if — a specific symptom appeared above, add the matching parameter to the `options` line of both loader entries, re-run `sudo sbctl sign-all`, reboot, and confirm with `cat /proc/cmdline`:

| Symptom | Parameter |
|---|---|
| Panel flicker / artefacts | `amdgpu.dcdebugmask=0x600` |
| Bluetooth wedges and disappears | `usbcore.autosuspend=-1` |
| WiFi drops after resume | `options mt7925e disable_aspm=1` in `/etc/modprobe.d/` (costs idle power) |
| GPU hangs under load | `amdgpu.cwsr_enable=0` |

Add them one at a time. Adding the whole bundle preemptively means debugging a regression you introduced.

---

### Task 17: Desktop bring-up

**Depends on:** Tasks 7 (bootstrap script), 16 (hardware).
**Provides:** the working Hyprland desktop.

- [ ] **Step 1: Install the prerequisites and clone the dotfiles**

```bash
sudo pacman -S --needed git stow base-devel
git clone https://github.com/psychonaut0/dotfile-final.git ~/dotfiles
cd ~/dotfiles && git log --oneline -5
```

Expected: the Phase 1 commits present (capability flags, BLVCKFlow.conf, bootstrap).

- [ ] **Step 2: Install an AUR helper**

```bash
sudo pacman -S --needed paru || { git clone https://aur.archlinux.org/paru-bin.git /tmp/paru && cd /tmp/paru && makepkg -si --noconfirm; }
paru --version
```

- [ ] **Step 3: Run the bootstrap**

```bash
cd ~/dotfiles && ./system/bootstrap.sh core laptop
```

Expected: packages installed, every package directory stowed, and `current.conf -> BLVCKFlow.conf` created. If it exits with "no host config for BLVCKFlow", the hostname is wrong — fix with `sudo hostnamectl set-hostname BLVCKFlow` and re-run.

- [ ] **Step 4: Add the manual-rotate binds now the device id is knowable**

`BLVCKFlow.conf` ships without them because they need the touchscreen id to keep touch input in sync with the rotation.

```bash
hyprctl devices -j | jq -r '.touch[0].name'
```

Insert into `~/dotfiles/hypr/.config/hypr/hosts/BLVCKFlow.conf`, replacing the comment block that explains their absence, using the id printed above:

```
# Monitor transform (sync touch input)
$touch = <id from hyprctl devices>
bind = $mainMod, M, exec, hyprctl keyword monitor $mainMonitor,transform,2 && hyprctl keyword "device[$touch]:transform" 2
bind = $mainMod Shift, M, exec, hyprctl keyword monitor $mainMonitor,transform,0 && hyprctl keyword "device[$touch]:transform" 0
bind = $altMod, M, exec, hyprctl keyword monitor $mainMonitor,transform,2 && hyprctl keyword "device[$touch]:transform" 2
bind = $altMod Shift, M, exec, hyprctl keyword monitor $mainMonitor,transform,0 && hyprctl keyword "device[$touch]:transform" 0
```

Then:

```bash
cd ~/dotfiles && git add hypr/.config/hypr/hosts/BLVCKFlow.conf
git commit -m "feat(hypr): add BLVCKFlow manual-rotate binds"
git push
hyprctl reload
```

Verify with `Super+M` then `Super+Shift+M`: the display rotates and touch input follows.

- [ ] **Step 5: Install the /etc configs**

```bash
cd ~/dotfiles && sudo ./system/install.sh
```

Expected: output lines for the shared configs and `[BLVCKFlow] /etc/sddm.conf.d/10-host.conf`. If the host line is absent, `hostname` does not match the directory name.

- [ ] **Step 6: Generate the theme**

AGS will not start without `~/.config/ags/_colors.scss`, which is gitignored and generated.

```bash
sh ~/scripts/wallpaper.sh ~/Pictures/wallpapers/<some-image>
ls -la ~/.config/ags/_colors.scss
```

Expected: the file exists.

- [ ] **Step 7: Install the hyprgrass plugin**

```bash
hyprpm add https://github.com/horriblename/hyprgrass
hyprpm enable hyprgrass
hyprpm list
```

Expected: hyprgrass listed and enabled.

- [ ] **Step 8: Verify the desktop**

Log out and back in (or reboot). Then:

```bash
hyprctl monitors -j | jq -r '.[] | "\(.name) \(.width)x\(.height)@\(.refreshRate) scale=\(.scale)"'
pgrep -x gjs
```

Expected: `eDP-1 2560x1600@180 scale=1.6` and an AGS PID.

- [ ] **Step 9: Verify the capability-gated widgets appear**

Open the control panel from the bar. Expected: the brightness slider **and** the Rotate toggle are both present (they are gated on `hasBacklight` / `hasRotation`, which are true for BLVCKFlow), and the on-screen keyboard button appears in the bottom pill.

- [ ] **Step 10: Test rotation end to end**

```bash
sh ~/scripts/auto-rotate.sh toggle
```

Physically rotate the tablet. Expected: the display rotates **and touch input follows** — if touch is 90° off, the autodetection in Task 3 failed and `$ROTATE_TOUCH` needs setting explicitly.

- [ ] **Step 11: Confirm no zombie processes accumulate**

The dotfiles CLAUDE.md warns that ungated shell polling piles up zombies. Wait five minutes after login, then:

```bash
ps -eo stat,comm | grep -c '^Z'
```

Expected: `0`.

---

## Phase 3 — Fleet onboarding

### Task 18: Distribute the SSH key

**Depends on:** Task 17.
**Provides:** root SSH from BLVCKFlow to all 16 fleet hosts plus blvckmain.

Two constraints shape this: `ssh-copy-id` from the new machine **cannot** work (`permitrootlogin without-password` fleet-wide, so there is no password path), and the key must be **fresh** — sshd matches the first `authorized_keys` entry for a given key, so reusing an existing fleet key would silently route the new workstation into ct-backup's forced-command dispatcher.

- [ ] **Step 1: Generate a fresh key on BLVCKFlow**

```bash
ssh-keygen -t ed25519 -a 100 -C 'psy@BLVCKFlow' -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

- [ ] **Step 2: Move the public key to an already-authorised host**

From BLVCKFlow (after Task 19 Step 1 if Tailscale is already up, otherwise copy it by hand):

```bash
tailscale file cp ~/.ssh/id_ed25519.pub blvcksmall:
```

On BLVCKSmall:

```bash
tailscale file get /tmp/
ssh-keygen -l -f /tmp/id_ed25519.pub
```

Compare the fingerprint against BLVCKFlow's before proceeding.

- [ ] **Step 3: Load it and confirm it is genuinely new**

On BLVCKSmall:

```bash
NEWKEY="$(cat /tmp/id_ed25519.pub)"
grep -q "$NEWKEY" ~/.ssh/authorized_keys 2>/dev/null && echo 'STOP: key already in use' || echo 'fresh key, ok'
```

Expected: `fresh key, ok`. If it says STOP, generate a different key — do not proceed.

- [ ] **Step 4: Check cluster quorum before touching pmxcfs**

`/etc/pve` is a corosync-replicated FUSE filesystem, **read-only unless the cluster is quorate**, and this is a 2-node cluster needing both votes. If proxmoxnode is down the append silently fails.

```bash
ssh proxmoxmain 'pvecm status | grep -E "Quorate|Total votes"'
```

Expected: `Quorate: Yes`, total votes 2.

- [ ] **Step 5: Append once to cover both Proxmox nodes**

Both nodes' `/root/.ssh/authorized_keys` are symlinks to the same pmxcfs file. Append with `>>` only — never `sed -i`, `install`, or rename-in-place on pmxcfs.

```bash
ssh proxmoxmain "if ! grep -qxF '$NEWKEY' /etc/pve/priv/authorized_keys; then printf '%s\n' '$NEWKEY' >> /etc/pve/priv/authorized_keys; echo added; else echo present; fi"
ssh proxmoxnode "grep -c 'psy@BLVCKFlow' /root/.ssh/authorized_keys"
```

Expected: `added`, then `1` — which proves pmxcfs propagation.

- [ ] **Step 6: Append to each of the 14 CTs**

The cluster file does **not** cover existing CTs; each has its own independent `authorized_keys`.

```bash
for o in 5 6 7 8 9 10 11 12 13 14 15 16 17 18; do
  H=192.168.3.$o; printf '%-14s ' "$H"
  ssh -o BatchMode=yes -o ConnectTimeout=5 "root@$H" \
    "install -d -m 700 /root/.ssh; touch /root/.ssh/authorized_keys; chmod 600 /root/.ssh/authorized_keys; \
     if ! grep -qxF '$NEWKEY' /root/.ssh/authorized_keys; then printf '%s\n' '$NEWKEY' >> /root/.ssh/authorized_keys; echo added; else echo present; fi" \
    || echo FAILED
done
```

Expected: 14 lines, each `added`. Any `FAILED` must be resolved before continuing.

- [ ] **Step 7: Add it to blvckmain**

```bash
ssh blvckmain "install -d -m 700 ~/.ssh; touch ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys; \
  if ! grep -qxF '$NEWKEY' ~/.ssh/authorized_keys; then printf '%s\n' '$NEWKEY' >> ~/.ssh/authorized_keys; echo added; else echo present; fi"
```

- [ ] **Step 8: Prove access from BLVCKFlow**

Back on BLVCKFlow:

```bash
for o in 2 3 5 6 7 8 9 10 11 12 13 14 15 16 17 18; do
  H=192.168.3.$o; printf '%-14s ' "$H"
  ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "root@$H" hostname || echo FAILED
done
ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new blvckmain hostname
```

Expected: 16 hostnames plus `BLVCKMain`, no FAILED.

- [ ] **Step 9: Register the key with GitHub**

Add `~/.ssh/id_ed25519.pub` at https://github.com/settings/keys, then:

```bash
ssh -T git@github.com
```

Expected: `Hi psychonaut0! You've successfully authenticated`.

---

### Task 19: Tailscale and the infra CLI

**Depends on:** Task 17.
**Provides:** off-LAN fleet reachability and the `infra` command.

- [ ] **Step 1: Join the tailnet with the right flags**

`--accept-routes` **defaults to false on Linux**. Omitting it makes the machine functionally offline from the fleet: no route to `192.168.3.0/24`, so no Pi-hole, so no `.lan` name resolves, so no `infra-bin.lan`, so `infra update` fails and `infra` cannot SSH to a single CT.

```bash
sudo pacman -S --needed tailscale
sudo systemctl enable --now tailscaled
sudo tailscale up --hostname=blvckflow --accept-routes --accept-dns --operator=$USER
```

- [ ] **Step 2: Verify the prefs match the fleet's working shape**

```bash
tailscale debug prefs | grep -E 'RouteAll|CorpDNS|WantRunning'
```

Expected: `RouteAll: true`, `CorpDNS: true`, `WantRunning: true` — the same as BLVCKSmall.

- [ ] **Step 3: Verify `.lan` resolution works over the tailnet**

```bash
tailscale dns status | sed -n '/Split DNS Routes/,+4p'
ip route get 192.168.3.5
getent hosts infra-bin.lan
```

Expected: a `lan -> 192.168.3.5` split-DNS route, the route to ct-dns going via `tailscale0`, and `infra-bin.lan` resolving to `192.168.3.12`.

If enrolment succeeded but `192.168.3.x` is unreachable, the subnet routes may need approving for this node in the Tailscale admin console — the ACL policy could not be read during planning.

- [ ] **Step 4: Install the infra CLI into the user path**

`install.sh` defaults to `/usr/local/bin`, which is correct for CTs but wrong here: `infra update` rewrites its own resolved executable path, so a root-owned binary can never self-update as `psy`. It also hard-requires `curl`, `sha256sum` and `python3` — on Arch the `python` package provides the latter.

```bash
sudo pacman -S --needed curl python coreutils
mkdir -p ~/.local/bin
curl -fsSL http://infra-bin.lan/install.sh | INFRA_INSTALL_DIR="$HOME/.local/bin" sh
infra version
```

Expected: the current mirror release (v0.7.3 at time of writing).

- [ ] **Step 5: Set `INFRA_REPO` so the CLI works from any directory**

`infra` locates the repo by walking up from the cwd for a `.git` dir, so it breaks inside *other* git repos — `cd ~/dotfiles && infra ls` fails looking for `~/dotfiles/stacks`.

Add to `~/.zshrc.d/.exports` (or `~/.zshrc` if that file does not exist):

```bash
export INFRA_REPO="$HOME/Documents/personal/infra"
```

- [ ] **Step 6: Clone the infra repo and its out-of-band secrets**

`CLAUDE.local.md` and the Cloudflare token are gitignored and will **not** arrive with a clone.

```bash
mkdir -p ~/Documents/personal
git clone git@github.com:psychonaut0/infra.git ~/Documents/personal/infra
scp blvcksmall:/home/psy/Documents/personal/infra/CLAUDE.local.md ~/Documents/personal/infra/CLAUDE.local.md
mkdir -p ~/.config/infra
scp blvcksmall:~/.config/infra/cloudflare.yml ~/.config/infra/cloudflare.yml
chmod 600 ~/.config/infra/cloudflare.yml
```

Note: `CLAUDE.local.md` records that the current Cloudflare token leaked into a session transcript on 2026-07-28. Prefer minting a fresh `Cloudflare Tunnel:Read` token over copying that one.

- [ ] **Step 7: Exercise the CLI**

```bash
source ~/.zshrc
infra ls
infra status
infra ct status
infra dns ls
infra tunnel diff
```

Expected: the service list, container states across the fleet, the CT overview, 23 DNS records, and `tunnel diff` reporting no drift (exit 0).

- [ ] **Step 8: Prove it all works away from home**

Tether to a phone, disable WiFi so the home LAN is genuinely unreachable, then:

```bash
ip route get 192.168.3.12 | grep -q tailscale0 && echo "routing via tailnet"
getent hosts infra-bin.lan
infra status
```

Expected: routing via `tailscale0`, the name still resolves, and `infra status` returns real data. This is the single most valuable check in the plan — it proves the portable is actually portable.

---

### Task 20: Final verification and documentation

**Depends on:** Tasks 18, 19.
**Provides:** a signed-off machine and accurate docs.

- [ ] **Step 1: Run the full boot-integrity check**

```bash
bootctl status | grep -E "Secure Boot|Product|Current Boot Loader" -A1
sudo sbctl verify
systemctl is-enabled systemd-boot-update.service
pacman -Q systemd-boot-manager 2>/dev/null && echo "PROBLEM: reinstall it away" || echo "correctly absent"
sudo efibootmgr -v | head
```

Expected: Secure Boot enabled (user); all files signed; `systemd-boot-update.service` enabled; `systemd-boot-manager` absent; no stale NVRAM entry pointing at the XBOOTLDR partition.

- [ ] **Step 2: Run the hardware sweep**

```bash
vainfo 2>&1 | grep -i gfx
cat /sys/class/power_supply/BAT*/charge_control_end_threshold
cat /sys/devices/system/cpu/amd_pstate/status
hyprctl monitors -j | jq -r '.[] | "\(.name) \(.width)x\(.height)@\(.refreshRate) scale=\(.scale)"'
systemctl is-enabled power-profiles-daemon.service
```

Expected: gfx1151; `80`; `active`; `eDP-1 2560x1600@180 scale=1.6`; `masked`.

- [ ] **Step 3: Confirm both OSes and both kernels still boot**

Reboot four times, selecting in turn: CachyOS, CachyOS (LTS), Windows, then back to CachyOS. Confirm each reaches a usable desktop and that Windows does not prompt for a BitLocker key.

- [ ] **Step 4: Record anything that differs from the spec**

Where reality diverged — a different ESP size, a workaround parameter that proved necessary, hardware that worked better or worse than predicted — update the spec's *Known limitations* section rather than leaving the record stale.

```bash
cd ~/Documents/personal/infra
$EDITOR docs/superpowers/specs/2026-08-09-blvckflow-z13-setup-design.md
```

- [ ] **Step 5: Commit any corrections**

```bash
cd ~/Documents/personal/infra
git add docs/superpowers/specs/2026-08-09-blvckflow-z13-setup-design.md
git commit -m "docs(blvckflow): record what the build actually found"
git push
```

- [ ] **Step 6: Verify the dotfiles sync loop works in both directions**

Make a trivial change on BLVCKFlow, push it, and pull it on BLVCKSmall — confirming the three-host repo is genuinely shared and the capability refactor did not break the other hosts.

```bash
# on BLVCKFlow
cd ~/dotfiles && git pull && git push
# on BLVCKSmall
cd ~/dotfiles && git pull && stow -R ags hypr scripts && ags quit && setsid -f ags run ~/.config/ags
```

Expected: BLVCKSmall's desktop still works, with its brightness slider and Rotate toggle intact.

---

## Notes on maintenance

**Adding or removing a kernel** is the one operation that needs manual work in this layout. `systemd-boot-manager` is gone, so a new kernel package needs a hand-written `/boot/loader/entries/<name>.conf` and a `sudo sbctl sign -s /boot/vmlinuz-<name>`. Ordinary kernel *updates* need nothing — the entries reference stable filenames that `mkinitcpio` overwrites in place, and the pacman hook re-signs.

The alternative is `kernel-install` with the AUR `pacman-hook-kernel-install`, which is `BOOT_ROOT`-aware and generates versioned entries automatically, at the cost of masking the mkinitcpio hooks. It was not chosen because hand-written entries are simpler and this machine adds kernels roughly never.

**After a Windows feature update or BIOS update**, Windows Boot Manager routinely reasserts itself as `BootOrder[0]` and the machine appears to boot straight to Windows. Fix with `sudo efibootmgr -o <linux>,<windows>` using the numbers from `efibootmgr -v`.
