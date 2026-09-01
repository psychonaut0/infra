# ct-dev — remote development container

> Deployed to `~/CLAUDE.md` on ct-dev. `<WORK_REPO>` is substituted at provision
> time from `CLAUDE.local.md` (this infra repo is public).

## Machine
- Unprivileged LXC, **VMID 116 on proxmoxmain**, Debian 13 (Trixie)
- **IP:** 192.168.3.19 · MagicDNS `ct-dev` on the tailnet
- 6 vCPU, 12 GB RAM, 4 GB swap, 120 GB disk
- **Role:** always-on remote workspace. Long-lived agent sessions live here so
  they survive the workstation sleeping or the SSH connection dropping.

## What this machine is NOT

It is a headless container, not a workstation. The global BLVCKFlow context does
**not** apply here. None of the following exist:

- No desktop — no Hyprland, no Wayland, no SDDM, no GUI of any kind.
- **No `z13ctl`, no `asusctl`, no hardware control.** There is no hardware.
- No local LLM. `llama-server` on `127.0.0.1:8088` is BLVCKFlow only.
- No `nvm` (Node is installed system-wide).
- **No `sudo -A` / askpass.** Plain `sudo`, or root directly.
- No Thunar, no kitty, no stow-managed dotfiles.

## Sessions

**Always work inside `tmux`** — it is the persistence layer, not a preference.
Detaching is how work survives you; a bare SSH session dies with the connection.

- `claude --resume` picks up prior sessions (state migrated from the workstation).
- `tmux` is the only persistence mechanism on this box — there is no headless
  batch runner and no notification service; everything runs inside an
  attached (or detached) `tmux` session.

## Workspace

```
~/Documents/work/travelware/<WORK_REPO>
```

**This path is load-bearing and must not change.** It is byte-identical to the
workstation so that (a) the `includeIf gitdir:~/Documents/work/travelware/` work
git identity applies automatically, and (b) Claude Code's project directory
encodes to the same name, which is what lets sessions migrate between machines.
Never set a repo-local git identity.

## Toolchain

Go 1.26 · Node 26 · Bun 1.3.13 · Docker + compose · tmux · git · git-lfs

AWS tooling is four separate binaries — `aws` (CLI v2), `session-manager-plugin`,
`pgcli`, and the project's own **`travelogue` CLI** (a Go binary distributed via
Bitbucket downloads, self-updating with `travelogue update`; it refuses to run if
`aws` is not on PATH). The `travelogue` CLI is *not* part of the monorepo and is
not installed by `bun install`.

Unlike BLVCKFlow, these are installed **system-wide** here — root is available,
so the userland `~/.local` workaround that machine uses is unnecessary.

## AWS access

Login via `travelogue login` or the repo's `bun run login`. Credentials are
short-lived SSO sessions; there are no long-lived access keys on this box, and
none should ever be added.

Because ct-dev is headless, use the **`--no-browser`** flow — the CLI prints a
URL and code to complete on whatever device you are actually sitting at.

**Do not pin a repo-local `AWS_CONFIG_FILE`.** Repo scripts set `AWS_PROFILE`
themselves; profile-name scoping *is* the project scoping, and overriding the
config path breaks the `travelogue` CLI.

## Durability

**Nothing here is backed up.** ct-backup skips VMID 116 deliberately so work
source never reaches personal Backblaze storage.

The base setup is reproducible instead: provisioning lives in the infra repo at
`stacks/ct-dev/`. Recovery is *recreate and re-run*, not *restore*.

**Consequence: uncommitted work dies with the container.** Push early, push
often. Committed work is safe in the work GitHub org; nothing else is.
