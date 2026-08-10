# BLVCKFlow — ASUS ROG Flow Z13 (2025) Dual-Boot Workstation — Design

**Status:** Spec — 2026-08-09
**Goal:** Bring a new ASUS ROG Flow Z13 (2025) into service as `BLVCKFlow` — a Windows/CachyOS dual-boot convertible that merges the roles of the existing two workstations, runs the same Hyprland desktop from the same dotfiles repo, and has full SSH + `infra` CLI reach into the fleet from anywhere.

## Background

The fleet has two workstations: `BLVCKMain` (desktop, dual 1440p, audio rig) and `BLVCKSmall` (2-in-1 touchscreen laptop). Both run CachyOS + Hyprland from `~/dotfiles`, a Stow repo with a three-layer host mechanism (`hypr/hosts/<HOSTNAME>.conf`, `ags/lib/host.ts`, `system/hosts/<HOSTNAME>/`).

The Z13 is a third host, not a replacement — it is a tablet like `BLVCKSmall` but is intended as the primary machine like `BLVCKMain`. That dual identity is the one structural decision in the desktop half of this spec: the AGS shell currently branches on host *identity*, and `BLVCKFlow` needs every branch currently gated on `isBLVCKSmall`.

Windows is retained for League of Legends, which Riot Vanguard gates behind Secure Boot. That single fact drives the boot architecture and is treated as a hard requirement, not a preference.

### Verified starting facts (2026-08-09)

| Fact | Value |
|---|---|
| Model | ROG Flow Z13 (2025), GZ302EA / GZ302EAC — Ryzen AI Max "Strix Halo", gfx1151 (RDNA 3.5) |
| Memory | 64GB LPDDR5X-8000, quad-channel, **soldered** — fixed for the life of the machine |
| Panel | 13.4" 2560x1600 16:10, 180Hz, IPS-level, Adaptive-Sync advertised, stylus-capable |
| Ports | 2x USB4 Type-C (DP alt + PD), 1x USB 3.2 Gen2 Type-A, 3.5mm, microSD UHS-II, XG Mobile. **No HDMI** |
| Storage | Single 1TB M.2 2230 NVMe (~931.5 GiB usable) |
| Aura devices | Exactly two — lightbar `18c6`, detachable keyboard `1a30`. No AniMe Matrix, no Slash |
| Biometrics | **No fingerprint reader.** IR camera for Windows Hello only |
| Fleet kernel | CachyOS 7.1.2 (this host), 7.0.8 (blvckmain). 6.19 was the last 6.x; 7.0 shipped April 2026 |
| systemd | 261.2-1 in Arch/CachyOS |
| `asusctl` | **6.3.11, in the default-enabled `[cachyos]` repo** — no third-party repo needed |
| `linux-lts` | 6.18.37 — well past gfx1151 enablement, credible rescue kernel |
| Tailnet | `.lan` resolves over Tailscale via a tailnet-wide Split DNS route (`lan` → ct-dns); `main-gateway` advertises both subnets and offers an exit node |
| `infra` mirror | v0.7.3, reachable over Tailscale from off-LAN (verified) |
| Proxmox key sharing | Both nodes' `/root/.ssh/authorized_keys` symlink to `/etc/pve/priv/authorized_keys`, hashes identical — one append covers both |

## Requirements

**In scope:**

- Windows 11 retained and **fully functional for Vanguard-gated games**, at 200GB.
- CachyOS on the same disk, booting via systemd-boot, with a permanent LTS fallback kernel.
- Secure Boot **enabled**, with the owner's own keys, Microsoft's keys retained.
- Hibernation supported from day one.
- Same Hyprland/AGS desktop as the other two hosts, driven from the same dotfiles repo.
- SSH access to all 16 fleet hosts (14 CTs + both Proxmox nodes) plus `blvckmain`, `infra` CLI, and Tailscale reachability from off-LAN.
- The host recorded in this repo's `CLAUDE.md` (devices + network layout) and in the dotfiles repo.
- A committed bootstrap (package lists + script) so host #4 is a procedure, not an archaeology exercise.

