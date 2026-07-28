# ct-files

Samba (SMB) plus two copyparty instances serving a web drive over the **same
files**. Samba stays a first-class read-write writer; copyparty is a stateless
front-end that reads the filesystem live on every request.

| Instance | uid | Host port | LAN | Public | Tree |
|---|---|---|---|---|---|
| `copyparty-psy` | 1000 | 3923 | `drive.lan` | `drive.<PERSONAL_DOMAIN>` | `/mnt/cloud/volumes/samba/data/psy` |
| `copyparty-family` | 1001 | 3924 | `family.lan` | `family.<PERSONAL_DOMAIN>` | `/mnt/cloud/volumes/samba/data/family` |

- Design: `docs/superpowers/specs/2026-07-28-ct-files-web-drive-design.md`
- Build log: `docs/superpowers/plans/2026-07-28-ct-files-web-drive.md`
- **Private notes** (credentials location, defence posture, accepted risks,
  internal routing): `README.local.md` — gitignored, because this repo is public.

## Why two containers

copyparty's `uid` volflag only works when the process runs as root, and this is
a **privileged** LXC where container uid 0 is host uid 0 — not acceptable for an
internet-facing service. Instead each container
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

This is demonstrable, not theoretical: the same password hashed against two
different `/cfg` directories yields different hashes. Evidence in
`README.local.md`.

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

Per-client detail, and which clients share a ban bucket, is in `README.local.md`.

### Upload throughput

Do not benchmark uploads from a Tailscale-routed client. Measured from
`BLVCKSmall` over WiFi + Tailscale (relay `fra`), both HTTP PUT and plain ssh ran
at **~0.24 MB/s** — a property of that path, not of copyparty or the storage. For
comparison, a direct write into the psy tree from inside ct-files does
**210 MB/s**, and ct-files sat at 95% idle throughout. Benchmark from a host with
a real route before concluding anything about upload speed.

## Share links

**`shr` must be set or the feature does not exist.** `--shr` defaults to empty,
which disables sharing entirely and leaves the UI with nothing to render — the
button is simply absent, with no error anywhere. This was missed on first deploy.
Confirm it is on by checking the capability the server sends to a *browser*
(curl gets a plain-text view, not the web app):

```bash
curl -s -A "Mozilla/5.0" -H "PW: <password>" http://192.168.3.11:3923/ \
  | grep -o '"have_shr":[^,]*'
# expect: "have_shr": "/share/"
```

`shr-site` is set to the public URL per instance. Without it, a link created
while browsing `drive.lan` embeds that hostname and is useless to anyone outside
the LAN.

Shares live in `/cfg/copyparty/shares.db`, inside the backed-up `/cfg`, so they
survive restarts and restores.

### Creating one in the UI

1. Optionally select files. Selecting nothing shares **the folder you're in**.
2. Click **📨 share** in the file-manager toolbar.
3. Fill in the dialog: **name** (random if blank), **passwd** (optional),
   **expiry** with units of min / hours / days, or *eternal*.
4. **✅ create share** — then Enter/OK copies the link to your clipboard.

Limitation, straight from the UI's own text: *"you cannot select more than one
folder, or mix files and folders in one selection."* Share a parent folder
instead.

Sharing is the feature with the weakest upstream track record, which is why the
advisory feed is enabled. Details in `README.local.md`.

## Gotchas

- **No trash.** Web deletes are a real `unlink` and **bypass Samba's `recycle`
  vfs**. SMB deletes land in `.deleted` and are recoverable; web deletes are not.
  The maintainer declined a recycle bin on purpose (discussion #1059). Backup
  coverage and the accepted risks are in `README.local.md`.
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
- **Keep FTP and `dk`/dirkeys off.** Both default off. `dk` also bypasses
  sibling-volume permission filtering, which this design relies on.
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
- **Some auth options are deliberately left at their defaults.** Do not change
  them without reading `README.local.md` and the spec's *Deliberately minimal, by
  decision* — the choices are intentional, not oversights.
- **Patch promptly.** `vc-url` is enabled and surfaces new advisories in the
  control panel; upstream ships fixes same-day.
- **Two opposite compose traps, learned the hard way.** A changed *bind-mounted
  config file* needs `docker compose restart <svc>` — `up -d` reports `Running`
  and does not reload, because the service definition is unchanged. A changed
  *`.env` / `env_file`* needs `docker compose up -d <svc>` — `restart` reuses the
  existing container along with its baked-in environment, so the new variable
  never arrives. Using the wrong one fails silently in both directions.

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
| `PUT https://drive.<PERSONAL_DOMAIN>/...` (WebDAV / rclone / curl) | **413** after 1.9 MB, in 0.6 s |
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
