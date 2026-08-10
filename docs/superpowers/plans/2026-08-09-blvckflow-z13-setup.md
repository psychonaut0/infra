# BLVCKFlow (ROG Flow Z13) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring an ASUS ROG Flow Z13 (2025) into service as `BLVCKFlow` — a Windows/Arch dual-boot convertible running the fleet's Hyprland desktop, with full SSH and `infra` reach.

**Status (2026-08-10):** Phases 1 and 2 complete. Phase 3 (Arch) and Phase 4 (fleet) remain.

**Spec:** `docs/superpowers/specs/2026-08-09-blvckflow-z13-setup-design.md`

---

## Global Constraints

- **Secure Boot stays ENABLED.** Vanguard requires it plus TPM 2.0; it is firmware-global, so "off for Linux" does not exist. Currently **on**, verified.
- **`sbctl enroll-keys --microsoft`** — mandatory, or `bootmgfw.efi` stops being trusted and Windows will not boot. **Omit `--firmware-builtin`**: on this ASUS board it creates duplicate builtin-db entries and a Secure Boot failure.
- **ASUS firmware: OS Type = Windows UEFI Mode, Secure Boot Mode = Custom.** "Other OS" disables Secure Boot outright on ASUS boards.
- **NEVER format `/dev/nvme0n1p1`.** It is the shared 1 GiB ESP and holds the Windows bootloader. Mount it, never `mkfs` it.
- **Only partitions 5 and 6 may be created.** Partitions 1–4 are Windows and are finished.
- **Plain Arch, then the CachyOS kernel** — not a CachyOS install. Matches `BLVCKMain` and `BLVCKSmall`.
- **Take the `-v4` CachyOS repos** (Zen 5 supports x86-64-v4). `BLVCKMain` uses v3 for older silicon.
- Baseline cmdline is `root=UUID=… rw resume=UUID=…` and nothing else. Verify with `cat /proc/cmdline`, never by reading the config back.
- Hostname is exactly `BLVCKFlow` — `ags/lib/host.ts` string-compares it. (Windows is `BLVCKFlow-win`, deliberately distinct.)
- **This repo is public and sanitized.** No real domain, public IP, or tailnet suffix in any committed file.
- Commit style: short imperative subject, conventional prefix, explanatory body, no co-author lines.

---

## Phase 1 — Repo preparation ✅ COMPLETE

Merged to `~/dotfiles` master at `6abb750`. AGS capability flags, host-agnostic `lid.sh`/`auto-rotate.sh`, `$toggleMonitor`, `BLVCKFlow.conf`, SSH config fixes, package manifests + `bootstrap.sh`, both CLAUDE.mds.

**The manifests need no changes for this build.** `linux-cachyos`, `cachyos-keyring` and `cachyos-mirrorlist` in `core.txt` are the fleet standard; `bootstrap.sh core laptop` works as written.

---

## Phase 2 — Windows ✅ COMPLETE

Clean install of Windows 11 Home 25H2 (build 26200), verified over SSH:

| Check | State |
|---|---|
| Activation | Permanent, firmware digital licence |
| Secure Boot / TPM | **On** / present, enabled, ready |
| BitLocker | **FullyDecrypted, 0 protectors** |
| Hibernation / Fast Startup | **Off**, no `hiberfil.sys` |
| Device problems | **None** — all drivers bound |
| RTC | **UTC** (`RealTimeIsUniversal=1`) |
| Hostname | `BLVCKFlow-win` |
| OpenSSH | Key-only, running, `Automatic` |

**As-built disk 0:**

```
1   1024 MB  EFI System   <- shared; Linux mounts this at /boot
2     16 MB  MSR
3 204056 MB  NTFS  C:
4    743 MB  WinRE
~730 GiB     UNALLOCATED  <- Phase 3 uses this
```

Debloat applied conservatively; `C:\blvckflow-debloat-undo.ps1` reverses every change.

### Lessons recorded

- **Install media must be Microsoft basic data, not an ESP.** Windows hides ESP-typed partitions and assigns no drive letter, so Setup could not find its own `\sources\install.swm` and reported *"install a driver to show hardware"* — which reads like a storage-driver fault. The tell is `list volume`: the installer partition is absent.
- **`rsync -a` cannot write to FAT32** — it exits 23 setting ownership, which under `set -e` aborts the build.
- **Arch ISOs identify their root by `archisosearchuuid=`** (the ISO9660 volume UUID). A FAT32 volume ID can never match it, so a copied stick boots and drops to a rescue shell. The entries must be rewritten to `archisolabel=`.