**Out of scope:**

- Any change to CT or server infrastructure. Onboarding a workstation is a keys + Tailscale + docs operation; nothing in the backup, monitoring, or DNS stacks needs to move.
- Adding `BLVCKFlow` to `stacks/hosts.yaml` or `fleet.json` — those are SSH targets for fleet ops, all servers.
- A `.lan` DNS record. MagicDNS covers workstations tailnet-wide; no workstation has a Pi-hole record today.
- Audio-production tooling (Evo8 scripts) — that stays `BLVCKMain`-only.
- Migrating or retiring `BLVCKSmall`.

## The Secure Boot constraint

Riot Vanguard requires Secure Boot **enabled** plus TPM 2.0 on Windows 11; without it League refuses to launch (VAN9001). Secure Boot is a firmware-global state, so "off for Linux, on for Windows" is not available. Disabling it would leave a 200GB partition whose only remaining purposes are BIOS updates, Armoury Crate, and ITE keyboard recovery.

**Decision: enrol custom keys with `sbctl`, Secure Boot stays enabled.**

`BLVCKMain` already reports `Secure Boot: enabled (user)` — this fleet has the precedent. The requirements:

- `sbctl enroll-keys --microsoft` — **the `--microsoft` flag is load-bearing.** Without Microsoft's KEK/db retained, `bootmgfw.efi` stops being trusted and Windows will not boot.
- Sign `systemd-bootx64.efi` on the ESP and both kernels on XBOOTLDR.
- A pacman hook to re-sign on every kernel and systemd upgrade, or the machine stops booting after an update.
- Firmware must be in Setup Mode to enrol. On this chassis that is a BIOS reset-to-setup, and the BIOS is a cut-down ASUS tablet UI — the escape hatch if function keys are unavailable is `systemctl reboot --firmware-setup` from Linux.

Sequencing matters: Secure Boot state is part of the TPM measurement that seals BitLocker. Every SB toggle must happen **before** BitLocker is re-enabled, or Windows demands the recovery key at every boot.

## Disk layout and bootloader

### Why XBOOTLDR

The factory ESP is expected to be ~260MB — enough for the Windows bootloader and the ~150KB sd-boot binary, but not for two kernels plus initramfs (measured at ~129MB for a cachyos + lts pair on this fleet). Growing the ESP means moving Windows. XBOOTLDR is the mechanism systemd-boot provides for exactly this: sd-boot's stub stays on the small ESP, kernels live on a separate partition, and **Windows auto-detection is unaffected** — `config_add_entry_windows()` scans the ESP the loader booted from, independently of XBOOTLDR.

XBOOTLDR must be **FAT32**. (Strictly, sd-boot can load drop-in EFI filesystem drivers, so ext4 is not categorically impossible — but it adds a failure mode with no upside on a first install.)

### Partition table

Target layout on `nvme0n1`. Sizes assume ~931.5 GiB usable; **the factory ESP size and logical sector size must be confirmed on the live ISO before committing** (`lsblk -o NAME,SIZE,PHY-SEC,LOG-SEC`).

| Part | Size | Type | Mount | Notes |
|---|---|---|---|---|
| p1 | ~260 MiB | ESP (vfat) | `/efi` | **Factory, never reformatted.** sd-boot binary + loader.conf + Windows |
| p2 | 16 MiB | MSR | — | Factory |
| p3 | 200 GiB | NTFS | — | Windows C:, shrunk from Windows' own Disk Management |
| p4 | ~800 MiB | NTFS | — | Windows Recovery, factory |
| p5 | 1 GiB | XBOOTLDR (FAT32) | `/boot` | GPT type `bc13c2ff-59e6-4262-a352-b275fd6f7172` (`sfdisk` alias `xbootldr`) |
| p6 | 64 GiB | swap | — | Hibernation target; sized to RAM so any image fits |
| p7 | ~665 GiB | ext4 | `/` | |

