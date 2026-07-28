# ct-files Web Drive — Design

**Status:** Spec — 2026-07-28
**Goal:** Replace FileBrowser on ct-files with a Drive-like web front-end over the two existing Samba trees, reachable from the phone and remote PCs over a public hostname, while Samba keeps full read-write access to the same plain files from the home PC.

## Background

ct-files (VMID 107, `192.168.3.11`) currently runs two user-facing services:

1. **Samba** (`dperson/samba`) — two shares out of `/mnt/cloud/volumes/samba/data/`: `psy` (uid/gid 1000) and `family` (uid/gid 1001). Both use `force user`/`force group`, a recycle bin (`.deleted`, `recycle:versions=yes`, `recycle:maxsize=0`), and the `catia fruit recycle streams_xattr` vfs stack. This service is working well and is **not** changing.
2. **FileBrowser** (`filebrowser/filebrowser:latest`, port 8080) — exposes the *entire* `/mnt/cloud` pool as `/srv`, including `mediaserver`, `minecraft`, `games`, `satisfactory`, `dump` and `lost+found`. Single-user auth in its own SQLite db, disconnected from the Samba user split.

FileBrowser is being replaced because of three specific complaints, in the owner's words: **auth & permissions**, **missing features**, and it being the **wrong tool** — the ask is "something like Google Drive, but not the full Nextcloud suite, just a drive for uploading under my psy samba share, so I can use it on the phone/other PCs via web/app and locally/home PC via samba". Notably, *UI polish was not among the complaints.*

Upstream `filebrowser/filebrowser` is also end-of-life.

### Measured starting state (2026-07-28)

| Fact | Value |
|---|---|
| `psy` tree | 4,737 files / 702 dirs / 51 GB (+ 17 GB in `.deleted`) |
| `family` tree | 91,212 files / 281 GB (+ 21 GB in `.deleted`) |
| Thumbnailable content | ~66,476 JPEGs, 1,002 videos, 372 PDFs, 141 PSDs, 122 CR2 raws, 1,178 wavs |
| AppleDouble `._*` files on disk | **0** |
| POSIX xattrs in use on the trees | **0** (despite `streams_xattr` being configured) |
| ct-files rootfs | 3.9 GB, 1.8 GB used, **1.9 GB free (49%)** |
| ct-files CPU / RAM | 1 vCPU (`sched_getaffinity` = 1), 1024 MB, 512 MB swap |
| Live RAM use | Samba 120 MB, filebrowser 13.5 MB, portainer-agent 16 MB; 862 MB available |
| Docker images on disk | 337.8 MB |
| mergerfs policy | `category.create=mfs` — **non**-path-preserving, so `rename()` succeeds across branches |
| Cloudflare Tunnel | token-managed (`TUNNEL_TOKEN`), no local config file |

## Requirements

**In scope:**

- Drive-like web UI over the two existing Samba trees, on the same plain files — no separate silo, no import step.
- Two accounts mirroring the Samba split: `psy` sees only the `psy` tree, `family` sees only the `family` tree. Neither can see or traverse into the other, enforced server-side.
- Web uploads must land owned by uid 1000 (psy tree) / uid 1001 (family tree), with modes consistent with those already present on each tree, so Samba users retain full ownership and in-place edit.
- Files written over SMB must appear in the web UI without a manual rescan; files uploaded over the web must be readable, renamable, deletable **and modifiable in place** over SMB.
- Access-only. No sync daemon, no offline mirroring, no virtual filesystem client.
- Reachable from outside the house over a public hostname via the existing Cloudflare Tunnel — no port-forward, no UniFi change.
- Uploads from a phone must work for files well over 100 MB through that tunnel.
- Share links with password and expiry.
- Previews for images, video (with seek), PDF, audio, text.
- Must not corrupt, rewrite, relocate or stamp metadata onto the existing 332 GB of data. Removing the service must leave the trees byte-identical.

**Out of scope:**