---

## Phase 3 — Arch Linux

Installer stick: partition 1 is `ARCH_202608`; partition 2 is `DRIVERS` and must not be touched.

### Task 10: Boot the installer and prove the hardware works

**Depends on:** nothing.
**Provides:** a live environment with network.

- [ ] **Step 1: Boot the stick**

Power on holding `Esc`, pick the USB. Secure Boot is enabled and the Arch ISO is signed by Microsoft's third-party CA, so it should boot as-is. If it refuses, do **not** disable Secure Boot permanently — note it and continue; Task 14 replaces the keys anyway.

- [ ] **Step 2: Confirm the essentials before touching the disk**

```bash
cat /sys/power/mem_sleep
lsblk -o NAME,SIZE,FSTYPE,LABEL,PARTTYPENAME /dev/nvme0n1
ip link
```

Expected: four partitions on `nvme0n1` (ESP, MSR, NTFS, WinRE); a wireless interface present. The Z13 has **no Ethernet port**, so Wi-Fi in the live environment is not optional.

- [ ] **Step 3: Get online**

```bash
iwctl
# station wlan0 scan
# station wlan0 get-networks
# station wlan0 connect <SSID>
# exit
ping -c3 archlinux.org
```

If the MT7925 does not appear, use USB tethering from a phone — it binds to generic CDC drivers and always works.

- [ ] **Step 4: Sync the clock and confirm UEFI mode**

```bash
timedatectl set-ntp true
ls /sys/firmware/efi/efivars >/dev/null && echo "UEFI mode OK"
```

- [ ] **Step 5: Record the exact free space**

```bash
sfdisk --dump /dev/nvme0n1 > /tmp/gpt-before.bak
parted /dev/nvme0n1 unit GiB print free
```

Copy `/tmp/gpt-before.bak` somewhere off-machine. Note the free region's start and size — the next task depends on it.

---

### Task 11: Create and format the Linux partitions

**Depends on:** Task 10.
**Provides:** `nvme0n1p5` (swap) and `nvme0n1p6` (root).

- [ ] **Step 1: Append two partitions to the free space**

`--append` only adds; it cannot modify partitions 1–4.

```bash
sfdisk --append /dev/nvme0n1 <<'EOF'
size=64GiB, type=swap, name="swap"
type=linux, name="arch-root"
EOF
partprobe /dev/nvme0n1
```

- [ ] **Step 2: Verify before formatting**

```bash
lsblk -o NAME,SIZE,FSTYPE,LABEL,PARTTYPENAME /dev/nvme0n1
```

Expected: p1 EFI System 1G, p2 16M, p3 ntfs ~199G, p4 ntfs 743M, **p5 64G Linux swap**, **p6 ~666G Linux filesystem**. If p1–p4 changed in any way, stop and restore from `gpt-before.bak`.

- [ ] **Step 3: Format only the new partitions**

```bash
mkswap -L swap /dev/nvme0n1p5
mkfs.ext4 -L arch-root /dev/nvme0n1p6
```

**Do not run `mkfs` against p1.** It holds the Windows bootloader.

- [ ] **Step 4: Mount, reusing the existing ESP**

```bash
mount /dev/nvme0n1p6 /mnt
mkdir -p /mnt/boot
mount /dev/nvme0n1p1 /mnt/boot
swapon /dev/nvme0n1p5
ls /mnt/boot/EFI
```

Expected: `Microsoft` present in `/mnt/boot/EFI` — that proves you mounted the real ESP and did not format it.

---

### Task 12: Install the base system

**Depends on:** Task 11.
**Provides:** a bootable Arch with stock `linux`.

- [ ] **Step 1: pacstrap**

```bash
pacstrap -K /mnt base base-devel linux linux-firmware amd-ucode \
  sudo networkmanager iwd git vim stow openssh sbctl
```

`amd-ucode` matters: the `microcode` mkinitcpio hook bundles it into the initramfs, so no separate `initrd` line is needed in the loader entries.