`gptfdisk` is not installed on CachyOS; `sfdisk` is, and accepts the `xbootldr` type alias directly.

File placement follows systemd's split: EFI binaries, `loader.conf` and `random-seed` land on the ESP; `loader/entries` and `EFI/Linux` land on XBOOTLDR. A `loader.conf` written to XBOOTLDR is **silently ignored**. Entry file paths are relative to the partition root, so a kernel at `/boot/vmlinuz-linux-cachyos` is written as `/vmlinuz-linux-cachyos`.

With ESP at `/efi` and XBOOTLDR at `/boot`, **stock CachyOS mkinitcpio presets need zero edits** — every preset path already resolves through `/boot`.

### CachyOS-specific landmines

Three, none documented in any guide:

1. **Calamares cannot produce this layout**, confirmed by a CachyOS developer on their own forum ("I'm pretty sure that it's impossible to do even with manual partitioning too"). The install therefore runs in two stages: let Calamares install normally with p5 as a plain `/boot`, then flip the GPT type code and re-run `bootctl` from a chroot.

2. **`systemd-boot-manager` (`sdboot-manage`) must be removed.** It is installed unconditionally by Calamares and hooked to every kernel and systemd upgrade. It only knows the ESP path (`ESP="$(bootctl -p)"`), so in the target layout it dies at its own `loader/entries` existence check and never regenerates anything — a pacman hook that fails on every kernel update. (It does not *destroy* XBOOTLDR entries, as is sometimes claimed; it cannot reach them. But a permanently failing hook is not acceptable either.)

3. **Removing it removes the only thing that updates sd-boot.** `systemd-boot-update.service` ships with an `[Install]` section and a preset, but presets apply only at initial enablement — on a real CachyOS install it is **disabled**, and this fleet's own `BLVCKSmall` is running an sd-boot binary four minor versions behind its systemd. `systemctl enable systemd-boot-update.service` is part of the fixup, and belongs in the verification script.

Additional safeguards, each cheap:

- `sfdisk --dump /dev/nvme0n1 > gpt.bak` before the type-code flip. One line, makes the single riskiest command reversible.
- Back up `EFI/BOOT/BOOTX64.EFI` before `bootctl install` — it overwrites Microsoft's fallback loader.
- Use `arch-chroot -S` for `bootctl install`; plain `arch-chroot` runs in a PID namespace where bootctl refuses to touch NVRAM.
- Explicit `/etc/fstab` entries for both `/efi` and `/boot` — these also disable `systemd-gpt-auto-generator` for both partitions, which is what we want.
- `default cachyos.conf` (not `@saved`) and a generous timeout until keyboard input at the sd-boot menu is proven. This is a tablet: the boot menu is not touch-driven, and there are model-specific reports of the magnetic keyboard being unresponsive at firmware stage. Console legibility on a 2560x1600 panel is also a live concern — `console-mode auto` rather than `keep`.
- After a Windows feature update or BIOS update, Windows Boot Manager routinely reasserts itself as `BootOrder[0]`. `efibootmgr -o <linux>,<windows>` is a 30-second fix that should be written down before it is needed.

### Hibernation

Swap partition sized at 64GiB — equal to RAM, so any image fits regardless of `/sys/power/image_size`. zram (CachyOS default) and a hibernation swap partition coexist correctly; systemd deliberately skips zram when choosing a hibernation target, so no zram change is needed.

`resume=UUID=<swap>` goes on the kernel cmdline. No `resume_offset` (that is a swapfile concern), and no busybox `resume` mkinitcpio hook — CachyOS uses the `systemd` hook, where `systemd-hibernate-resume-generator` handles it.

