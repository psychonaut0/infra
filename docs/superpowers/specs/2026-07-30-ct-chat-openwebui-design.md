# ct-chat — Self-hosted ChatGPT Equivalent on OpenRouter — Design

**Status:** Spec — 2026-07-30
**Goal:** Stand up a new CT running Open WebUI against a single OpenRouter account, giving a ChatGPT/Claude-equivalent experience — server-side chat history, persistent memory, web search — in the browser and through a native Android app, reachable from anywhere.

## Background

The owner has a new OpenRouter account and wants the surrounding product, not just API access: conversations that persist and sync, memory that carries facts between chats, web search grounding, and a phone app that is not a bookmark.

OpenRouter itself ships only `openrouter.ai/chat` — no memory, no mobile app. So the product layer has to be self-hosted. Nothing in the fleet currently serves this role; this is a greenfield CT.

One upstream change makes the design materially simpler than it would have been two months ago. Per OpenRouter's *"every modality, one API"* post (dated **2026-07-16**), the account is no longer chat-only — it now exposes `/embeddings` (including `openai/text-embedding-3-small` and `qwen/qwen3-embedding-0.6b`), `/audio/transcriptions`, `/audio/speech`, `/images` and `/videos` under the same base URL and the same key. Every external dependency this deployment needs, except web search, collapses into one vendor and one credential.

### Verified starting facts (2026-07-30)

| Fact | Value |
|---|---|
| Open WebUI latest release | **v0.11.0**, published 2026-07-27 — release notes lead with *"Redesigned interface"* |
| Previous OWUI minor | v0.10.2 (2026-07-01) |
| Conduit (Android/iOS client) latest | **v4.0.0**, published 2026-07-28 — one day after OWUI 0.11.0 |
| Conduit repo | 1.8k stars, ~1,391 commits, GPL-3.0, both app stores, README states support for Open WebUI 0.11 |
| Next free CT slot | VMID **115**, IP **192.168.3.18** (highest current: ct-workout, 114 / .17) |
| OpenRouter web plugin cost | $0.005/request (Exa, ≤10 results); Perplexity engine same price |
| Brave Search free tier | 2,000 queries/month |
| OWUI licence | BSD-3 + branding-retention clause from v0.6.6 (2025-04-19); clause does not bind under 50 users in a rolling 30 days |

## Requirements

**In scope:**

- Server-side chat history, searchable, synced across browser and phone by construction (one server, thin clients).
- Persistent cross-conversation memory that the user can inspect and edit.
- Web search grounding, toggleable per message.
- Browser access and a **native** Android app — not a PWA shim.
- Reachable from outside the house, over HTTPS, without a VPN.
- Attachments and image input; voice input and read-aloud; image generation. All routed to OpenRouter.
- A single OpenRouter credential for every model-backed capability.
- Full conformance to `docs/new-ct-checklist.md`, including the live half of every integration point.

**Out of scope:**

- Local model inference. No Ollama, no GPU, no CPU inference. Every token is OpenRouter's.
- Multi-user beyond the owner. The design admits a second account later but ships with one.
- LAN hostname (`chat.lan`). Deliberately omitted — see *Exposure*.
- Centralised SSO / forward-auth for `.lan`. Explicitly parked by prior decision; nothing here revisits it.
- RAG over a large existing corpus. Document upload works, but no bulk ingestion of the mergerfs pool.
- Replacing or touching any existing service.

## Frontend selection

Surveyed against the four hard requirements (synced history, memory, web search, native Android app). Two serious candidates; the rest fail on the app requirement or on the sync requirement.

### Rejected

