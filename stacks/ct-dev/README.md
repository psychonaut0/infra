# ct-dev — runbook

Always-on development container. VMID 116 on proxmoxmain, `192.168.3.19`,
MagicDNS `ct-dev`. Design: `docs/superpowers/specs/2026-08-31-ct-dev-design.md`.

## Bootstrap / recovery

Recovery is **recreate and re-run**, not restore — nothing here is backed up.

1. On proxmoxmain: `bash provision-host.sh` (sysctl, `pct create`, CT config).
2. Push `provision-ct.sh` and `home/` into the CT, then:
   `env WORK_REPO=... WORK_REPO_URL=... bash /root/provision-ct.sh`
3. Copy the `travelogue` binary from BLVCKFlow to `/root/travelogue` before
   step 2 (it is distributed via Bitbucket downloads, not fetchable anonymously).
4. Interactive, once: `tailscale up`, `travelogue setup`, `travelogue login`,
   `claude` (login).
5. Migrate Claude session state (see below).

Both scripts are idempotent; re-running them is the intended way to converge.
`provision-ct.sh` is the **only** supported way to reach a working ct-dev —
the box is not backed up, so the script is the sole rebuild path. Any manual
fix applied to the live container to get unblocked must be folded back into
the script immediately, or the "reproducible from these scripts" claim that
justifies skipping backups quietly stops being true.

## Claude session migration

The project path is byte-identical to the workstation, so the state directory
encodes to the same name and transplants without rewriting:

```
rsync -a ~/.claude/projects/<encoded-work-repo-dir> ct-dev:~/.claude/projects/
rsync -a ~/.claude/settings.json ct-dev:~/.claude/settings.json
rsync -a ~/.claude/skills/ ct-dev:~/.claude/skills/
```

Credentials are **not** copied — log in once on the box. Never copy
`~/.claude/.credentials.json` or `~/.claude/.claude.json` (machine identity /
onboarding state, not portable session data) host-to-host.

`rsync` is not part of the base ct-dev image — `apt-get install -y rsync` on
first use if it's missing (`ssh ct-dev "which rsync"` to check first).

## Daily use

- Always work inside `tmux`; it is what makes sessions survive disconnect.
- Batch, nothing attached: `dev-task run <name> "<prompt>"`, then
  `dev-task list` / `dev-task log <name>`. MOTD shows what finished.
- AWS: `travelogue login` (headless — use the `--no-browser` flow).

## Gotchas

- **`vm.max_map_count` lives on the hypervisor.** If OpenSearch will not start,
  check proxmoxmain, not the container — the sysctl is not namespaced.
- **`/dev/net/tun` must be passed through** or Tailscale cannot come up.
- **Never `--accept-routes`.** ct-dev is on `192.168.3.0/24`, a subnet the
  tailnet advertises; accepting it black-holes the LAN.
- **Not backed up, deliberately.** ct-dev is absent from `CT_IPS` in
  `stacks/ct-backup/scripts/pre-backup.sh` on purpose. Uncommitted work dies
  with the container — push often.
- **Resource budget is shared with ct-nvr.** The 12 GB allocation assumes
  recording stays off. Running full builds with ct-nvr also running will
  oversubscribe proxmoxmain.
- **PATH on ct-dev has three independent contexts, configured separately:**
  - login/interactive shells → `/etc/profile.d/ct-dev-path.sh`
  - non-interactive `ssh ct-dev "cmd"` → `/etc/environment` (read by `pam_env`)
  - systemd `--user` units (this is how `dev-task` runs) →
    `~/.config/environment.d/10-ct-dev.conf`

  Any binary added to this box must be verified in **all three** — a binary
  that works interactively can be completely invisible to scripts and to
  systemd units. Also note `environment.d` is parsed only when the user
  manager starts; `systemctl --user daemon-reexec` does **not** re-read it —
  a full `systemctl restart user@1000.service` is required, which terminates
  that user's running `--user` units.
- **`ping` does not work as unprivileged `psy`** in this container (no
  `cap_net_raw`) — use `sudo ping` or a TCP-level check instead.
- **Avoid `cmd | grep -q` in scripts.** Under `set -o pipefail`, `grep -q`'s
  early exit SIGPIPEs the producer for a deterministic exit 141.