- [ ] **Step 2: fstab**

```bash
genfstab -U /mnt >> /mnt/etc/fstab
cat /mnt/etc/fstab
```

Confirm the ESP line points at p1 mounted on `/boot`, and that p5 appears as swap.

- [ ] **Step 3: Enter the system and set basics**

```bash
arch-chroot /mnt
ln -sf /usr/share/zoneinfo/Europe/Rome /etc/localtime
hwclock --systohc
sed -i 's/^#en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen
locale-gen
echo 'LANG=en_US.UTF-8' > /etc/locale.conf
echo 'BLVCKFlow' > /etc/hostname
```

The hostname must be exactly `BLVCKFlow` — `ags/lib/host.ts` compares that string, and `system/install.sh` keys its host directory on it.

- [ ] **Step 4: Users**

```bash
passwd
useradd -m -G wheel -s /bin/bash psy
passwd psy
EDITOR=vim visudo   # uncomment: %wheel ALL=(ALL:ALL) ALL
systemctl enable NetworkManager sshd
```

Username `psy` is required — several scripts hardcode `/home/psy`.

- [ ] **Step 5: Install systemd-boot, preserving Windows**

`bootctl install` overwrites `EFI/BOOT/BOOTX64.EFI`, which on this ESP is Microsoft's fallback loader. Back it up first.

**`bootctl install` must run under `arch-chroot -S`, not plain `arch-chroot`.** Plain mode uses a PID namespace, and `bootctl` deliberately refuses to touch UEFI variables there — it copies every file to the ESP and silently creates **no NVRAM entry**, so the firmware boots straight into Windows with no visible error. Verified the hard way on this build.

So either run the whole configuration under `arch-chroot -S /mnt`, or do the bootloader step separately from the live environment:

```bash
cp -a /boot/EFI/BOOT/BOOTX64.EFI /root/BOOTX64.EFI.microsoft.bak 2>/dev/null || true
bootctl install                    # only works if the chroot was entered with -S
systemctl enable systemd-boot-update.service
```

If the NVRAM entry is missing afterwards, fix it from the live environment without re-doing anything else:

```bash
mount /dev/nvme0n1p6 /mnt && mount /dev/nvme0n1p1 /mnt/boot
arch-chroot -S /mnt bootctl install
efibootmgr | grep -E '^BootOrder|Linux Boot Manager'
```

**Enabling that unit is not optional.** Arch ships it disabled (presets only apply at first enablement), which is why both existing fleet hosts run sd-boot 257.4 under systemd 261.

- [ ] **Step 6: Write the loader configuration**

```bash
ROOT_UUID=$(blkid -s UUID -o value /dev/nvme0n1p6)
SWAP_UUID=$(blkid -s UUID -o value /dev/nvme0n1p5)
echo "root=$ROOT_UUID  swap=$SWAP_UUID"
```

`/boot/loader/loader.conf`:

```
default arch.conf
timeout 5
console-mode auto
editor no
```

`/boot/loader/entries/arch.conf`, substituting the UUIDs:

```
title   Arch Linux
linux   /vmlinuz-linux
initrd  /initramfs-linux.img
options root=UUID=<ROOT_UUID> rw resume=UUID=<SWAP_UUID>
```

`console-mode auto` because firmware text mode on a 2560x1600 panel is frequently unreadable. No separate `amd-ucode.img` initrd line — the `microcode` hook handles it.

- [ ] **Step 7: Verify and reboot**

```bash
bootctl status
bootctl list
ls /boot/EFI/Microsoft/Boot/bootmgfw.efi
exit
umount -R /mnt
reboot
```

`bootctl list` should show the Arch entry; the Windows entry is synthesised at boot from `bootmgfw.efi`, so asserting the file exists is the check that matters here.

- [ ] **Step 8: First boot**

Expected: a menu with Arch Linux and Windows Boot Manager. Boot Arch, log in, then:

```bash
cat /proc/cmdline
```

Both `root=UUID=` and `resume=UUID=` must be present. Then reboot once into Windows to confirm it still starts.

---

### Task 13: Add the CachyOS kernel

**Depends on:** Task 12.
**Provides:** `linux-cachyos` alongside stock `linux`.

- [ ] **Step 1: Connect and update**

