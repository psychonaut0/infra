# ct-files

Samba (SMB) plus two copyparty instances serving a web drive over the **same
files**. Samba stays a first-class read-write writer; copyparty is a stateless
front-end that reads the filesystem live on every request.

| Instance | uid | Host port | LAN | Public | Tree |
|---|---|---|---|---|---|
| `copyparty-psy` | 1000 | 3923 | `drive.lan` | `drive.ncsp.dev` | `/mnt/cloud/volumes/samba/data/psy` |
| `copyparty-family` | 1001 | 3924 | `family.lan` | `family.ncsp.dev` | `/mnt/cloud/volumes/samba/data/family` |

- Design: `docs/superpowers/specs/2026-07-28-ct-files-web-drive-design.md`
- Build log: `docs/superpowers/plans/2026-07-28-ct-files-web-drive.md`
- Credentials: `/root/copyparty-credentials.txt` on ct-files (0600). Move these
  into your password manager and delete the file.

## Why two containers

copyparty's `uid` volflag only works when the process runs as root, and this is
a **privileged** LXC where container uid 0 is host uid 0 — not acceptable for an
internet-facing service with an active advisory history. Instead each container
simply *runs as* the uid that owns its tree, so uploads land correctly by nature.

Verified in place: psy uploads land `1000:1000 644` (dirs `755`), family uploads
land `1001:1001 660` (dirs `770`, no world bits). The `chmod_f`/`chmod_d`
volflags **do** apply without the `uid`/`gid` volflags, so no root and no umask
workaround is needed.

## Layout on the CT

```
/opt/stacks/ct-files/copyparty/psy/       → container /cfg  (conf + ah-salt.txt + sessions.db)
/opt/stacks/ct-files/copyparty/family/    → container /cfg
/var/lib/copyparty/psy/                   → container /var/copyparty  (index + thumbnails)
/var/lib/copyparty/family/                → container /var/copyparty
```

`/opt/stacks/ct-files` **is** backed up (ct-backup `FULL_STACK_CTS`).
`/var/lib/copyparty` is **not**, on purpose: it is regenerable, and thumbnails
would otherwise ship to B2 every night. Note that ct-backup's volume-export loop
takes *every* named Docker volume with no exclusion mechanism — that is exactly
why `/cfg` is a host-directory bind mount rather than a named volume.

## THE SALT TRAP

Each instance's argon2 salt lives at `/cfg/copyparty/ah-salt.txt` and password
hashes are computed against it.

- **Never change a container's `user:`** once accounts exist.
- **Never delete the salt file.**
- Hashes are **not portable** between the two instances — separate salts.
- A restore that omits `/cfg` leaves every password broken.

This is demonstrable, not theoretical. The same password hashed against two
different `/cfg` directories yields different hashes:

```
/cfg A → +1gaXXMWLFu6Jm7ah3q5RsW-hGIIdjcw5
/cfg B → +l46xkBJrglP-Ev5g4TrPfPHC0qcUprgX
```

## Regenerating a password

Generate the hash with the **same `/cfg`** and the **same uid** as the running
container, or it will not validate. Needs a real TTY — this will not work through
a non-interactive shell:

```bash
# psy (uid 1000)
ssh -t ct-files 'docker run --rm -it --user 1000:1000 \
  -v /opt/stacks/ct-files/copyparty/psy:/cfg \
  copyparty/ac:latest --ah-alg argon2 --ah-cli'

# family (uid 1001)
ssh -t ct-files 'docker run --rm -it --user 1001:1001 \
  -v /opt/stacks/ct-files/copyparty/family:/cfg \
  copyparty/ac:latest --ah-alg argon2 --ah-cli'
```

It prompts twice, prints a hash starting with `+`, then loops — Ctrl+C to exit.
Paste the hash into the `[accounts]` block of that instance's `copyparty.conf`,
then `docker compose up -d <service>`.

## Real IP — read before touching the proxy config

Every ban rule keys on client IP. A mistake here either disables the bans or
bans your own proxy and locks everyone out for 24 h.

The public and LAN paths are **deliberately not chained**:

```
public: client → CF edge → cloudflared (.6) ──────→ copyparty (.11)
LAN:    client ─────────→ Caddy (.12) ────────────→ copyparty (.11)
direct: LAN client ───────────────────────────────→ copyparty (.11:3923)
```

- From **`.6`** (cloudflared): `CF-Connecting-IP` is set by the Cloudflare edge
  and cannot be forged by the client. Preserved as-is.
- From **`.12`** (Caddy, `.lan` vhosts only): Caddy **overwrites** the header
  with `{remote_host}`, so a LAN client's forged value is discarded.
- **Direct** to `:3923`/`:3924`: untrusted peer, header ignored, socket peer used.

Never set `xff-src` to `lan` — copyparty is directly reachable on the LAN, so any
host could forge the header to evade a ban or ban a third party.

Chaining cloudflared → Caddy → copyparty would be *broken*: Caddy would have to
either preserve a forgeable header or overwrite the real client IP with `.6`.

Verified: a forged `CF-Connecting-IP` through Caddy is discarded, and a forged
header sent directly to the port is ignored.

### Some clients share one ban bucket

What was actually measured (2026-07-28):

| Client | copyparty logs |
|---|---|
| proxmoxmain `192.168.3.2` (same subnet) | `192.168.3.2` — real address |
| ct-mgmt `192.168.3.12` (same subnet) | `192.168.3.12` — real address |
| A workstation on `192.168.1.0/24` reaching ct-files **via the Tailscale subnet router** | `192.168.3.1` — the router, not the client |
| Public, through cloudflared | the true public client IP (verified `93.38.52.48`) |