**Do not write `/etc/kernel/cmdline`.** On a stock CachyOS install that file is never read; the cmdline lives in the `options` line of `/boot/loader/entries/*.conf`. And `mkinitcpio -P` rebuilds the initramfs only — it does not apply cmdline changes. Verify with `cat /proc/cmdline` after reboot, never by reading the config back.

## Hardware enablement

Kernel 7.1 clears essentially every version gate in circulating guides, most of which were written against 6.14–6.16.

| Component | Status |
|---|---|
| iGPU (gfx1151) | Works. Mesa ≥ 24.1 for VA-API; `vainfo` should name gfx1151 and list AV1 Profile 0 |
| Speakers | **Work.** Both GZ302 quirks (`0x1043:0x1514`, `0x1043:0x1fb3`) are in mainline 7.1, verified in `alc269.c`; CS35L41 firmware ships in the fleet's `linux-firmware`. No modprobe config. (Older reports claim the `EAC` variant is silent — those predate the March 2026 quirk. Test on the unit; if silent, check the subsystem id with `cat /proc/asound/card*/codec*`) |
| WiFi/BT (MT7925) | Works. **Do not apply `disable_aspm=1` preemptively** — native ASPM was fixed in 6.17 and it costs idle power. Keep it as a diagnostic if drops appear. If Bluetooth wedges, the lever is `usbcore.autosuspend=-1` and a full power cycle — *not* downgrading `linux-firmware` |
| Touchscreen | Works natively (merged 6.15/6.16) |
| Stylus | Untested on this exact pen under Hyprland — the one cited "working" report used a Surface Pen. Treat pressure/tilt as unverified |
| Accelerometer | Reads via `amd_sfh` + `iio-sensor-proxy`. Note `iio-hyprland-git` was last updated 2024-11 — budget for it not building |
| Cameras | **Broken.** AMD ISP4 is not in mainline 7.1, and no `OV05C10` sensor driver exists in any tree. Workaround: USB webcam. No IR camera means no face unlock |
| Fingerprint | **Absent on this model.** Do not install `fprintd` |
| Tablet-mode switch | **Broken.** `asus-nb-wmi` advertises `SW_TABLET_MODE` but emits no events on detach. Needs a third-party uinput daemon, or do without |
| Suspend | s2idle. Flakiness on Strix Halo traces to the ISP4 camera i2c driver. Diagnose with `amd-debug-tools` (in `[extra]`) rather than guessing at unbind hacks; confirm `/sys/power/mem_sleep` on the unit rather than assuming deep is unavailable |