```bash
nmcli device wifi connect "<SSID>" password "<password>"
sudo pacman -Syu
```

- [ ] **Step 2: Add the CachyOS repositories**

```bash
curl -O https://mirror.cachyos.org/cachyos-repo.tar.xz
tar xvf cachyos-repo.tar.xz && cd cachyos-repo
sudo ./cachyos-repo.sh
```

The script installs `cachyos-keyring` and `cachyos-mirrorlist` out-of-band (the keyring lives inside the repo it authenticates) and writes the `pacman.conf` stanzas **above** `[core]`, which is what yields the optimised builds. Take the **v4** repos.

- [ ] **Step 3: Confirm what the repos will supply**

```bash
pacman -Sy
pacman -Si linux-cachyos | grep -E '^(Repository|Version)'
pacman -Si asusctl | grep -E '^(Repository|Version)'
```

Both should resolve from a `cachyos*` repo. `asusctl` is not in Arch's official repos; this is where it comes from.

- [ ] **Step 4: Install the kernel**

```bash
sudo pacman -S linux-cachyos linux-cachyos-headers
ls -l /boot/vmlinuz-* /boot/initramfs-*
```

- [ ] **Step 5: Add its loader entry**

Keep stock `linux` as the fallback: same vintage, so no feature regression on new silicon, and it is the reference kernel — if something breaks, booting it says immediately whether the cause is a CachyOS patch.

`/boot/loader/entries/cachyos.conf`:

```
title   Arch Linux (cachyos)
linux   /vmlinuz-linux-cachyos
initrd  /initramfs-linux-cachyos.img
options root=UUID=<ROOT_UUID> rw resume=UUID=<SWAP_UUID>
```

Then set `default cachyos.conf` in `/boot/loader/loader.conf`.

- [ ] **Step 6: Verify**

```bash
sudo bootctl list
sudo reboot
# after reboot:
uname -r
```

Expected: a `-cachyos` kernel, with the stock entry still selectable.

---

### Task 14: Secure Boot with your own keys

**Depends on:** Task 13 — both kernels must exist before signing.
**Provides:** Secure Boot enabled with custom keys; Windows still boots.

- [ ] **Step 1: Check state**

```bash
sudo sbctl status
```

Enrolment needs `Setup Mode: Enabled`.

- [ ] **Step 2: Put the firmware in Setup Mode**

```bash
systemctl reboot --firmware-setup
```

In the BIOS: Security → Secure Boot → **Delete all Secure Boot keys** (this enters Setup Mode). Leave Secure Boot itself enabled. Confirm `OS Type = Windows UEFI Mode` and `Secure Boot Mode = Custom`. Save and exit.

- [ ] **Step 3: Create and enrol, retaining Microsoft's keys**

```bash
sudo sbctl create-keys
sudo sbctl enroll-keys --microsoft
sudo sbctl status
```

**`--microsoft` is mandatory** — without it `bootmgfw.efi` stops being trusted and Windows will not boot. **Do not pass `--firmware-builtin`** on this board.

- [ ] **Step 4: Sign everything in the boot path**

```bash
sudo sbctl sign -s /boot/EFI/systemd/systemd-bootx64.efi
sudo sbctl sign -s /boot/EFI/BOOT/BOOTX64.EFI
sudo sbctl sign -s /boot/vmlinuz-linux
sudo sbctl sign -s /boot/vmlinuz-linux-cachyos
sudo sbctl verify
```

Do **not** re-sign anything under `/boot/EFI/Microsoft` — those carry Microsoft's signatures.

- [ ] **Step 5: Make re-signing automatic**

```bash
pacman -Ql sbctl | grep -i hook
```

If no hook ships, create `/etc/pacman.d/hooks/95-sbctl-sign.hook`:

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

Without this, the first kernel update produces an unsigned kernel and the machine stops booting.

- [ ] **Step 6: Re-enable Secure Boot and verify both OSes**

Reboot into the BIOS, set Secure Boot **Enabled**, save. Then:

```bash
sudo sbctl status          # expect: Secure Boot: Enabled (user)
bootctl status | grep -i secure
```

Boot Windows and confirm it starts, then in PowerShell `Confirm-SecureBootUEFI` → `True`, and launch League to confirm Vanguard still initialises.

