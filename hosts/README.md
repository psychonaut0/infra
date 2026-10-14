# Host-level config (Proxmox nodes)

Files under `hosts/<node>/` mirror their absolute path on that node
(`hosts/proxmoxnode/etc/nut/ups.conf` → `/etc/nut/ups.conf` on proxmoxnode).
Unlike `stacks/`, nothing here is deployed by `infra deploy` — these are
version-controlled copies of hand-installed config, kept in sync by hand.

The repo is public, so secrets and identifiers are placeholders. The real
values live only on the hosts (and in the nightly restic snapshot, via
`host-export.sh`):

| Placeholder | Where the real value is |
|---|---|
| `<NUT_PRIMARY_PASSWORD>` | proxmoxnode `/etc/nut/upsd.users` |
| `<NUT_SECONDARY_PASSWORD>` | proxmoxnode `/etc/nut/upsd.users`, also `/root/nut-secondary-password` |
| `<PROXMOXMAIN_MAC>` | `ip link show eno1` on proxmoxmain |

## NUT (UPS)

Design and test results: *Power (UPS)* in `AGENTS.md`. Summary: proxmoxnode
holds the USB cable and runs `upsd` (`netserver`); proxmoxmain is a
`netclient` that shuts itself down after 3 min on battery; proxmoxnode rides
on to LOWBATT, then kills UPS power so both nodes cold-boot when mains returns.

### Install from scratch

On proxmoxnode:

```bash
apt-get install -y nut-server nut-client wakeonlan
# The UPS udev rule only applies on hotplug; if the cable was already in:
udevadm trigger --subsystem-match=usb --action=add
```

On proxmoxmain:

```bash
apt-get install -y nut-client
```

Copy the files from `hosts/<node>/etc/nut/` to `/etc/nut/`, substitute the
placeholders (generate fresh passwords with `openssl rand -hex 16` — the
secondary password must match between proxmoxnode's `upsd.users` and
proxmoxmain's `upsmon.conf`), then:

```bash
chown root:nut /etc/nut/*
chmod 640 /etc/nut/*.conf /etc/nut/upsd.users
chmod 750 /etc/nut/upssched-cmd
```

proxmoxnode: `systemctl restart nut-driver@atlantis nut-server && systemctl enable --now nut-monitor`.
Editing `ups.conf` triggers `nut-driver-enumerator`, which restarts the driver
on its own — a concurrent manual restart may report `Job canceled`; that is
harmless.

proxmoxmain: `systemctl enable --now nut-monitor`.

### Verify

```bash
# on proxmoxnode
upsc atlantis@localhost              # ups.status OL CHRG, delays 60/120
upsc -c atlantis@localhost           # clients: 127.0.0.1 and 192.168.3.2
# on proxmoxmain
upsc atlantis@192.168.3.3 ups.status
runuser -u nut -- upsmon -c reload   # proves the nut user can signal upsmon,
                                     # which is how upssched-cmd triggers fsd
```

Unplug test (stay under 3 min, or proxmoxmain really shuts down): during the
outage `ups.status` goes `OB DISCHRG` and `ps -C upssched` shows a daemon on
proxmoxmain; on replug the status returns to `OL` and the daemon exits.
Events log as `nut-monitor` (not `upsmon`); timer starts and cancels are not
logged at all.

### Known gaps

- proxmoxmain often hangs at the end of poweroff with the power LED on, and
  WoL did not wake it after a forced power-off (2026-10-14). Until the smart
  plug exists, an outage longer than 3 min needs a manual power cycle.
- BIOS on both nodes (ErP off, WoL on, *Restore on AC Power Loss* = Power On)
  is not yet confirmed. Without *Restore on AC*, a killpower cycle leaves the
  nodes off; proxmoxnode alone is not quorate and will not autostart its CTs.