**Do not apply speculative kernel parameters at install.** `amdgpu.dcdebugmask=0x600`, `sg_display=0`, `cwsr_enable=0` and `abmlevel` circulate as a bundle; each is a workaround for a specific symptom, and applying them blind means debugging a regression you introduced. Baseline cmdline is `root=UUID=… rw resume=UUID=…` and nothing else. (Note the panel type is contradictory across ASUS's own pages — "IPS-level" on the spec sheet, "OLED" in eShop titles — and `dcdebugmask=0x600` is an OLED-artefact fix. Resolve with `drm_info` before considering it.)

GTT/VRAM tuning is **not needed**. amdgpu already defaults the GTT limit to 50% of RAM (~32GiB here). Only ROCm/LLM work above ~30GB needs more, and the knob is `ttm.pages_limit` — `amdgpu.gttsize` is deprecated, and the `amdttm.` prefix applies only to AMD's out-of-tree DKMS. The dedicated-VRAM carveout is set from **Armoury Crate in Windows**, not the BIOS, and persists into Linux — a small carveout plus a raised TTM limit is the documented shape.

`amd_iommu=off` is wrong; `iommu=pt` is the correct middle ground if IOMMU ever needs relaxing.

### Power and thermal

`asusctl` 6.3.11 from `[cachyos]`. Notes that matter:

- `systemctl enable asusd.service` **fails** — the unit has no `[Install]` section and is udev + D-Bus activated, so it needs no enabling. The user-session companion does: `systemctl --user enable asusd-user.service`. Before any of this, confirm the DMI strings actually match the udev rule's globs and that `asus-nb-wmi` binds — that is the single precondition for asusd ever starting.
- Battery limit is `asusctl battery limit 80`; asusd persists and re-applies it, so no custom unit is needed.
- **`power-profiles-daemon` must be masked, not disabled** — it is D-Bus activated and will restart itself, racing asusd over `platform_profile` and EPP.
- `amd-pstate` is already `active`/EPP by default on Zen 5. Do not force `guided` or `passive`.
- PPT/TDP control comes from the `asus-armoury` driver (merged 6.19) via `asusctl armoury set`. It is gated behind both `ppt_enabled`/`EnablePptGroup` **and** an enabled custom fan curve — a prerequisite that has been in force since 6.3.8. If `armoury set` appears to do nothing, that is why.
- `asusctl profile next` does not cycle all four profiles — on firmware exposing LowPower it never lands on Quiet. Bind `profile set Quiet` explicitly.
- The Aura D-Bus object path embeds the USB device number and **changes on every keyboard reattach**. Any AGS integration must enumerate via `GetManagedObjects` and read `DeviceType`, never hardcode a path.
- `rog-control-center` has an open fd-leak panic (#229) and is a poor fit; the D-Bus API is polkit-free and open to `wheel`, which suits scripting from the AGS bar directly.

### Two hardware risks worth stating plainly

**Sustained inference can hard-power-cut the machine.** Multiple owners report the GZ302EA cutting power with no graceful shutdown after ~6 minutes of sustained load (llama.cpp, whisperx), root-caused to an EC cooling gate on a sensor Linux cannot read — die temp ~35C below limit at the time. Reproduces across distros and kernels. On a 64GB Strix Halo bought partly for local LLM work, this is the most consequential known defect and it has no software fix.

**The ITE keyboard microcontroller can wedge into bootloader mode** (`048d:89db`), and the only known recovery is a Windows-only ASUS tool. It can also block entry to the BIOS. Keep a USB keyboard on hand during the install, and note this as a third independent argument for retaining Windows (alongside Vanguard and the Armoury Crate VRAM carveout).

## Desktop — dotfiles host #3

### Capability flags, not host equality

`ags/lib/host.ts` currently exports `isBLVCKSmall` / `isBLVCKMain`, and six sites branch on them. `BLVCKFlow` needs *every* `isBLVCKSmall` branch — it has a backlight, an accelerometer, and an on-screen keyboard. Adding `|| isBLVCKFlow` at four call sites will rot.

Instead, add `isBLVCKFlow` and derive capability flags — `hasBacklight`, `hasRotation`, `hasOSK` — so a fourth host only ever edits `host.ts`. Two of the affected sites (`ControlPanel.tsx:28,31`) are the 3-second `brightnessctl` and `auto-rotate.pid` pollers the dotfiles CLAUDE.md warns about; widening the gate keeps them off `BLVCKMain` while enabling them here.

Also flagged and worth cleaning while in there: `Volume.tsx` imports `isBLVCKMain` and never uses it, and its two Evo8 functions are dead. `WallpaperButton.tsx` exports `BottomPanel` despite its filename — grep for the symbol, not the file.

### Panel scale

`monitor = eDP-1, 2560x1600@180, 0x0, 1.6`

Scale 1.6 is the only sane fractional choice: 2560/1.6 = 1600 and 1600/1.6 = 1000 are both exact, so Hyprland accepts it, and the resulting ~141 logical PPI matches the effective density of the other two hosts — meaning the AGS bar's fixed 14px font and button sizes stay identically proportioned across the fleet. Scale 1.5 is rejected outright by Hyprland (2560/1.5 is not a clean divisor); 1.25 leaves touch targets too small for a tablet; 2 wastes a primary machine's screen.

### Scripts and the missing lid

The Z13 is a kickstand tablet with a magnetically detachable cover. **There is no hinge, no libinput `Lid Switch`, and no `/proc/acpi/button/lid/LID0`** — so `BLVCKFlow.conf` gets zero `bindl=…Lid Switch` lines, and `lid.sh`'s `close`/`open` paths are unreachable here.

But `lid.sh` cannot simply be ignored: the *shared* `hypridle.conf` calls `lid.sh resume` as `after_sleep_cmd` on every host. As written it would fail its grep against a nonexistent lid state and never re-apply monitor config after wake. Three targeted fixes: treat a missing lid as "open", replace the hardcoded 1920x1080 modeline in `enable_edp()` with `hyprctl reload` (so each host re-applies its own rules), and autodetect the touch device instead of hardcoding `BLVCKSmall`'s ELAN id. `auto-rotate.sh` needs the same touch autodetection — without it, touch input stays rotated 90° off after every rotation, which is the single most visible breakage.

One safety edit to shared config: `Super+F1` disables `$mainMonitor`. On both existing hosts that is an external display; on `BLVCKFlow` it is `eDP-1`, the only output when undocked — so the bind would black-screen the machine with no way back. Introduce `$toggleMonitor`, defined in all three host files (an undefined Hyprland variable is passed through as a literal string and silently breaks the bind).

### Machine-local files

Two gitignored files must be created by hand or the desktop will not start: `hypr/hosts/current.conf` (the host symlink) and `ags/_colors.scss` (generated by running matugen once).

## Fleet integration

Four independent tracks, none touching CT or service machinery.

**Tailscale.** `--accept-routes` **defaults to false on Linux**, and omitting it makes the machine functionally offline from the fleet: no route to `192.168.3.0/24`, so no Pi-hole, so no `.lan` name, so no `infra-bin.lan`, so no `infra update`. `.lan` resolution over Tailscale is verified working via a tailnet-wide Split DNS route, which the node inherits automatically. Set `--operator=$USER` to avoid sudo on every subsequent `tailscale set`.

**SSH keys.** Fresh ed25519 key — never reuse a fleet key, because sshd matches the first `authorized_keys` entry for a given key and ct-backup's forced-command entry would silently capture it. `ssh-copy-id` from the new machine cannot work (`permitrootlogin without-password` fleet-wide), so keys are pushed *from* an already-authorised host. One append to `/etc/pve/priv/authorized_keys` covers both Proxmox nodes via pmxcfs — but that filesystem is read-only unless the 2-node cluster is quorate, so check `pvecm status` first and append with `>>` only. The 14 CTs each need their own append; the `infra` CLI has no key-distribution subcommand (an `infra keys add` wrapping that loop is a reasonable follow-up).

**`infra` CLI.** Install to `~/.local/bin`, not `/usr/local/bin` — `infra update` rewrites its own resolved path, so a root-owned binary can never self-update as `psy`. Requires `curl`, `sha256sum` and `python`. Note `infra` locates the repo by walking up for a `.git` dir, so it misbehaves inside *other* repos; set `INFRA_REPO` globally.

**Docs.** Add the host to this repo's `CLAUDE.md` (Network & Devices + the ASCII Network Layout). **This repo is public and sanitized** — no tailnet suffix, real domain, or public IP in the device block; those belong in `CLAUDE.local.md`, which does not arrive with a clone and must be copied separately.

### Pre-existing gaps found during recon

Worth fixing while here, all in `dotfiles/ssh/.ssh/config`:

- `ct-chat` is missing entirely — `ssh 192.168.3.18` falls back to user `psy` and is denied. `infra` is immune because it uses `root@<ip>`, which is why this went unnoticed.
- `Host blvckserver` (192.168.3.4) is dead — that machine was decomposed into the CT fleet.
- `bitbucket.org` pins `IdentityFile ~/.ssh/id_travelware`, a work key that has no business on a personal machine.

Separately, `CLAUDE.md`'s claim that `blvckmain` resolves to `192.168.1.110` is stale in practice — MagicDNS answers first.

## Bootstrap capture

`BLVCKMain` and `BLVCKSmall` share 156 explicit packages; 165 are desktop-only and 49 laptop-only. The dotfiles CLAUDE.md currently records required packages as prose, which is already incomplete.

Commit `system/packages/{core,laptop,desktop,aur}.txt` plus a `bootstrap.sh` that installs them, stows, and creates the host symlink. `BLVCKFlow` draws `core` + `laptop`.

Two corrections to the naive package list: `libva-mesa-driver` and `mesa-vdpau` no longer exist (folded into `mesa`), and **`wvkbd-mobintl` is not a package** — it is the binary produced by the AUR `wvkbd` package.

## Verification

Ordered so each step's dependencies are already proven:

1. **Live ISO, before touching anything:** `lsblk -o NAME,SIZE,PHY-SEC,LOG-SEC` (confirm ESP size and sector size); touchscreen responds; WiFi associates. Without WiFi there is no mirror, no Tailscale, no fleet.
2. **Windows, before repartitioning:** BitLocker recovery key exported *and verified readable*; Device Encryption off; `powercfg /h off`; BIOS + firmware updated; Armoury Crate VRAM carveout set.
3. **Post-install, before first unassisted reboot:** `bootctl status` shows the correct ESP/XBOOTLDR split; both kernel entries present; `bootmgfw.efi` exists on the ESP; `efibootmgr -v` shows no stale entry pointing at p5; `systemd-boot-update.service` enabled; `systemd-boot-manager` removed.
4. **First boot:** `cat /proc/cmdline` (never read the config back); `sbctl status` shows Secure Boot enabled with own keys; Windows still boots.
5. **Hardware:** `vainfo` names gfx1151; audio out and mic; `hyprctl monitors` shows 2560x1600@180 at scale 1.6; touch + rotation + stylus; `asusctl battery limit 80` persists across reboot; suspend/resume; hibernate from a TTY *before* binding it to anything.
6. **Fleet:** SSH to all 16 fleet hosts plus `blvckmain` by alias; `infra version` / `ls` / `status` / `ct status`; `infra dns ls` and `infra tunnel diff` (exercises repo- and token-dependent paths); `.lan` resolution and `infra-bin.lan` reachability from a tethered connection with the LAN unreachable.

## Known limitations

- No working camera, and no path to one until the ISP4 + OV05C10 stack lands upstream.
- No biometric login of any kind.
- Tablet-mode detection does not work; cover detach is invisible to userspace without a third-party daemon.
- Sustained heavy inference may hard-cut power. Unfixable in software.
- Speaker output is quieter than under Windows — the shipped CS35L41 gain profile is conservative and Dolby's tuning is Windows-only.
- Secure Boot with custom keys means every kernel and systemd update depends on a signing hook. If the hook breaks, the machine stops booting.
- `.lan` resolution has two single points of failure (ct-dns and the subnet router) — and because the tailscale0 route wins for `192.168.3.0/24`, that is true even on the home LAN.
- The tailnet ACL policy was not readable during recon. If enrolment succeeds but `192.168.3.x` is unreachable, subnet routes may need approving in the Tailscale admin console.

## Follow-up work

- `infra keys add <pubkey>` — wrap the key-distribution loop, which is currently hand-rolled.
- AGS integration for asusd (battery limit, platform profile, fan curve) over its polkit-free D-Bus API.
- Decide whether `BLVCKSmall` stays in service or is retired once `BLVCKFlow` is bedded in.
- Re-test tablet-mode and camera support after each kernel bump; both are upstream-pending rather than permanently broken.