- [ ] **Step 7: Prove the hook works before trusting it**

```bash
sudo pacman -S --needed linux-cachyos && sudo sbctl verify
```

All files must still report signed after a kernel transaction.

---

### Task 15: Hibernation

**Depends on:** Task 14 (loader entries are signed after changes).
**Provides:** working `systemctl hibernate`.

- [ ] **Step 1: Confirm the pieces**

```bash
swapon --show
grep '^HOOKS' /etc/mkinitcpio.conf
cat /sys/power/disk
```

Expect a 64G partition swap; `systemd` in HOOKS (so `systemd-hibernate-resume-generator` handles `resume=` — do **not** add the busybox `resume` hook); `shutdown` offered by `/sys/power/disk`.

- [ ] **Step 2: Confirm `resume=` reached the kernel**

```bash
cat /proc/cmdline
```

It was set in Tasks 12 and 13. If missing, fix the `options` line in both loader entries and re-run `sudo sbctl sign-all`.

- [ ] **Step 3: Test from a TTY**

Switch to `Ctrl+Alt+F3` first, so a graphical failure cannot mask the result.

```bash
sudo systemctl hibernate
```

Power back on; the session should resume with processes intact.

```bash
journalctl -b 0 | grep -i "hibernat\|resume" | head
uptime
```

---

### Task 16: Hardware enablement

**Depends on:** Task 13.
**Provides:** GPU acceleration, audio, power management, battery limit.

- [ ] **Step 1: Baseline — what already works**

```bash
sudo pacman -S --needed mesa vulkan-radeon libva-utils
vainfo 2>&1 | head -20
wpctl status | head -30
nmcli device status
cat /sys/power/mem_sleep
```

Expect `vainfo` to name gfx1151 and list AV1 Profile 0; an ALC294 card; `[s2idle]`.

- [ ] **Step 2: Confirm the ASUS platform driver binds**

This is the precondition for `asusd` ever starting.

```bash
cat /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_family
ls /sys/bus/platform/drivers/asus-nb-wmi/
```

- [ ] **Step 3: asusctl**

```bash
sudo pacman -S asusctl
asusctl -v
systemctl --user enable --now asusd-user.service
systemctl status asusd.service --no-pager | head
```

`systemctl enable asusd.service` **fails** — no `[Install]` section; it is udev + D-Bus activated and needs no enabling.

- [ ] **Step 4: Resolve the platform_profile conflict**

```bash
sudo systemctl mask --now power-profiles-daemon.service
systemctl is-enabled power-profiles-daemon.service
```

Must report `masked`. Disabling is not enough — it is D-Bus activated and restarts itself, racing asusd.

- [ ] **Step 5: Battery limit**

```bash
asusctl battery limit 80
cat /sys/class/power_supply/BAT*/charge_control_end_threshold
```

Expect `80`. asusd persists and re-applies it; no custom unit needed.

- [ ] **Step 6: Confirm amd-pstate is already correct**

```bash
cat /sys/devices/system/cpu/amd_pstate/status
```

Expect `active`. Do not force `guided` or `passive`.

- [ ] **Step 7: Suspend**

```bash
sudo systemctl suspend
# wake it, then:
sudo pacman -S --needed amd-debug-tools
sudo amd_s2idle --duration 60
```

If resume misbehaves, `amd_s2idle` names the blocking device — use that rather than guessing at unbind hacks.

- [ ] **Step 8: Only now consider workaround parameters**

Add to the `options` line of both entries **only** against an observed symptom, then `sudo sbctl sign-all`, reboot, and confirm with `cat /proc/cmdline`:

| Symptom | Parameter |
|---|---|
| Panel flicker / artefacts | `amdgpu.dcdebugmask=0x600` |
| Bluetooth wedges | `usbcore.autosuspend=-1` |
| Wi-Fi drops after resume | `options mt7925e disable_aspm=1` in `/etc/modprobe.d/` |
| GPU hangs under load | `amdgpu.cwsr_enable=0` |

One at a time. The bundle applied preemptively means debugging a regression you introduced.

---

### Task 17: Desktop bring-up

**Depends on:** Tasks 13 and 16.
**Provides:** the Hyprland desktop.

- [ ] **Step 1: Clone the dotfiles**