| Candidate | Disqualifying reason |
|---|---|
| **`openrouter.ai/chat`** | No memory, no mobile app. Not a product. |
| **Chatbox and similar BYOK clients** | Real native Android app, but history is stored per-device and cross-device sync is a paid cloud tier. Fails the sync requirement, which is the whole point. |
| **LobeChat** | PWA only, no native client. |
| **SillyTavern** | Character/roleplay-oriented; wrong product shape. |
| **LibreChat** | Survived review and has the **better memory design** — a dedicated memory agent that reads and writes a visible key/value store on every request, closer to what ChatGPT actually does. Rejected on two grounds. (a) **Web search is not self-containable:** a search provider *and* a scraper are both mandatory, and while SearXNG covers search for free, every scraper option (Firecrawl, Serper, Tavily) is a paid third-party API — local Firecrawl is documented as "planned". That is a second vendor and a second bill for a capability Open WebUI gets from one key. (b) **PWA only on Android**, which fails a stated requirement outright. Its five-container stack (api, mongodb, meilisearch, rag_api, pgvector) and 4 GB floor also triple the backup surface versus one data directory. |

### Selected: Open WebUI

Chosen because it is the only candidate that satisfies all four hard requirements, and because the mobile client is genuinely good rather than merely existing: **Conduit** is a native GPL-3.0 Flutter app on both stores, tracking upstream closely enough that v4.0.0 shipped one day after OWUI v0.11.0, and it supports chats, folders, notes, tools, web search, image generation, voice and attachments — not a WebView wrapper.

Secondary wins: one container instead of five; state is a single directory, so the nightly restic job needs one rsync rather than three dump types; and every model-backed slot accepts an OpenAI-compatible base URL, which is what makes the single-credential design work.

Accepted costs, stated plainly:

- **Memory is the weaker half of the trade.** Native memory in Settings → Personalization is injected into system context each turn, but discussion [#19196](https://github.com/open-webui/open-webui/discussions/19196) reports that only ~3 entries take effect and the rest are ignored. The mitigation is the community **Adaptive Memory** function, which means the memory layer is a third-party plugin whose upgrade risk we own. This was accepted with the defect known, not discovered later.
- **A tool-call timeout interacts badly with Cloudflare.** See *Web search and the 100-second ceiling*.
- **Branding clause.** Under 50 users the BSD-3 terms govern; the practical obligation is simply not to strip Open WebUI branding. No action required.
- **Fast-moving upstream.** 0.11.0 is a redesign three days old. Version pinning is mandatory, not stylistic.

## Architecture

### Container

New CT **`ct-chat`**, VMID **115**, IP **192.168.3.18**, on **proxmoxmain**, unprivileged, Debian 13 (Trixie). No device passthrough needed, so unprivileged is correct.

Standard Docker-in-LXC configuration per the checklist: `lxc.apparmor.profile: unconfined`, `lxc.mount.auto: proc:rw sys:rw`, `features: nesting=1,keyctl=1`, `onboot: 1`, static IP on vmbr0 (gw 192.168.3.1, DNS 192.168.3.5), rootfs on `local-lvm`, and a `#ct-chat - AI chat frontend (Open WebUI → OpenRouter)` description line in `/etc/pve/lxc/115.conf`.

### Stack

`/opt/stacks/ct-chat/docker-compose.yml`, mirrored at `stacks/ct-chat/`. Two services:

| Service | Image | Port | Notes |
|---|---|---|---|
| `open-webui` | `ghcr.io/open-webui/open-webui:v0.11.0` | 8080 | Version-pinned. Bind-mounted `./data`. Healthcheck on `/health`. |
| `portainer-agent` | standard block | 9001 | Fleet convention. |

No SearXNG container — Brave was chosen as the search provider.

**State is a bind-mounted `./data`, not a Docker named volume.** This is deliberate and has a backup consequence: it places SQLite, uploads and the vector store inside `/opt/stacks/ct-chat`, so ct-chat must go in `FULL_STACK_CTS` and volume-only backup would capture nothing. SQLite is correct for one user; Postgres is a scaling decision that does not apply and would add a dump type to the backup path.

`restart: unless-stopped` on both. Secrets in `.env` (gitignored) with a committed `.env.example` carrying placeholders only.

### One credential, five slots

Every OpenAI-compatible base URL in Open WebUI defaults to `${OPENAI_API_BASE_URL}` — verified individually for `RAG_OPENAI_API_BASE_URL`, `AUDIO_STT_OPENAI_API_BASE_URL`, `AUDIO_TTS_OPENAI_API_BASE_URL` and `IMAGES_OPENAI_API_BASE_URL`. Setting the root pair therefore propagates to every slot, and the per-slot URLs are left unset on purpose rather than repeated.

| Slot | Configuration |
|---|---|
| Chat | `OPENAI_API_BASE_URL=https://openrouter.ai/api/v1`, `OPENAI_API_KEY=<openrouter key>` |
| Embeddings | `RAG_EMBEDDING_ENGINE=openai`, `RAG_EMBEDDING_MODEL=openai/text-embedding-3-small` |
| Speech-to-text | `AUDIO_STT_ENGINE=openai`, `AUDIO_STT_MODEL=openai/gpt-4o-mini-transcribe` |
| Text-to-speech | `AUDIO_TTS_ENGINE=openai`, `AUDIO_TTS_MODEL=hexgrad/kokoro-82m`, `AUDIO_TTS_VOICE=<voice from that model's set>` |
| Image generation | `ENABLE_IMAGE_GENERATION=true`, `IMAGE_GENERATION_ENGINE=openai` (already the default), `IMAGE_GENERATION_MODEL=google/gemini-2.5-flash-image` |

Model IDs above were taken from OpenRouter's published collections on 2026-07-30 and are starting points, not constraints — the catalogue moves fast. Cheaper or better-suited swaps within each slot: `openai/whisper-large-v3-turbo` for STT, `deepgram/aura-2` or `microsoft/mai-voice-2-flash` for TTS, `black-forest-labs/flux.2-klein-4b` or `openai/gpt-image-2` for images.

**`AUDIO_TTS_VOICE` is model-specific and cannot be carried across a model swap** — each TTS model publishes its own voice set (`x-ai/grok-voice-tts-1.0`, for instance, offers Eve, Ara, Rex, Sal and Leo). Changing `AUDIO_TTS_MODEL` without updating the voice will fail or silently fall back.

Two of these are **not optional niceties but RAM controls**, because their empty defaults run models locally:

- `RAG_EMBEDDING_ENGINE` empty means SentenceTransformers, which loads roughly 500 MB **per worker** and is documented as one of the two most common causes of memory growth in production OWUI deployments.
- `AUDIO_STT_ENGINE` empty means **the backend runs a local Whisper instance**. This is easy to miss and would defeat the entire sizing argument below.

`RAG_EMBEDDING_BATCH_SIZE` defaults to `1`, the safe value for APIs that reject batching. Whether OpenRouter's `/embeddings` accepts batched input is unverified; raising it is a post-deploy tuning step, not a launch requirement.

The model picker gets an explicit allowlist. OpenRouter exposes 400+ models and an unfiltered picker is unusable; roughly a dozen curated entries.

### Memory

**Adaptive Memory** community function, installed deliberately in place of trusting native Personalization memory. Version-pinned at install, and OWUI upgrades must be treated as requiring a memory smoke-test rather than assumed transparent.

It performs its own extraction call per turn, so it is pointed at a deliberately cheap model. Memory quality is not a function of the memory model being expensive.

### Web search and the 100-second ceiling

`WEB_SEARCH_ENGINE=brave` with `BRAVE_SEARCH_API_KEY`, `ENABLE_WEB_SEARCH=true`.

The real design constraint is [OWUI #16747](https://github.com/open-webui/open-webui/issues/16747): `/chat/completions` does not emit its first SSE byte until the whole tool-use cycle completes. Cloudflare's **100-second idle timeout is not raisable on the free plan**, so a slow search surfaces a **524** to the client — even though the answer still arrives over the WebSocket channel, making the failure ugly rather than fatal.

The latency is dominated by Open WebUI's post-search pipeline (fetch each result URL, chunk, embed, retrieve), not by Brave. Mitigations, in escalation order:

1. `WEB_SEARCH_RESULT_COUNT` left at its default of **3**. Already conservative; no change needed.
2. `WEB_SEARCH_CONCURRENT_REQUESTS=1` — the docs recommend exactly this for the Brave free tier to avoid rate-limit errors, so it is doubly justified.
3. `BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL=true` — skips the embed-and-retrieve stage, removing the largest latency term and the per-search embedding spend.
4. **Only if 524s persist:** `BYPASS_WEB_SEARCH_WEB_LOADER=true`, which uses search-engine snippets and never fetches page content. This is the strongest lever and the only one that costs answer quality, so it is held in reserve rather than applied at launch.

## Exposure, auth and header trust

Public path only, matching the `portfolio.<PERSONAL_DOMAIN>` precedent of cloudflared proxying straight to the app:

```
public: client → CF edge → CF Access → cloudflared (.6) → open-webui (.18:8080)
direct: LAN client ─────────────────────────────────────→ open-webui (.18:8080)
```

- Ingress `chat.<PERSONAL_DOMAIN>` → `http://192.168.3.18:8080`, added in the **Cloudflare Zero Trust dashboard**. The tunnel is token-managed, so this produces **no repo diff**; `infra tunnel export` must run immediately afterwards and `stacks/ct-tunnel/ingress.yml` be committed, or the mirror goes stale silently.
- **Cloudflare Access** policy on the hostname, email OTP to the owner's address. Conduit documents support for custom headers and reverse-proxy auth flows — naming oauth2-proxy, Authelia, authentik, Pangolin and Cloudflare Tunnel, capturing session state on-device — so the native app is not locked out. CF Access also supports a single-header service-token form if the cookie flow proves awkward.
- `ENABLE_SIGNUP=false` after the first account exists. `DEFAULT_USER_ROLE` already defaults to `pending`, so even a signup leak would not self-approve, but both belong in place.
- `WEBUI_SECRET_KEY` set **explicitly** in `.env`. Left unset it is generated into the data directory; explicit is auditable, backed up, and survives a data-directory restore without invalidating every session.
- `WEBUI_URL=https://chat.<PERSONAL_DOMAIN>` so generated links are correct.
- Cloudflare WAF rate-limit rule on the signin endpoint. Open WebUI's own hardening guidance states it is built for private, trusted networks and pushes brute-force and DDoS defence to the proxy layer; this is that layer.

**`WEBUI_AUTH_TRUSTED_EMAIL_HEADER` is deliberately NOT set.** Cloudflare Access injects `Cf-Access-Authenticated-User-Email`, and trusting it would give seamless SSO — but port 8080 stays directly reachable on the LAN, so any LAN host could forge that header and authenticate as an arbitrary user. This is the same trap the ct-files design documented for `xff-src`: a trusted header is only trustworthy when the untrusted path does not exist. Open WebUI's own password auth remains the authority; CF Access is a strictly additive outer gate.

**Two inherited Cloudflare limits**, both already understood from copyparty:

- Free-plan request bodies cap at **100 MB**, so chat attachments above that fail at the edge.
- LAN traffic round-trips through the WAN. Accepted; `infra dns add chat.lan http://192.168.3.18:8080` remains a one-command fix if it becomes annoying, deliberately not shipped now.

### Cost containment

The endpoint is public and spends real money, so the blast radius needs a hard stop that does not depend on auth holding: **keep a modest prepaid OpenRouter balance and leave auto-topup off.** Prepaid credit is the only cap that survives a compromised login.

## Resources

**2 vCPU / 2048 MB RAM / 512 MB swap / 32 GB disk.**

2 GB is defensible *only* because both local-model paths are disabled — external embeddings and external STT. With either left at its empty default this is undersized and needs 4 GB. That coupling is the single most important sizing fact in this spec.

**Disk was revised from 16 GB to 32 GB during implementation (2026-07-30), measured rather than estimated.** `ghcr.io/open-webui/open-webui:v0.11.0` unpacks to **7.16 GB**, which put a 16 GB rootfs at 54% used with nothing running and no user data. The binding constraint is not steady state but upgrades: `docker compose pull` fetches the new image before releasing the old, so an upgrade needs roughly 14 GB of images simultaneously and would have failed on disk.

A leaner variant was investigated and rejected as a fix. Probing the ghcr manifest API, `v0.11.0-slim`, `-cuda` and `-ollama` all exist (`-lite` does not), but slim's compressed layer total is 1473 MB against the standard image's 1741 MB — a ~270 MB delta, so "slim" is not a no-local-ML build and does not change the arithmetic. Since any tag needs room for two images during an upgrade, capacity was the correct lever. local-lvm had 279 GB free after the resize, so the cost is negligible.

### Version pairing

Pin **OWUI v0.11.0 with Conduit v4.0.0** and treat them as a tested pair — Conduit's README claims 0.11 support and its release landed one day after OWUI's, which is the strongest available signal. Whether Conduit v4.0.0 *requires* 0.11 or merely supports it is unverified; confirm before deploying, because it determines the rollback target. If the three-day-old redesign proves unstable, roll back to v0.10.2 (2026-07-01) and a correspondingly older Conduit, not v0.10.2 with Conduit v4.0.0.

## Fleet integration

| Surface | Change |
|---|---|
| `stacks/hosts.yaml` | `ct-chat: ip: 192.168.3.18` |
| `cli/internal/discover/fleet.json` | IP + `open-webui` → `ct-chat` service mapping |
| **Release tag** | fleet.json is embedded at build time. `git tag` → CI → infra-mirror (≤5 min) → `infra update -y`. **Prove from a CT**, not a workstation: `ssh root@ct-mgmt 'infra ls \| grep chat'` |
| Base host | `apt install rsync` (provides `/usr/bin/rrsync`; its absence silently fails every full-stack pull — this bit ct-workout), then `curl -fsSL http://infra-bin.lan/install.sh \| sh` |
| `stacks/ct-tunnel/ingress.yml` | Regenerated via `infra tunnel export` after the dashboard change; committed |
| `stacks/ct-backup/scripts/pre-backup.sh` | `ct-chat` in `CT_IPS` **and in `FULL_STACK_CTS`** — state is a bind mount, so volume-only backup captures nothing |
| ct-chat host | `/usr/local/bin/backup-dispatch.sh`, `/etc/backup-dispatch.conf` with `ALLOW_RSYNC_PATHS="/opt/stacks"`, forced-command key in `/root/.ssh/authorized_keys` |
| **ct-backup host** | scp both scripts to `root@192.168.3.13:/usr/local/bin/` — `infra deploy` does **not** cover ct-backup |
| `stacks/ct-mgmt/gatus/config.yaml` | Check at **important** tier. Condition must assert alive *and* enforcing auth — an unauthenticated 200 would mask a broken ACL. Determine the real unauthenticated response during deployment |
| `stacks/ct-mgmt/dashboard-src/` | Entry in **both** `server.js` (services array) and `src/services.js` (sections) with byte-identical `ping` join key, plus icon. Rebuild with `docker compose up -d --build dashboard` — `infra deploy dashboard` does not rebuild |
| Portainer | Register environment `192.168.3.18:9001` **within 72h** of agent start |
| `CLAUDE.md` | `### ct-chat` section, proxmoxmain CTs list, SSH tree, Services prose line — **keep placeholders**, the file is sanitised for public release |
| `docs/hardware.md` | LXC boot-disk allocation row |
| `stacks/ct-chat/README.md` | Setup **and restore** runbook |

## Verification

| Check | Method | Pass criterion |
|---|---|---|
| Embeddings are remote | Upload a document; watch logs and RSS | No SentenceTransformers download; RSS stays well under 2 GB |
| STT is remote | Check startup logs and RSS | No local Whisper load |
| Chat streaming through tunnel | Long response via `chat.<PERSONAL_DOMAIN>` | Streams token-by-token, no truncation |
| **Web search without 524** | Deliberately broad query through the tunnel | Completes under 100 s, no 524 |
| Voice in / read-aloud | Dictate a prompt, play a response | Both round-trip via OpenRouter |
| Image generation | Generate an image | Returns via OpenRouter |
| Memory persists | State a fact, start a **new** chat, ask for it | Recalled; entry visible and editable in Personalization |
| Memory survives restart | `docker compose down && up -d`, re-ask | Still recalled |
| Session survives restart | Restart, reload browser | Still logged in (explicit `WEBUI_SECRET_KEY`) |
| Signup closed | Attempt to register a second account | Rejected |
| **Header forgery blocked** | From a LAN host, `curl` to `.18:8080` with a forged `Cf-Access-Authenticated-User-Email` | **No authentication granted** |
| Conduit through CF Access | Android app against the public hostname | Authenticates, lists history, streams |
| Cross-device sync | Send from phone, open in browser | Same conversation, both directions |
| Attachment cap | Upload >100 MB through the tunnel | Fails at the edge as expected — confirms the known limit, not a bug |
| Fleet registration | `ssh root@ct-mgmt 'infra ls \| grep chat'` | Present — proves the release, not just the commit |
| **Backup end-to-end** | `systemctl start backup.service` on ct-backup, then `restic ls latest \| grep chat` | `/opt/stacks/ct-chat` captured **including `.env` and `data/`**, sane sizes, no WARN in `/var/log/backup/pre-backup.log` |
| Restore rehearsal | Restore `data/` to a scratch path | SQLite opens; chats and memories intact |
| Gatus | http://status.lan | Check green, and red when the container is stopped |

## Known limitations

Accepted, recorded so they are not rediscovered as surprises:

1. **Memory depends on a community function.** Native Personalization memory has an open ~3-entry defect ([#19196](https://github.com/open-webui/open-webui/discussions/19196)); Adaptive Memory is third-party. Pin it, and smoke-test memory on every OWUI upgrade.
2. **524s are possible on slow tool calls.** Structural: OWUI buffers until the tool cycle ends, Cloudflare's free-plan 100 s ceiling is fixed. Mitigated, not eliminated. The answer still arrives over the WebSocket.
3. **100 MB attachment cap** through the tunnel.
4. **LAN traffic round-trips through the WAN.** By choice.
5. **No LAN fallback if the tunnel is down.** With no `chat.lan`, a cloudflared or Cloudflare outage means no access at all. One command to fix if it ever matters.
6. **Fast-moving upstream on a three-day-old redesign.** Pinned, with a defined rollback pair.
7. **Public login page in front of a paid API key.** Defence is CF Access + OWUI password auth + WAF rate limiting + prepaid-only credit.
8. **Single user.** Family accounts are a later toggle, untested here.
9. **Per-turn memory extraction costs tokens** on every message, independent of whether anything was worth remembering.

## Follow-up work

Out of scope here, recorded so the reasoning is not lost:

- **`chat.lan` via Caddy** if WAN round-trips or tunnel-outage lockout become annoying: `infra dns add chat.lan http://192.168.3.18:8080`. Note this would make the LAN path a second untrusted entry point, which is exactly why `WEBUI_AUTH_TRUSTED_EMAIL_HEADER` stays unset either way.
- **Raise `RAG_EMBEDDING_BATCH_SIZE`** once OpenRouter's `/embeddings` batching behaviour is confirmed.
- **Second account** for family use, with `DEFAULT_USER_ROLE=pending` already providing the approval gate.
- **Migrate to LibreChat** only if memory quality becomes the binding complaint *and* paying for a scraper API becomes acceptable. Its memory design is genuinely better; the cost is five containers, a second vendor, and losing the native Android client.
- **Reconsider the OpenRouter web plugin** ($0.005/request) if Brave's 2,000/month proves limiting or its result quality disappoints. It would need the community OpenRouter pipe, adding a second third-party plugin — which is precisely why it was not chosen at launch.
- **MCP tool access** from Open WebUI, unexplored here.
