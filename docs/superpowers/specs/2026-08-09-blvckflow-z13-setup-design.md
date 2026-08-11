# BLVCKFlow — ASUS ROG Flow Z13 (2025) Dual-Boot Workstation — Design

**Status:** Revised 2026-08-10 — Windows half built, Linux half pending
**Goal:** Bring an ASUS ROG Flow Z13 (2025) into service as `BLVCKFlow` — a Windows/Arch dual-boot convertible that merges the roles of the existing two workstations, runs the same Hyprland desktop from the same dotfiles repo, and has full SSH + `infra` CLI reach into the fleet from anywhere.

## Revision note

The first draft of this spec (2026-08-09) contained two factual errors that changed the design. Both are corrected throughout; they are recorded here because the corrections are the interesting part.

**The fleet does not run CachyOS.** `BLVCKMain` and `BLVCKSmall` are **plain Arch** (`/etc/os-release` → `NAME="Arch Linux"`) with the CachyOS repositories added on top by CachyOS's own `cachyos-repo.sh`, which places them above `[core]`. That yields optimised builds — 1062 of 1514 installed packages on BLVCKSmall resolve to a CachyOS repo — and the `-cachyos` kernel. Neither host has `cachyos-hello`, Calamares, or `systemd-boot-manager`. The first draft assumed a CachyOS *distro* install and inherited two landmines from that assumption which never applied here.

**Windows was reinstalled from scratch**, not shrunk. That removed the BitLocker-decryption sequencing problem entirely and allowed a **1 GiB ESP** to be created up front, which deletes the XBOOTLDR design.

## Background

The fleet has two workstations: `BLVCKMain` (desktop, dual 1440p, audio rig) and `BLVCKSmall` (2-in-1 touchscreen laptop). Both run Arch + Hyprland from `~/dotfiles`, a Stow repo with a three-layer host mechanism (`hypr/hosts/<HOSTNAME>.conf`, `ags/lib/host.ts`, `system/hosts/<HOSTNAME>/`).

The Z13 is a third host — a tablet like `BLVCKSmall`, intended as the primary machine like `BLVCKMain`. That dual identity drove the one structural change in the desktop layer: the AGS shell branched on host *identity*, and `BLVCKFlow` needs every branch previously gated on `isBLVCKSmall`.

Windows is retained solely for League of Legends, which Riot Vanguard gates behind Secure Boot. That single fact drives the boot architecture and is a hard requirement.

### Verified facts

| Fact | Value |
|---|---|
| Model | ROG Flow Z13 (2025), GZ302EA / GZ302EAC — Ryzen AI Max+ 395 "Strix Halo", gfx1151 |
| Memory | 64GB LPDDR5X-8000, soldered |
| Panel | 13.4" 2560x1600, 180Hz, Adaptive-Sync |
| Ports | 2x USB4 Type-C, 1x USB 3.2 Gen2 Type-A, 3.5mm, microSD. **No HDMI** |
| Storage | Micron `MTFDKBK1T0QGN` 1TB NVMe (~931.5 GiB), standard NVMe — inbox `stornvme.sys` drives it |
| Biometrics | **No fingerprint reader.** IR camera is Windows-Hello only |
| Fleet OS | Plain Arch on all hosts; CachyOS repos layered above `[core]` |
| `[cachyos]` repo | 833 packages, only **90 shadow Arch**, 743 unique — mostly additive |
| `cachyos-extra-v4` | 7306 packages, **all** shadow Arch — this is the takeover repo |
| `asusctl` | Only in `[cachyos]`, not in Arch's official repos |
| MT7925 Wi-Fi | **No inbox Windows 11 25H2 driver.** ASUS package required |
| Touchscreen/stylus | **No vendor driver exists** for either SKU — inbox HID drives the digitiser |
| Windows edition | Firmware digital licence → Pro activates automatically |

## Requirements

**In scope:**

- Windows 11 retained and fully functional for Vanguard-gated games, at 200 GiB.
- Arch on the same disk, systemd-boot, with a fallback kernel.
- The CachyOS kernel (`linux-cachyos`) on that Arch install.
- Secure Boot **enabled**, owner's keys, Microsoft's keys retained.
- Hibernation supported.
- Same Hyprland/AGS desktop as the other two hosts, from the same dotfiles repo.
- SSH to all 16 fleet hosts plus `blvckmain`, `infra` CLI, Tailscale reachability off-LAN.
- Host recorded in this repo's `CLAUDE.md` and in the dotfiles repo.