```bash
sudo pacman -S --needed git stow base-devel
git clone https://github.com/psychonaut0/dotfile-final.git ~/dotfiles
cd ~/dotfiles && git log --oneline -5
```

- [ ] **Step 2: AUR helper**

```bash
sudo pacman -S --needed paru || {
  git clone https://aur.archlinux.org/paru-bin.git /tmp/paru && cd /tmp/paru && makepkg -si --noconfirm
}
paru --version
```

- [ ] **Step 3: Bootstrap**

```bash
cd ~/dotfiles && ./system/bootstrap.sh core laptop
```

Installs the manifests, stows every package directory, and creates the gitignored `current.conf` symlink. If it exits "no host config for BLVCKFlow", the hostname is wrong — `sudo hostnamectl set-hostname BLVCKFlow` and re-run.

- [ ] **Step 4: Add the manual-rotate binds**

`BLVCKFlow.conf` ships without them because they need the touchscreen id.

```bash
hyprctl devices -j | jq -r '.touch[0].name'
```

Insert into `~/dotfiles/hypr/.config/hypr/hosts/BLVCKFlow.conf`, replacing the comment explaining their absence:

```
# Monitor transform (sync touch input)
$touch = <id from hyprctl devices>
bind = $mainMod, M, exec, hyprctl keyword monitor $mainMonitor,transform,2 && hyprctl keyword "device[$touch]:transform" 2
bind = $mainMod Shift, M, exec, hyprctl keyword monitor $mainMonitor,transform,0 && hyprctl keyword "device[$touch]:transform" 0
bind = $altMod, M, exec, hyprctl keyword monitor $mainMonitor,transform,2 && hyprctl keyword "device[$touch]:transform" 2
bind = $altMod Shift, M, exec, hyprctl keyword monitor $mainMonitor,transform,0 && hyprctl keyword "device[$touch]:transform" 0
```

Then commit, push, and `hyprctl reload`.

- [ ] **Step 5: /etc configs**

```bash
cd ~/dotfiles && sudo ./system/install.sh
```

Expect a `[BLVCKFlow]` line for `/etc/sddm.conf.d/10-host.conf`. Its absence means `hostname` does not match the directory name.

- [ ] **Step 6: Theme**

AGS will not start without the gitignored `_colors.scss`.

```bash
sh ~/scripts/wallpaper.sh ~/Pictures/wallpapers/<image>
ls -la ~/.config/ags/_colors.scss
```

- [ ] **Step 7: hyprgrass**

```bash
hyprpm add https://github.com/horriblename/hyprgrass
hyprpm enable hyprgrass
hyprpm list
```

- [ ] **Step 8: Verify the desktop**

```bash
hyprctl monitors -j | jq -r '.[] | "\(.name) \(.width)x\(.height)@\(.refreshRate) scale=\(.scale)"'
pgrep -x gjs
```

Expect `eDP-1 2560x1600@180 scale=1.6` and an AGS PID. Open the control panel: the brightness slider and Rotate toggle must both be present (they are gated on `hasBacklight` / `hasRotation`).

- [ ] **Step 9: Rotation end-to-end**

```bash
sh ~/scripts/auto-rotate.sh toggle
```

Rotate the tablet. The display must rotate **and touch input follow**. If touch is 90° out, set `$ROTATE_TOUCH` explicitly.

- [ ] **Step 10: No zombies**

Five minutes after login:

```bash
ps -eo stat,comm | grep -c '^Z'
```

Expect `0`.

---

## Phase 4 — Fleet onboarding

### Task 18: Distribute the SSH key

**Depends on:** Task 17.

`ssh-copy-id` cannot work (`permitrootlogin without-password` fleet-wide), so keys are pushed *from* an already-authorised host. Use a **fresh** key: sshd matches the first `authorized_keys` entry, so reusing a fleet key would silently route this machine into ct-backup's forced-command dispatcher.

- [ ] **Step 1: Generate on BLVCKFlow**