The third row matters: `BLVCKSmall` (192.168.1.178, WiFi) has **no direct route**
to `192.168.3.0/24` — binding to its LAN address fails outright. Its only path is
the `Main-Gateway` Tailscale node, which advertises both `192.168.1.0/24` and
`192.168.3.0/24`. Traffic therefore arrives as `192.168.3.1`.

Consequence: **every client reaching copyparty through that subnet router shares
one ban bucket.** One such user failing 9 logins bans `192.168.3.1` and locks out
all of them for 24 h. Clear it with `docker restart copyparty-psy`.

Not verified: whether a *wired* host on `192.168.1.0/24` with a direct route logs
its own address. No such client was available to test. Do not assume either way.

The **public** path is unaffected — cloudflared is same-subnet and `CF-Connecting-IP`
carries the true client IP, so internet-facing bans hit real offenders.

### Upload throughput

Do not benchmark uploads from a Tailscale-routed client. Measured from
`BLVCKSmall` over WiFi + Tailscale (relay `fra`), both HTTP PUT and plain ssh ran
at **~0.24 MB/s** — a property of that path, not of copyparty or the storage. For
comparison, a direct write into the psy tree from inside ct-files does
**210 MB/s**, and ct-files sat at 95% idle throughout. Benchmark from a host with
a real route before concluding anything about upload speed.

## Gotchas

- **No trash.** Web deletes are a real `unlink` and **bypass Samba's `recycle`
  vfs**. SMB deletes land in `.deleted` and are recoverable; web deletes are not.
  The maintainer declined a recycle bin on purpose (discussion #1059), arguing it
  creates false confidence where snapshots and backups are the real protection.
  **The `family` tree currently has no off-site backup at all** — ct-backup has
  no mount for `samba/data/family`. Both accounts have delete rights by explicit
  decision; see the spec's *Known limitations* #8 and *Follow-up work*.
- **`PYTHONUNBUFFERED=1` is load-bearing for diagnostics.** copyparty logs to
  stdout and Python block-buffers it when stdout is a pipe; without this,
  `docker logs` sat at 0 bytes for minutes and then flushed 105 KB at once. That
  breaks any check that makes one request and reads the log immediately.
- **An unauthenticated `GET /` returns `200`, not `401`** — body is
  `howdy stranger (you're not logged in)`. `/?ls` returns empty
  `dirs`/`files`/`perms`, and a real path returns `403`. So a monitoring
  condition of `[STATUS] < 400` proves nothing; Gatus asserts the body instead.
- **Do not add `vc-exit`** from upstream's sample config — it shuts the server
  down on an advisory warning instead of just displaying it.
- **Keep FTP and `dk`/dirkeys off.** Both default off; recent advisories hit
  exactly those paths. `dk` also bypasses sibling-volume permission filtering.
- **Keep dedup off.** Hardlinks across mergerfs branches are unsafe, and editing
  a deduplicated file edits every copy.
- **`dbpath` must stay off the mergerfs pool** — SQLite on FUSE corrupts
  (upstream issue #919).
- **`th-mt` and `mtag-mt` are both pinned to 1.** Each defaults to the core
  count, and with two containers on 2 vCPU that meant 4 ffprobe processes
  competing with `smbd`.
- **`th-r-pil` / `th-r-ffi` are pinned lists with `psd` removed** — layered PSDs
  decode to >1 GB inside a 640 MB `mem_limit`. The lists were copied from
  `--help` on v1.20.19, so **re-check after a major image upgrade**; newly added
  extensions are silently omitted until refreshed. 122 CR2 raws are in neither
  list and will not thumbnail (cosmetic, accepted).
- **`usernames` and `pw-urlp` are deliberately at defaults**, per the decision to
  start simple and add Authelia later. Login is password-only (one account per
  instance, nothing to disambiguate) and `?pw=` in URLs stays accepted (disabling
  it breaks some WebDAV clients). Do not "fix" these without reading the spec's
  *Deliberately minimal, by decision*.
- **No 2FA.** copyparty has none natively. Authelia is the planned follow-up.
- **Patch promptly.** `vc-url` is enabled and surfaces new advisories in the
  control panel; upstream ships fixes same-day.
- **Reloading a bind-mounted config needs `restart`, not `up -d`.** For Caddy and
  Gatus, `docker compose up -d <svc>` reports `Running` and does not reload,
  because the service definition is unchanged. Use `docker compose restart <svc>`.

## Mobile

There is no usable native app. The official Android app *PartyUP!*
(`me.ocv.partyup`, F-Droid only) is an upload-only share target and its README
states only the PUT API is implemented, so it does not even get the chunked
upload path. There is no iOS app.

The mobile story is the **responsive web UI** plus **built-in WebDAV** for generic
clients such as Solid Explorer, CX File Explorer, or FolderSync for camera
auto-upload. Point WebDAV clients at the volume, not the server root.

### WebDAV through the tunnel is capped at 100 MB per file

Measured, not inferred:

| Path | 120 MB file |
|---|---|
| `PUT https://drive.ncsp.dev/...` (WebDAV / rclone / curl) | **413** after 1.9 MB, in 0.6 s |
| `PUT http://192.168.3.11:3923/...` (LAN, bypassing Cloudflare) | **201**, all 120,000,000 bytes |

Cloudflare's free plan caps request bodies at 100 MB and rejects at the edge. A
WebDAV `PUT` is a single request body, so it cannot get around this.

The **web UI can**, because its up2k uploader splits files client-side into 96 MiB
chunks — that is the documented reason for the default `--u2sz`. So:

- Camera photos and ordinary documents over WebDAV: fine.
- **Videos over ~100 MB over WebDAV through the public hostname: will fail with 413.**
  Use the web UI for those, or upload over the LAN/Tailscale.

This bounds FolderSync-style camera auto-upload to files under 100 MB when it runs
over the public hostname.