**Out of scope:**

- Any change to CT or server infrastructure.
- Adding `BLVCKFlow` to `stacks/hosts.yaml` or `fleet.json` — those are server SSH targets.
- A `.lan` DNS record. MagicDNS covers workstations.
- Audio-production tooling (Evo8) — stays `BLVCKMain`-only.
- Migrating or retiring `BLVCKSmall`.

## The Secure Boot constraint

Riot Vanguard requires Secure Boot **enabled** plus TPM 2.0 on Windows 11; without it League refuses to launch (VAN9001). Secure Boot is firmware-global, so "off for Linux, on for Windows" does not exist.

**Decision: enrol custom keys with `sbctl`, Secure Boot stays enabled.** `BLVCKMain` already reports `Secure Boot: enabled (user)`, so the fleet has precedent.

- `sbctl enroll-keys --microsoft` — the `--microsoft` flag is load-bearing. Without Microsoft's KEK/db retained, `bootmgfw.efi` stops being trusted and Windows will not boot.
- **Omit `--firmware-builtin`** on this ASUS board: it produces duplicate builtin-db entries and a Secure Boot failure.
- Sign `systemd-bootx64.efi` and every kernel; a pacman hook must re-sign on kernel and systemd upgrades or the machine stops booting after an update.
- ASUS firmware: **Boot → Secure Boot → OS Type = Windows UEFI Mode**, **Secure Boot Mode = Custom**. Selecting "Other OS" *disables* Secure Boot on ASUS boards.

## Disk layout

Windows is installed. Partitions 1–3 exist as built; Setup may have added a recovery partition — **confirm with `list partition` before creating the Linux partitions**.

| # | Size | Type | Mount | Status |
|---|---|---|---|---|
| 1 | 1 GiB | EFI System (FAT32) | `/boot` | **Built.** Shared with Windows |
| 2 | 16 MiB | MSR | — | Built |
| 3 | 200 GiB | NTFS | — | Built. Windows C: |
| 4 | ~800 MiB | NTFS | — | Possible WinRE, created by Setup — verify |
| 5 | 64 GiB | swap | — | Pending. Hibernation target, sized to RAM |
| 6 | ~665 GiB | ext4 | `/` | Pending |

**The 1 GiB ESP is the design's biggest simplification.** The original plan used XBOOTLDR because the factory ESP was ~260 MiB — too small for multiple kernels. Creating the ESP ourselves during a clean Windows install removes that entirely: no GPT type-code flip, no split `bootctl --esp-path/--boot-path`, no boot-manager surgery. systemd-boot is configured exactly as on `BLVCKMain`.

Two kernels plus initramfs measure ~129 MiB, so 1 GiB is generous even with three.

## Windows install — as built