```bash
ssh-keygen -t ed25519 -a 100 -C 'psy@BLVCKFlow' -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

- [ ] **Step 2: Move it to an authorised host and confirm it is new**

```bash
tailscale file cp ~/.ssh/id_ed25519.pub blvcksmall:
```

On BLVCKSmall:

```bash
tailscale file get /tmp/
NEWKEY="$(cat /tmp/id_ed25519.pub)"
grep -q "$NEWKEY" ~/.ssh/authorized_keys 2>/dev/null && echo 'STOP: key already in use' || echo 'fresh key, ok'
```

- [ ] **Step 3: Check cluster quorum before touching pmxcfs**

`/etc/pve` is corosync-replicated and read-only unless quorate; this is a 2-node cluster needing both votes.

```bash
ssh proxmoxmain 'pvecm status | grep -E "Quorate|Total votes"'
```

- [ ] **Step 4: One append covers both Proxmox nodes**

```bash
ssh proxmoxmain "if ! grep -qxF '$NEWKEY' /etc/pve/priv/authorized_keys; then printf '%s\n' '$NEWKEY' >> /etc/pve/priv/authorized_keys; echo added; else echo present; fi"
ssh proxmoxnode "grep -c 'psy@BLVCKFlow' /root/.ssh/authorized_keys"
```

Expect `added` then `1` — that proves pmxcfs propagation. Append with `>>` only; never `sed -i` on pmxcfs.

- [ ] **Step 5: Each of the 14 CTs**

```bash
for o in 5 6 7 8 9 10 11 12 13 14 15 16 17 18; do
  H=192.168.3.$o; printf '%-14s ' "$H"
  ssh -o BatchMode=yes -o ConnectTimeout=5 "root@$H" \
    "install -d -m 700 /root/.ssh; touch /root/.ssh/authorized_keys; chmod 600 /root/.ssh/authorized_keys; \
     if ! grep -qxF '$NEWKEY' /root/.ssh/authorized_keys; then printf '%s\n' '$NEWKEY' >> /root/.ssh/authorized_keys; echo added; else echo present; fi" \
    || echo FAILED
done
```

- [ ] **Step 6: And blvckmain**

```bash
ssh blvckmain "install -d -m 700 ~/.ssh; touch ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys; \
  if ! grep -qxF '$NEWKEY' ~/.ssh/authorized_keys; then printf '%s\n' '$NEWKEY' >> ~/.ssh/authorized_keys; echo added; else echo present; fi"
```

- [ ] **Step 7: Prove access from BLVCKFlow**

```bash
for o in 2 3 5 6 7 8 9 10 11 12 13 14 15 16 17 18; do
  H=192.168.3.$o; printf '%-14s ' "$H"
  ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 "root@$H" hostname || echo FAILED