- Changing anything about Samba. Its config, shares, masks and recycle behaviour stay exactly as they are.
- Full-suite groupware (calendar, contacts, office, mail). Explicitly rejected by the owner.
- Two-way sync clients, desktop virtual drives, camera-roll auto-sync as a first-party feature. (A generic WebDAV client such as FolderSync can do camera auto-upload; that is a user-side choice, not part of this deployment.)
- Native mobile apps. Accepted as a known gap — see *Known limitations*.
- TOTP/2FA on the public hostname. Deferred to a follow-up (see *Follow-up work*).
- Exposing any part of `/mnt/cloud` other than the two Samba trees. The current FileBrowser behaviour of serving the whole pool is a defect being fixed, not a feature being preserved.

## Tool selection

31 candidates were surveyed, 7 deep-evaluated against the constraints above, and the 3 finalists put through adversarial review against source code (not just docs).

### Rejected

| Candidate | Disqualifying reason |
|---|---|
| **Seafile** | Opaque block/chunked storage. No Samba interop by design. |
| **oCIS + PosixFS** | PosixFS is officially *"experimental… should not be used in production"*. mergerfs/FUSE absent from the supported-filesystem list. No per-user uid mapping. |
| **OpenCloud** | Docs require shutting the service down before any external add/remove/rename (non-collaborative mode is the install default, issue #220). Samba writes 24/7 — a continuous contract violation. Collaborative mode needs inotify on an unsupported filesystem. No external-storage feature (issue #1756, open since 2025-11-01), so it cannot be pointed at the existing paths; it creates and owns `<POSIX_ROOT>/users/<space>/`. "Assimilation" stamps `user.oc.*` xattrs onto every file plus sidecar metadata files, an irreversible mutation that becomes load-bearing (issue #1894: instance errors after a restore dropped xattrs). One uid per instance. No on-demand rescan — repair means restart. Plus CVE-2026-23989 (CVSS 8.2, unauthenticated public-link scope bypass, inherited from ownCloud/oCIS code). |
| **Nextcloud + `files_external` Local** | Uploads land as the PHP process uid (`www-data` 33/82) with no per-tree control; `.part`+rename flips Samba-created files to that uid on overwrite. Web-UI deletes copy the file onto the rootfs (`Trashbin.php::move2trash`). |
| **Filestash** | Serves its entire admin API unauthenticated until an admin password is set (`middleware.AdminOnly` short-circuits on empty `auth.admin`); verified by retrieving the full private config unauthenticated. Single uid per process. |
| **SFTPGo (OSS)** | No chunked/resumable upload — TUS is Enterprise-gated (feature request #1801 open since 2024-11-03, no maintainer response). Cloudflare's free plan caps request bodies at **100 MB** and returns 413 at the edge, so any phone video over 100 MB is hard-blocked with no OSS workaround preserving this topology. Upload batches also abort on first failure. |
| **FileBrowser Quantum** | Survived review and is the prettiest option, but: no chown at all (feature request #2384 closed unimplemented) so two containers are required anyway; **10 advisories Feb–Jun 2026, 4 Critical, three in the public-share path** that would be internet-exposed, including unauthenticated path traversal enabling arbitrary file *deletion* (CVE-2026-44542) and arbitrary move/copy/rename (CVE-2026-48777); web delete is `os.RemoveAll` — permanent, while SMB deletes go to Samba's recycle; web copy resets mtime and drops xattrs/ACLs; `cacheDir` defaults to a *relative* path inside the container's writable layer and the code's own `minRecommendedGB` is 20; `maxArchiveSize` defaults to 20 GB and folder-zip spools the whole archive to disk before serving; a config-breaking v2 is published with `prerelease: false`. |

### Selected: copyparty

`9001/copyparty`, MIT, the original and unambiguously the live project. 45,910 stars, 1,010 commits and 45 releases in the last 12 months, latest v1.20.19 (2026-07-27), same-day security fixes.

It wins on the constraints that actually bind here:

1. **No filesystem watching anywhere.** A repo-wide grep for inotify/pyinotify/watchdog/fsevents returns no filesystem watching (the one `opendb_watchdog` hit is a thread *name*). Directory listings are built by live `os.scandir` per request — `httpcli.tx_browser` → `vfs.ls` → `VFS._ls` → `util.statdir` (util.py:3530-3555) — and **never consult the database**. Out-of-band Samba writes therefore appear instantly, and mergerfs's unreliable FUSE inotify is a non-issue *by construction* rather than by configuration. The SQLite index is purely auxiliary, for search only.
2. **Chunked, resumable, integrity-checked uploads sized for Cloudflare.** `--u2sz` defaults to 96 MiB chunks specifically to stay under Cloudflare's request-size cap; "no filesize limit even on Cloudflare" is a documented property, and there is a dedicated Cloudflare Tunnel section in the README. Client hashes each chunk; interrupted uploads auto-resume. Drag-drop of whole folders, recursively.
3. **Real per-volume ownership control.** `uid`/`gid`/`chmod_f`/`chmod_d` volflags are implemented as actual `os.fchown`/`os.chown`/`os.fchmod` calls (`set_fperms`/`set_ap_perms`, util.py:2923-2936), covering uploads, new directories, and cross-volume move/copy.
4. **A real jail.** `VFS.get` (authsrv.py:597) runs `relchk` then `undot` (lexical `..` collapse, util.py:2423) **before** any filesystem mapping, then checks the permission bit per operation. Sibling volumes are filtered from listings by permission (`virt_vis` in `VFS._ls`), and the control panel renders only volumes the user can reach.
5. **Share links with password and expiry**, plus optional visitor upload, `--shr-who`/`--shr-adm` gating, and revival of recently expired shares.
6. **Small footprint, no external services.** SQLite in-process, no DB, no Redis. Docs state under 100 MiB private memory; the `ac` image is 163 MiB.

Accepted costs, stated plainly:

- **The UI is dense and brutalist, not visually Drive-like.** The maintainer's own comparison concedes FileBrowser looks nicer. Functionally it is ahead on every axis (navpane tree, grid/list, F2 batch rename, cross-tab cut/paste, multi-select, tag search, in-browser CBZ/markdown/media). Since UI polish was not one of the stated complaints, this is the right trade.
- **Not an installable PWA.** No `manifest.json` and no service worker anywhere in `copyparty/web/`; "add to home screen" is a plain bookmark.
- **14 security advisories, 10 with CVEs**, overwhelmingly XSS, plus two share-scoping leaks (CVE-2025-58753, CVE-2026-32108) and one 2023 path traversal. Disclosure handling is excellent and fixes ship same-day, but this is not a set-and-forget deployment — see *Security posture*.
- **Bus factor 1.** ~80% of recent commits are by one author. Mitigated by MIT licensing and by the data being plain files, so exit cost is near zero.
- **No native 2FA or OIDC.** Header-trust IdP integration only.

## Architecture

### Two containers, one per uid

```
                       ┌─ copyparty-psy     uid 1000, :3923 ─→ /mnt/cloud/volumes/samba/data/psy
ct-files (192.168.3.11)┤
                       └─ copyparty-family  uid 1001, :3924 ─→ /mnt/cloud/volumes/samba/data/family
                       └─ samba (unchanged)         :139/445 ─→ both trees
```

Each container runs *as* the uid that owns its tree, so uploads land correctly by nature — no chown, and critically **no root**. copyparty's `uid` volflag can only be set when running as root (README:1918), and ct-files is a privileged LXC where container uid 0 is host uid 0; running an internet-facing Python web app with 14 advisories as host root is rejected.

Each container serves exactly one volume with exactly one account and no root volume, so there is no path a user can address above their own tree.

| | copyparty-psy | copyparty-family |
|---|---|---|
| `user:` | `1000:1000` | `1001:1001` |
| Host port | 3923 | 3924 |
| Volume | `[/] /w` → `…/samba/data/psy` | `[/] /w` → `…/samba/data/family` |
| Account | `psy`, `accs: {rwmd: psy}` | `family`, `accs: {rwmd: family}` |
| `chmod_f` / `chmod_d` | `644` / `755` | `660` / `770` |
| LAN hostname | `drive.lan` | `family.lan` |
| Public hostname | `drive.ncsp.dev` | `family.ncsp.dev` |
| `/cfg` volume | `copyparty-psy-cfg` | `copyparty-family-cfg` |

Bridge networking with explicit port mapping — copyparty needs no mDNS, so `network_mode: host` is unnecessary and avoided.

#### File modes

The `chmod_f`/`chmod_d` values above are derived from the modes **actually observed on disk**, not from the `create mask`/`directory mask` values in `smb.conf` — the global `force create mode = 0664` / `force directory mode = 0775` override those masks, so the masks are misleading. Measured 2026-07-28:

| Tree | Files | Dirs |
|---|---|---|
| `psy` | `644` (2,137) + `764` (65) | `755` |
| `family` | `770` (39,387) + `764` (24,604) | `770` |

- **psy → `644`/`755`** reproduces the dominant existing modes exactly.
- **family → `660`/`770`** is a deliberate one-bit divergence: dirs match exactly, and `660` gives identical effective access to the existing `770` for every real purpose while not marking data files executable. The family tree is already mixed (`770` and `764` both present in volume), so no consistency is lost. Both trees are group-private with no world access, which is preserved.

In both cases the container's process uid *is* the file owner, so owner permissions alone are sufficient for copyparty, and Samba's `force user` makes the same identity the owner on the SMB side. Group and world bits are therefore not load-bearing for correctness — only for privacy, which is why no world bits are granted on `family`.

**Rejected alternative:** a single container with `user: "1000:1000"` + `group_add: ["1001"]` and a `gid: 1001` volflag on the family tree. This works bidirectionally (verified against the real `smb.conf`: copyparty reaches family's `0770 1001:1001` files via the group bit, and Samba's `force user = family` modifies copyparty's `664 *:1001` uploads via the group bit) and would give one login page and one hostname. It was rejected in favour of literally-correct ownership.

**Also rejected:** path-based routing on a single hostname via `--rp-loc`, which would avoid the second public hostname but makes share links and WebDAV paths fragile.

### Consequences of the two-container shape

- **Two internet-facing login pages** instead of one. Accepted.
- **Two argon2 salts.** The salt lives in `$XDG_CONFIG_HOME/copyparty` = `/cfg`, and is per-uid. Password hashes are **not portable** between the containers, and changing a container's `user:` after accounts exist silently invalidates every hash (issue #1421, reported from a Debian 13 Proxmox LXC — the same platform). The uid must be pinned before accounts are created.
- **No cross-tree search or shares.** No loss: the scoping requirement already forbids cross-tree visibility.
- **Independent ban tables** — a benefit. Failed logins against the family instance cannot lock anyone out of the psy instance.

### Storage, index and disk

`pct resize 107 rootfs +12G` → 16 GB. At 1.9 GB free there is no room for two indexes plus a thumbnail cache, and the CT that serves Samba must not run out of disk.

- **`dbpath` on the CT rootfs.** SQLite must not live on FUSE — issue #919 is a real "database disk image is malformed" report on a FUSE mount, and the maintainer's diagnosis specifically asked about mergerfs in the path.
- **`hist` (thumbnails, audio transcodes) also on the rootfs**, now that it is resized. Bounded by GC: `th-maxage 604800` (7 d) + `th-clean 43200` (12 h) — the cache is lazily populated and self-expiring, so peak size tracks what is actually browsed, not the whole tree. Pregeneration stays off.
- **`no-idx` regex covering `/\.deleted/` and `/\.hist/`.** On Linux the default exclusion list is empty (`noidx = APPLESAN_TXT if MACOS else ""`, `__main__.py:1879`), so without this the indexer walks all 38 GB of recycle data.
- **`--no-hash '.'`** — index name/size/mtime only. Full search still works; only search-by-content-hash and dedup are lost, and neither is wanted. Without it the first scan reads all 332 GB through FUSE.
- **Dedup stays off** (it already is by default: `--dedup`/`--hardlink`/`--reflink` are all opt-in). Hardlinks across mergerfs branches are unsafe, and README:1711 warns that editing a deduplicated file edits every copy.
- **`.deleted` and dotfiles are hidden by default** — `-ed` and `--dotsrch` are both off — so Samba's recycle bins do not clutter the UI without any extra config.

Nothing is written into the data trees except files the user uploads. No xattrs (`--db-xattr` is opt-in), no sidecar metadata, no relocation. Removing copyparty leaves the trees byte-identical.

### Exposure, auth and real-IP

**Two independent paths, deliberately not chained.** Public traffic bypasses Caddy entirely — matching the existing `portfolio.ncsp.dev` precedent, where cloudflared proxies straight to the app:

```
public: client → CF edge → cloudflared (.6) ────────────→ copyparty (.11)
LAN:    client ─────────→ Caddy (.12) ──────────────────→ copyparty (.11)
direct: LAN client ─────────────────────────────────────→ copyparty (.11:3923)
```

This matters. A chained `cloudflared → Caddy → copyparty` design is *broken* for real-IP: Caddy would have to either preserve `CF-Connecting-IP` (making it forgeable by any LAN host that can reach the public vhost with a spoofed `Host` header) or overwrite it with `{remote_host}` (destroying the real client IP, replacing it with cloudflared's `.6`). Splitting the paths gives each trusted upstream exactly one well-defined behaviour:

- `xff-hdr: cf-connecting-ip`, `xff-src: 192.168.3.6, 192.168.3.12`. **Never `lan`** — copyparty stays directly reachable on the LAN at its port, so a `lan`-wide trust would let any LAN host forge `cf-connecting-ip` to evade a ban or ban an arbitrary third party. copyparty's own code warns about exactly this (httpcli.py:499-500, `docs/xff.md`).
- **From `.6` (cloudflared):** `CF-Connecting-IP` is set by the Cloudflare edge and cannot be forged by the client. Preserved as-is → real public client IP.
- **From `.12` (Caddy, LAN vhosts only):** Caddy **overwrites** with `header_up CF-Connecting-IP {remote_host}`, so a LAN client's forged header is discarded and the real LAN IP is used.
- **Direct to `:3923`:** the peer is neither trusted IP, so copyparty ignores the header and uses the socket peer. Safe by default.

Caddy therefore only ever serves the `.lan` hostnames; the public hostnames exist solely as tunnel ingress rules. When Authelia lands it will likely restructure this — noted in *Follow-up work*.
- `--rproxy` defaults to `9999999`, deliberately out of bounds, so real-IP detection fails behind *any* proxy until configured. The startup log prints the recommended flags for the detected topology (httpcli.py:463-472) and must be read during deployment.
- **This is load-bearing.** The default ban rules (`--ban-pw 9,60,1440`, `--ban-403 9,2,1440`, `--ban-422 9,2,1440`, `--ban-404`, `--loris 60`) all key on client IP. Misconfigured, every request looks like the proxy and the first exploit scan bans the proxy for 24 hours, locking out the entire internet.
- **`ah-alg: argon2`.** The default is `none` — plaintext passwords in `copyparty.conf`. `py3-argon2-cffi` is baked into the `ac` image. The flow is two-step: run with `--ah-cli`, then paste the resulting hashes into the config.
- **`/cfg` must be a persistent named volume** — it holds the argon2 salt and `sessions.db`.
- `vc-url: https://api.copyparty.eu/advisories` — the built-in advisory feed, default-disabled. With 45 releases in 12 months and same-day security fixes this is the cheapest way to meet the patch-promptly obligation.
- **FTP stays off** (default). The advisory dated 2026-07-27 is an FTP volume-jail escape.
- **`dk`/dirkeys stay off.** Enabling them adds child volumes to `virt_vis` *regardless of permissions* in `VFS._ls`, and that code path is where GHSA-x5pq-m9p8-f4vx (2026-07-06) lived.
- **No 2FA in this phase.** Defence is argon2 + the IP ban system + correct real-IP. Cloudflare Access is explicitly **not** used — it breaks WebDAV and non-browser clients, which are the mobile story.

#### Deliberately minimal, by decision

The owner's instruction is to start simple on copyparty security and add Authelia later. The following are therefore left at their **defaults**, as an explicit choice rather than an oversight:

| Left at default | Effect | Why it's acceptable now |
|---|---|---|
| `usernames` (off) | Login is password-only, no username field | Each container has exactly **one** account, so there is nothing to disambiguate. Simpler for the family member, and logs are unambiguous anyway. |
| `pw-urlp` (accepts `?pw=`) | Credentials can appear in access logs and `Referer` headers | Avoids breaking WebDAV clients, which are the mobile story. Revisit if logs are ever shipped off-box. |
| Ban rules | Stock `ban-pw`/`ban-403`/`ban-422`/`ban-404`/`loris` | The stock values are already strict (9 bad passwords/hour → 24 h ban) and need no tuning. |

The three items that are **not** negotiable down to defaults, because omitting them is actively harmful rather than merely less-hardened — and each is a single line:

1. **`ah-alg: argon2`** — the alternative is literal plaintext passwords in a config file on an internet-facing host.
2. **`xff-hdr`/`xff-src`/`rproxy`** — this is *correctness*, not hardening. Wrong, and the stock ban rules ban the proxy and lock out the internet for 24 h.
3. **`vc-url`** — the only early warning about new advisories during the window before Authelia exists.

### Resources

Bump ct-files to **2 vCPU / 2048 MB / 16 GB rootfs**.

- **Thumbnailing is single-threaded per worker and CPU-bound.** On 1 vCPU, opening a large photo folder is minutes of saturated CPU that starves `smbd` in the same container. The second core is the mitigation.
- **`th-mt: 1` on both containers.** copyparty derives `CORES` from `len(os.sched_getaffinity(0))`, which correctly reads the cgroup (verified: `nproc`=1 today, and Proxmox uses cpuset rather than CFS quota). At 2 vCPU each container would otherwise default to 2 thumbnail threads — 4 threads on 2 cores with `smbd` competing. At 1 vCPU this setting would have been a no-op; after the bump it is required.
- **Drop `psd` from `--th-r-pil`.** 141 layered PSDs are in both default decoder lists and a 70 MB layered PSD decodes to well over 1 GB. Excluding them is better than relying on a memory ceiling to catch it.
- **`mem_limit: 640m` per container** as a backstop, not a primary control. Budget: ~150 MB × 2 copyparty + 120 MB Samba + 16 MB portainer-agent ≈ 440 MB steady, against 2048 MB.
- **Use the `ac` image** (Pillow + FFmpeg), not `iv` — libvips was demoted to last-fallback for excessive RAM in v1.20.19. Also relevant: issue #1556, high RAM during animated-AVIF thumbnailing.
- **`th-bwrap` off** (bubblewrap generally fails inside LXC) and **mimalloc left disabled** — the official compose ships it deliberately broken, and enabling it doubles RAM for 3× zip speed.
- 122 CR2 raws are in neither default decoder list and will silently not thumbnail. Cosmetic, accepted.
- 1,178 wav files will generate audio transcodes and waveform caches into `hist`.

## Migration

Parallel run, then cut over:

1. Resize rootfs, bump CPU/RAM, restart ct-files.
2. Deploy both copyparty containers on :3923/:3924 with FileBrowser still running on :8080.
3. Create accounts, capture argon2 hashes, verify locally by IP.
4. Add `drive.lan` / `family.lan` via `infra dns add`.
5. Add `drive.ncsp.dev` / `family.ncsp.dev` in the **Cloudflare dashboard** — the tunnel is token-managed, so this step produces **no repo diff** and is easy to lose. It is called out as a manual step in the plan.
6. Run the full verification matrix below.
7. Remove the `filebrowser` service and retire `files.lan`. Keep the `filebrowser-db` volume for a grace period before deleting.

**Rollback:** stop and remove the containers. copyparty writes nothing to the trees but uploaded files, so rollback is complete and instant; FileBrowser can be restored from the retained compose entry and volume.

### Fleet integration

| Surface | Change |
|---|---|
| `stacks/ct-files/docker-compose.yml` | Replace `filebrowser` with `copyparty-psy` + `copyparty-family` |
| `stacks/ct-files/copyparty/` | New: `psy.conf`, `family.conf` — **gitignored**, with `psy.conf.example` / `family.conf.example` committed. This repo is sanitized for public release and already gitignores the Mosquitto `passwd` file; argon2 hashes are strong but password hashes do not belong in a public repo. The examples carry every flag with placeholder hashes. |
| `stacks/ct-mgmt/Caddyfile` | Add `drive.lan`, `family.lan` with the `CF-Connecting-IP` override; remove `files.lan` |
| `stacks/ct-mgmt/gatus/config.yaml` | Replace the FileBrowser check with two checks. The condition must assert **alive *and* enforcing auth**, not merely `[STATUS] < 400` — an unauthenticated 200 would otherwise mask a broken ACL. Determine the actual unauthenticated response during deployment: if copyparty returns `401`, use `[STATUS] == 401`; if it serves a `200` login page instead, use `[STATUS] == 200` plus a `[BODY]` condition matching the login form. Do not settle for a bare reachability check. |
| `stacks/ct-mgmt/dashboard-src/src/services.js` | Replace the FileBrowser tile with two tiles |
| `stacks/ct-backup/scripts/pre-backup.sh` | Add both `/cfg` volumes **and** add ct-files to the full-`/opt/stacks`-tree list. Two distinct gaps: (a) without the argon2 salt and `sessions.db` from `/cfg`, a restore leaves every password broken; (b) ct-files is currently **not** in the full-tree list (only ct-ha, ct-tools, ct-games, ct-workout are), so the gitignored `*.conf` files holding the account hashes would not be captured by the repo *or* the backup. |
| `CLAUDE.md`, `docs/hardware.md` | Update ct-files role, ports, resources, storage notes |

ct-files already has `ALLOW_EXPORT_VOLUMES=1` in `pre-backup.sh`, so the volume export path exists. Per the standing lesson from ct-workout, the backup script change must be **deployed to `/usr/local/bin` and verified by an actual run**, not merely committed.

## Verification

| Check | Method | Pass criterion |
|---|---|---|
| Ownership, psy | Upload via web, `stat` | `1000:1000`, mode `644`; new dirs `755` |
| Ownership, family | Upload via web, `stat` | `1001:1001`, mode `660`; new dirs `770` |
| Samba → web visibility | Write a file over SMB, reload the web listing | Appears immediately, no rescan |
| Web → Samba, in-place edit | Upload via web, then modify that file over SMB | Succeeds |
| Web → Samba, delete/rename | Rename and delete a web-uploaded file over SMB | Succeeds |
| Jail, psy | Attempt to reach the family tree and `/mnt/cloud` as `psy` | 403/422, no traversal |
| Jail, family | Same, inverted | 403/422 |
| Recycle bins hidden | Browse and search both trees | No `.deleted` content visible |
| **Large upload through tunnel** | Push a **>100 MB video from the phone** via `drive.ncsp.dev` | Completes — this is the test that eliminated SFTPGo |
| Upload resume | Interrupt a large upload, resume | Resumes rather than restarting |
| Real-IP / ban isolation | Fail 9 logins from one device | That device banned; a second device on a different IP unaffected |
| Ban table independence | Fail 9 logins on the family instance | psy instance unaffected |
| Share link | Create with password + expiry, open externally | Password enforced, expiry honoured |
| Previews | Image, video seek, PDF, audio, text | All render; video seeks (Range/206) |
| Thumbnail load under stress | Open a large photo folder, watch CPU/RAM, test SMB concurrently | Samba stays responsive, no OOM |
| Index scope | Check index size and scan duration | `.deleted` excluded, no content hashing |
| Backup | Run a real ct-backup cycle, then restore-test a `/cfg` volume | Both `/cfg` volumes present, `/opt/stacks/ct-files` captured including the gitignored `*.conf`; passwords survive the restore |

## Known limitations

Accepted, documented so they are not rediscovered as surprises:

1. **No usable native mobile app.** The official Android app *PartyUP!* (`me.ocv.partyup`, F-Droid and GitHub Releases only — not Play Store) is an upload-only share target; its README states *"only the PUT API is implemented for now so there is no resumable uploads yet"*, so it does not even get the chunked-upload path. There is no iOS app, only official Shortcuts for upload. The mobile story is: **responsive mobile web UI** for browsing and uploading (this is where chunked/resumable upload works), plus **built-in WebDAV** for generic clients such as Solid Explorer, CX File Explorer, or FolderSync for camera auto-upload. WebDAV clients should be pointed at the volume, not the server root.
2. **Not an installable PWA.** Home-screen icon is a plain bookmark with no offline shell.
3. **No 2FA.** See below.
4. **Requires prompt patching.** 14 advisories to date, mostly XSS, two of them share-scoping leaks in precisely the feature being used. `vc-url` is enabled to surface new ones.
5. **PDF has no built-in viewer.** Files are served with correct content types so the browser's native viewer handles them; to be confirmed during verification.
6. **Two hostnames, two logins.** A consequence of the chosen ownership model.
7. **Aesthetics.** The UI is functional and dense, not a polished Drive clone.
8. **Web deletes are permanent and, on the `family` tree, currently unrecoverable.** Accepted by explicit decision — both accounts get `rwmd`, delete included.

   copyparty has **no trash**. The maintainer declined the feature on purpose (discussion #1059), arguing a recycle bin creates false confidence where snapshots and backups are the real protection, and that deleted-but-retained files are a privacy problem. A copyparty delete is a real `unlink`, so it **bypasses Samba's `recycle` vfs** — SMB deletes land in `.deleted` and are recoverable, web deletes are not.

   For the `psy` tree the fallback is the nightly restic backup to B2 (`mp1` → `/backup-sources/samba-psy`). **For the `family` tree there is no fallback at all:** ct-backup has no mount for `samba/data/family`, so those 281 GB / 91,212 files have no off-site copy, on a pool that `docs/hardware.md:106` already flags as "single-point-of-failure on two non-RAID HDDs". The 21 GB `.deleted` recycle bin is the only safety net, and web deletes skip it.

   This exposure pre-dates copyparty — drive failure is the larger risk and is unaddressed either way. Granting web delete rights adds one more route to the same loss. Recorded as accepted; the fix is in *Follow-up work* and was deliberately kept out of this project's scope.

## Follow-up work

Out of scope here, recorded so the decisions are not lost:

- **Add the `family` tree to ct-backup.** *Recommended, and the highest-value item on this list.* One read-only mount mirroring the existing `samba-psy` pattern:

  ```
  pct set 109 -mp10 /mnt/cloud/volumes/samba/data/family,mp=/backup-sources/samba-family,ro=1
  ```

  281 GB in Backblaze B2 is roughly **$1.50–2/month** at $6/TB. This closes the gap described in *Known limitations* item 8, and would make web deletes recoverable from restic as a side effect. Explicitly deferred to keep this project scoped — not because it isn't worth doing.
- **Authelia in front of both containers** for real TOTP 2FA. copyparty supports header-trust IdP integration with official compose examples for Authelia and authentik. Planned by the owner as a later step; this spec deliberately ships without it (see *Deliberately minimal, by decision*).
- **An `xbd` before-delete trash hook**, if a recycle bin is ever wanted without relying on backups. Confirmed viable: most hook types, `xbd` included, abort the action when the hook exits non-zero **provided the `c` flag is given**. A hook could copy into the existing `.deleted` tree — which this design already excludes from indexing — making web deletes behave like SMB deletes. Caveats: nobody has built one (the maintainer's suggestion in discussion #1059 remains unimplemented), the copy doubles I/O on delete, and getting the abort-vs-succeed semantics right so the UI does not report a spurious failure needs care. Strictly optional once backups exist.
- **Retire the `filebrowser-db` volume** after the grace period.
- **Revisit `pw-urlp`** if credentials-in-logs ever matters — e.g. if access logs are shipped off-box.
- OpenCloud remains the better *product* if the requirements ever change — specifically, if Samba write access to that data becomes expendable. Its blocking constraint is architectural (it must own its storage root and stamps xattrs onto every file), not a bug that will be fixed.