Clean install from a USB stick built on Linux: GPT, a FAT32 partition with the ISO contents, `install.wim` split with `wimlib-imagex` into `install.swm` + `install2.swm` (FAT32's 4 GiB file ceiling), plus a second exFAT partition carrying the driver set.

`autounattend.xml` at the stick root and at `sources/$OEM$/$$/Panther/` handles: local account, no Microsoft account, skipped privacy prompts, US locale with `en-GB` UI (the media is English International — a `UILanguage` mismatch stalls Setup), `PreventDeviceEncryption`, and `powercfg /h off` at first logon.

It deliberately contains **no `<DiskConfiguration>` and no `<InstallTo>`**. On a disk that will hold Linux, a wrong `DiskID` silently erases everything with no confirmation dialog. The partition picker stays interactive; one mouse click is the price.

`powercfg /h off` is not optional: Fast Startup leaves NTFS dirty, which is precisely what stops Linux mounting the Windows partition.

### Two failures worth recording

**The installer partition must be Microsoft basic data, not an ESP.** It was first created with `type=uefi`. UEFI booted it happily, but Windows *hides* ESP-typed partitions and assigns them no drive letter — so Setup started, then could not find its own `\sources\install.swm`. The resulting dialog reads "install a driver to show hardware", which sounds like a missing storage driver and sends you at the SSD. It is really "I lost my install media". The tell is `list volume`: the installer partition is absent while other partitions show letters. Rufus types this partition as basic data for exactly this reason.

**`rsync -a` cannot write to FAT32.** `-a` implies ownership and permission preservation, which FAT cannot store; rsync exits 23 and, under `set -e`, aborts the build before the WIM split. Use `-rlt --no-perms --no-owner --no-group`.

## Linux install — plan

**Plain Arch, then the CachyOS kernel**, matching the rest of the fleet. Install with `archinstall` or `pacstrap`; there is no Calamares and no `systemd-boot-manager` anywhere in this design.

1. Install Arch to partitions 5–6, ESP mounted at `/boot`, `linux` as the base kernel.
2. Add the CachyOS repos with CachyOS's `cachyos-repo.sh`, which installs `cachyos-keyring` and `cachyos-mirrorlist` and writes the `pacman.conf` stanzas. Take the **v4** repos: Zen 5 supports x86-64-v4, as does BLVCKSmall's Tiger Lake. `BLVCKMain` uses v3.
3. `pacman -S linux-cachyos linux-cachyos-headers`.
4. Keep stock `linux` as the second boot entry. It is the same vintage as `linux-cachyos`, so no feature regression on new silicon, and it is the reference kernel: if something breaks, booting stock `linux` immediately says whether the cause is a CachyOS patch or the hardware.

`systemd-boot-update.service` is **disabled by default on Arch** — presets only apply at first enablement. Both existing hosts are running sd-boot 257.4 under systemd 261 because of this. Enable it here, and fix the other two.

Baseline kernel cmdline is `root=UUID=… rw resume=UUID=…` and nothing else. No `dcdebugmask`, `sg_display`, `cwsr_enable`, `abmlevel`, `gttsize`, or `disable_aspm` — each is a workaround for a specific symptom, applied only after observing that symptom. Verify with `cat /proc/cmdline`, never by reading the config back.

GTT/VRAM tuning is unnecessary: amdgpu already defaults the GTT limit to 50% of RAM (~32 GiB). Only ROCm work above ~30 GiB needs more, and the knob is `ttm.pages_limit` — `amdgpu.gttsize` is deprecated.

## Hardware enablement

Kernel 7.1 clears essentially every version gate in circulating guides, most written against 6.14–6.16.

| Component | Status |
|---|---|
| iGPU (gfx1151) | Works. Mesa ≥ 24.1 for VA-API |
| Speakers | Work. Both GZ302 quirks are in mainline 7.1; CS35L41 firmware ships in `linux-firmware` |
| WiFi/BT (MT7925) | Works. **Do not** apply `disable_aspm=1` preemptively — fixed in 6.17, and it costs idle power. If Bluetooth wedges, the lever is `usbcore.autosuspend=-1`, *not* downgrading `linux-firmware` |
| Touchscreen | Works natively (merged 6.15/6.16) |
| Stylus | Untested under Hyprland — the one "working" report used a Surface Pen |
| Cameras | **Recon was wrong.** Not an ISP4/`OV05C10` part at all — it enumerates as a plain USB UVC device (`ASUS 5M webcam`, `636e:0bda`, `/dev/video0-3`) advertising MJPG to 2592x1944. The driver works; capture returns no frames, so suspect a privacy shutter or firmware gate, not a missing driver |
| Fingerprint | **Absent on this model** |
| Tablet-mode switch | **Broken.** `SW_TABLET_MODE` advertised but no events on detach |
| Suspend | s2idle. Diagnose with `amd-debug-tools`, not guesswork |

`asusctl` comes from `[cachyos]`. `systemctl enable asusd.service` **fails** — no `[Install]` section, it is udev + D-Bus activated; enable `asusd-user.service` instead. **`power-profiles-daemon` must be masked**, not disabled — it is D-Bus activated and will restart itself, racing asusd over `platform_profile`. Battery limit is `asusctl battery limit 80`, persisted by asusd.

**Two unfixable risks:** sustained inference can hard-cut power via an EC thermal gate on a sensor Linux cannot read (~6 minutes of llama.cpp, reproduced across distros); and the ITE keyboard controller can wedge into bootloader mode, recoverable only with a Windows-only ASUS tool — a third argument for keeping Windows.

## Desktop — dotfiles host #3

The AGS shell now branches on **capability flags** (`hasBacklight`, `hasRotation`, `hasOSK`) rather than host identity, so a fourth host only ever edits `lib/host.ts`. Two of the gated sites are 3-second shell pollers that spawn zombie processes if ungated.

`monitor = eDP-1, 2560x1600@180, 0x0, 1.6`. Scale 1.6 is the only sane fractional value: 2560/1.6 = 1600 and 1600/1.6 = 1000 are both exact, and the resulting ~141 logical PPI matches the other two hosts so the AGS bar stays proportionate. Hyprland rejects 1.5 outright.

The Z13 is a kickstand tablet with a detachable cover: **no hinge, no libinput `Lid Switch`, no `/proc/acpi/button/lid/LID0`**. So no lid binds — but `lid.sh resume` still runs, because `hypridle.conf` is shared, hence a missing lid file now counts as "open" and `enable_edp()` calls `hyprctl reload` instead of a hardcoded modeline.

`$toggleMonitor` exists so `Super+F1` cannot disable the only output on a tablet.

**The package manifests need no changes.** `linux-cachyos`, `cachyos-keyring` and `cachyos-mirrorlist` in `core.txt` are the fleet standard, not contamination. `bootstrap.sh core laptop` works as written.

## Fleet integration

**Tailscale:** `--accept-routes` defaults to false on Linux; without it the machine is functionally offline from the fleet — no route to `192.168.3.0/24`, so no Pi-hole, so no `.lan` name, so no `infra-bin.lan`. `.lan` resolution over Tailscale is verified working via a tailnet-wide split-DNS route.

**SSH keys:** fresh ed25519 — never reuse a fleet key, since sshd matches the first `authorized_keys` entry and ct-backup's forced-command entry would silently capture it. `ssh-copy-id` cannot work (`permitrootlogin without-password` fleet-wide), so keys are pushed *from* an already-authorised host. One append to `/etc/pve/priv/authorized_keys` covers both Proxmox nodes via pmxcfs — but that filesystem is read-only unless the 2-node cluster is quorate.

**`infra` CLI:** install to `~/.local/bin`, not `/usr/local/bin` — `infra update` rewrites its own resolved path, so a root-owned binary can never self-update as `psy`.

**Docs:** this repo is public and sanitized. No tailnet suffix, real domain, or public IP in the device block.

## Verification

1. **Windows:** `manage-bde -status C:` says not enabled or fully decrypted; `powercfg /a` reports hibernation unavailable; `Confirm-SecureBootUEFI` returns `True`; League launches and Vanguard initialises.
2. **Before first unassisted reboot of Linux:** `bootctl status` shows the right ESP; both kernel entries present; `bootmgfw.efi` still on the ESP; `systemd-boot-update.service` enabled.
3. **First boot:** `cat /proc/cmdline`; `sbctl status` shows Secure Boot enabled with own keys; Windows still boots.
4. **Hardware:** `vainfo` names gfx1151; audio in/out; `hyprctl monitors` shows 2560x1600@180 scale 1.6; touch, rotation, stylus; `asusctl battery limit 80` persists; suspend/resume; hibernate from a TTY.
5. **Fleet:** SSH to all 16 hosts plus `blvckmain`; `infra version|ls|status|ct status`; `.lan` resolution and `infra-bin.lan` from a tethered connection with the LAN unreachable.

## Known limitations

- Camera enumerates as USB UVC and the driver binds, but no frames come out. Cause unidentified — a privacy shutter or firmware gate, not the missing ISP4 driver this spec originally assumed.
- Touch gestures do not work. hyprgrass was removed; Hyprland 0.56's native `gesture` keyword is accepted by the parser but does not fire on touchscreen input. Deferred with no working path.
- No biometric login of any kind.
- Tablet-mode detection does not work; cover detach is invisible to userspace.
- Sustained heavy inference may hard-cut power. Unfixable in software.
- Speakers are quieter than under Windows — the shipped CS35L41 gain profile is conservative and Dolby's tuning is Windows-only.
- Secure Boot with custom keys means every kernel and systemd update depends on a signing hook.
- `.lan` resolution has two single points of failure (ct-dns and the subnet router), true even on the home LAN because the tailscale0 route wins for `192.168.3.0/24`.
- The tailnet ACL policy was not readable during recon; if enrolment succeeds but `192.168.3.x` is unreachable, subnet routes may need approving.

## Build-order gaps found during execution

Four steps were missing from the plan. All of them surfaced late, as confusing
symptoms rather than obvious omissions — the common cause being **local state on
BLVCKSmall that nothing deploys**, so no step existed to skip:

- **Stow the `system` dotfiles package before enabling Tailscale.** It ships
  `99-tailscale-subnet-fix`, the `sudoers.d/askpass` helper, the `sddm.conf.d`
  files and the astronaut theme variant. Skipping it meant `--accept-routes`
  black-holed the LAN the moment Tailscale came up, and the cause was three
  layers away from the symptom.
- **Install `inetutils`.** `system/install.sh` opens with `HOST="$(hostname)"`
  under `set -euo pipefail`, and Arch does not ship `hostname` by default, so
  the installer aborts on line 14 on a fresh host.
- **Run `~/scripts/install-fonts.sh`.** `ags/style.scss` asks for the commercial
  `Gintronic Nerd Font`, which existed only in BLVCKSmall's
  `~/.local/share/fonts`. Absent, fontconfig silently substitutes Noto Sans and
  the entire shell renders in the wrong typeface at the wrong metrics — which
  reads as mis-themed icons, text and sliders, not as a missing font. The
  archive of record is now `ct-files:…/psy/Documents/Fonts/_install/`; the fonts
  stay out of the repo on licence grounds. Note the archive originally held only
  `.woff`, which fontconfig cannot use, so it had to be completed with the
  `.ttf` set before it could install anything.
- **Install `ttf-nerd-fonts-symbols`, `breeze-icons`, `cantarell-fonts`.** Not
  in the curated package list. Without them the glyph fallback chain and
  Thunar's icon fallbacks differ from the other hosts.

GTK3 dark mode was a fleet-wide bug rather than a Z13 one: GTK3 ignores
`gsettings` `color-scheme=prefer-dark` (that key only drives GTK4/libadwaita),
so Thunar and friends rendered light against a dark shell on **all three**
hosts. Both toolkits' `settings.ini` are now a tracked `gtk` stow package with
`gtk-application-prefer-dark-theme=1`.

## Follow-up work

- `infra keys add <pubkey>` — wrap the key-distribution loop, currently hand-rolled.
- Touch gestures: neither hyprgrass nor Hyprland's native `gesture` works. Needs a fresh look.
- Test `asusctl armoury set ppt_pl1_spl` below 60W against the EC power-cut-under-load defect.
- Identify why the UVC camera yields no frames (privacy shutter? firmware gate?).
- Keyboard backlight has no hotkey on any host; `asusctl leds next/prev` would do it once the folio's keysyms are confirmed with `wev`.
- `hypr/env.conf` sets `QT_QPA_PLATFORMTHEME=qt5ct` but `qt5ct` is installed on **none** of the three hosts, so Qt apps are unthemed fleet-wide. Either install it or drop the variable.
- `gsettings` names `Orchis-Pink-Dark` (gtk-theme) and `Reversal-black-dark` (icon-theme) on BLVCKSmall; neither theme exists on any host (`~/.themes` and `~/.icons` are empty). Stale strings — GTK silently falls back to Adwaita. Clean up or install them for real.
- The AGS stylesheet emits four GTK4 CSS warnings at every start (`max-height`, `overflow`, `max-width` — unsupported properties). Harmless but noisy.
- AGS integration for asusd over its polkit-free D-Bus API.
- Enable `systemd-boot-update.service` on `BLVCKMain` and `BLVCKSmall` and run `bootctl update` — both are four minor versions behind.
- Decide whether `BLVCKSmall` stays in service.
- Re-test tablet-mode and camera support after each kernel bump; both are upstream-pending rather than permanently broken.
- The public `infra` repo's pushed history contains the real domain, public IP, HAOS MAC and ISP name from before the 2026-07-28 sanitisation. Removing them needs a history rewrite and force-push.