done
ssh -o StrictHostKeyChecking=accept-new blvckmain hostname
```

- [ ] **Step 8: GitHub**

Add the pubkey at https://github.com/settings/keys, then `ssh -T git@github.com`.

---

### Task 19: Tailscale and the infra CLI

**Depends on:** Task 17.

- [ ] **Step 1: Join the tailnet**

`--accept-routes` **defaults to false on Linux**; without it there is no route to `192.168.3.0/24`, so no Pi-hole, so no `.lan`, so no `infra-bin.lan`.

```bash
sudo pacman -S --needed tailscale
sudo systemctl enable --now tailscaled
sudo tailscale up --hostname=blvckflow --accept-routes --accept-dns --operator=$USER
```

- [ ] **Step 2: Verify the prefs match the fleet**

```bash
tailscale debug prefs | grep -E 'RouteAll|CorpDNS|WantRunning'
```

Expect `RouteAll: true`, `CorpDNS: true`.

- [ ] **Step 3: Verify `.lan` resolves over the tailnet**

```bash
tailscale dns status | sed -n '/Split DNS Routes/,+4p'
ip route get 192.168.3.5
getent hosts infra-bin.lan
```

Expect a `lan -> 192.168.3.5` route, ct-dns via `tailscale0`, and `infra-bin.lan` → `192.168.3.12`. If enrolment succeeds but `192.168.3.x` is unreachable, subnet routes may need approving in the Tailscale admin console.

- [ ] **Step 4: infra CLI**

Install to `~/.local/bin`: `infra update` rewrites its own resolved path, so a root-owned binary can never self-update as `psy`.

```bash
sudo pacman -S --needed curl python coreutils
mkdir -p ~/.local/bin
curl -fsSL http://infra-bin.lan/install.sh | INFRA_INSTALL_DIR="$HOME/.local/bin" sh
infra version
```

- [ ] **Step 5: Set `INFRA_REPO`**

`infra` locates the repo by walking up for a `.git` dir, so it breaks inside other repos. Add to `~/.zshrc.d/.exports`:

```bash
export INFRA_REPO="$HOME/Documents/personal/infra"
```

- [ ] **Step 6: Clone the repo and its out-of-band secrets**

`CLAUDE.local.md` and the Cloudflare token are gitignored and will not arrive with a clone.

```bash
mkdir -p ~/Documents/personal
git clone git@github.com:psychonaut0/infra.git ~/Documents/personal/infra
scp blvcksmall:/home/psy/Documents/personal/infra/CLAUDE.local.md ~/Documents/personal/infra/
mkdir -p ~/.config/infra && scp blvcksmall:~/.config/infra/cloudflare.yml ~/.config/infra/
chmod 600 ~/.config/infra/cloudflare.yml
```

Prefer minting a fresh `Cloudflare Tunnel:Read` token over copying the existing one, which leaked into a transcript on 2026-07-28.

- [ ] **Step 7: Exercise it**

```bash
infra ls && infra status && infra ct status && infra dns ls && infra tunnel diff
```

- [ ] **Step 8: Prove it works away from home**

Tether to a phone, disable Wi-Fi so the home LAN is unreachable, then:

```bash
ip route get 192.168.3.12 | grep -q tailscale0 && echo "routing via tailnet"
getent hosts infra-bin.lan
infra status
```

This is the check that proves the portable is actually portable.

---

### Task 20: Final verification and documentation

**Depends on:** Tasks 18, 19.

- [ ] **Step 1: Boot integrity**

```bash
bootctl status | grep -E "Secure Boot|Product|Current Boot Loader" -A1
sudo sbctl verify
systemctl is-enabled systemd-boot-update.service
sudo efibootmgr -v | head
```

Expect Secure Boot enabled (user), all files signed, the update unit enabled, and no stale NVRAM entries.

- [ ] **Step 2: Hardware sweep**

```bash
vainfo 2>&1 | grep -i gfx
cat /sys/class/power_supply/BAT*/charge_control_end_threshold
cat /sys/devices/system/cpu/amd_pstate/status
hyprctl monitors -j | jq -r '.[] | "\(.name) \(.width)x\(.height)@\(.refreshRate) scale=\(.scale)"'
systemctl is-enabled power-profiles-daemon.service
```

Expect gfx1151, `80`, `active`, `eDP-1 2560x1600@180 scale=1.6`, `masked`.

- [ ] **Step 3: Both OSes, both kernels**

Reboot four times: Arch (cachyos), Arch (stock), Windows, back to Arch. Each must reach a usable desktop, and Windows must not prompt for a BitLocker key.

- [ ] **Step 4: Update the fleet docs**

The device block in this repo's `CLAUDE.md` was written before the build. Correct anything that turned out differently, and run the secrets scan before committing:

```bash
cd ~/Documents/personal/infra
git diff CLAUDE.md > /tmp/doc.diff
awk -F'|' '/^\| `<[A-Z_]+>`/ { gsub(/[`[:space:]]/,"",$3); if (length($3)>5) print $3 }' CLAUDE.local.md \
  | while read -r s; do grep -qF -- "$s" /tmp/doc.diff && echo "LEAK: $s"; done
rm -f /tmp/doc.diff
```

- [ ] **Step 5: Record what differed**

Update the spec's *Known limitations* with anything the build revealed, then commit both.

---

## Maintenance notes

**Adding a kernel** needs a hand-written `/boot/loader/entries/<name>.conf` plus `sudo sbctl sign -s /boot/vmlinuz-<name>`. Ordinary kernel *updates* need nothing — entries reference stable filenames that mkinitcpio overwrites in place, and the pacman hook re-signs.

**After a Windows feature update or BIOS update**, Windows Boot Manager tends to reassert itself as `BootOrder[0]`. Fix with `sudo efibootmgr -o <linux>,<windows>` using the numbers from `efibootmgr -v`.

**Both existing hosts need `sudo bootctl update` and `systemctl enable systemd-boot-update.service`** — they are running sd-boot 257.4 under systemd 261.
