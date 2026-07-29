# ct-chat / Open WebUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provision `ct-chat` (VMID 115, 192.168.3.18) running Open WebUI v0.11.0 against a single OpenRouter account, publicly reachable at `chat.<PERSONAL_DOMAIN>` through the existing Cloudflare Tunnel, fully integrated into fleet discovery, backups, monitoring and docs.

**Architecture:** One unprivileged Debian 13 LXC on proxmoxmain running two containers — `open-webui` on :8080 and the standard `portainer-agent` on :9001. All model-backed capabilities (chat, embeddings, speech-to-text, text-to-speech, image generation) point at `https://openrouter.ai/api/v1` with one API key; web search uses Brave. State is a bind-mounted `./data` inside `/opt/stacks/ct-chat`, so backups need full-stack rsync *plus* an online SQLite `.backup`.

**Tech Stack:** Proxmox LXC, Docker Compose, Open WebUI v0.11.0, OpenRouter API, Brave Search API, Cloudflare Tunnel + Cloudflare Access, restic/ct-backup, Gatus, Caddy (not used here — no `.lan` name by design).

**Spec:** `docs/superpowers/specs/2026-07-30-ct-chat-openwebui-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **Image pin:** `ghcr.io/open-webui/open-webui:v0.11.0`. Never `:main` or `:latest`. Upstream released a redesign on 2026-07-27 and moves fast.
- **Android client pairing:** Conduit **v4.0.0** (released 2026-07-28) is the tested pair for OWUI v0.11.0. Rollback target is OWUI v0.10.2 **with a correspondingly older Conduit**, never v0.10.2 + Conduit v4.0.0.
- **This repo is public and sanitised.** Never commit the real domain, public IP, or any API key. Use `<PERSONAL_DOMAIN>` in committed files. Real values live in `CLAUDE.local.md`. Gatus config may use `${PERSONAL_DOMAIN}` because it is substituted at runtime from a gitignored `.env`.
- **proxmoxmain only.** Do not place this CT on proxmoxnode.
- **Two env vars are RAM controls, not preferences.** `RAG_EMBEDDING_ENGINE=openai` and `AUDIO_STT_ENGINE=openai` must both be set. Empty defaults load SentenceTransformers (~500 MB per worker) and a local Whisper instance respectively, and the 2048 MB allocation assumes neither ever loads.
- **`WEBUI_AUTH_TRUSTED_EMAIL_HEADER` must remain unset.** Port 8080 stays LAN-reachable, so trusting a Cloudflare Access header would let any LAN host forge authentication.
- **Secrets:** `.env` gitignored, `.env.example` committed with placeholders only.
- **committed ≠ deployed.** Every integration point below has a repo half and a live half. The live half is called out explicitly and must be proven, not assumed.
- **Most Open WebUI env vars are `ConfigVar` (persistent).** They are read from the environment **only on first boot**, copied into the database, and from then on **the database wins and env changes are silently ignored.** Verified persistent among the vars used here: `ENABLE_SIGNUP`, `DEFAULT_USER_ROLE`, `WEBUI_URL`, `OPENAI_API_BASE_URL`, `ENABLE_WEB_SEARCH`, `BRAVE_SEARCH_API_KEY`, `WEB_SEARCH_CONCURRENT_REQUESTS`, `BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL`. Verified *not* persistent: `WEBUI_SECRET_KEY`, `WEB_SEARCH_ENGINE`, `AUDIO_STT_ENGINE`, `AUDIO_TTS_ENGINE`, `IMAGE_GENERATION_ENGINE`.

  Two consequences that shape several tasks below:

  1. **Changing a persistent setting after first boot must be done in the Admin Panel**, not by editing `.env`. Editing `.env` and running `docker compose up -d` will appear to succeed and change nothing. This is upstream's own documented recommendation.
  2. **The committed `docker-compose.yml` is a first-boot seed, not live state.** Live configuration lives in `data/webui.db`, which is why Task 8's SQLite dump is a *configuration* backup and not merely a chat backup.

  `ENABLE_PERSISTENT_CONFIG=False` would make the compose file authoritative instead, but it is deliberately **not** used: upstream warns that the Admin Panel still appears editable while silently discarding changes, which is a worse footgun than the seed semantics. The alternative is recorded in the README so the choice is not relitigated.

---

## File Structure

| Path | Responsibility |
|---|---|
| `stacks/ct-chat/docker-compose.yml` | Create — service definitions, all Open WebUI configuration via environment |
| `stacks/ct-chat/.env.example` | Create — placeholder secrets + the two-phase `ENABLE_SIGNUP` flag |
| `stacks/ct-chat/.gitignore` | Create — ignore `.env` and `data/` |
| `stacks/ct-chat/README.md` | Create — setup and **restore** runbook |
| `stacks/hosts.yaml` | Modify — add `ct-chat` IP |
| `cli/internal/discover/fleet.json` | Modify — add host IP + `open-webui` service mapping |
| `stacks/ct-backup/scripts/pre-backup.sh` | Modify — `CT_IPS`, `FULL_STACK_CTS`, `SQLITE_TARGETS` |
| `stacks/ct-mgmt/gatus/config.yaml` | Modify — LAN liveness check + public Access-enforcement check |
| `stacks/ct-mgmt/dashboard-src/server.js` | Modify — services array entry |
| `stacks/ct-mgmt/dashboard-src/src/services.js` | Modify — section entry (matching `ping`) |
| `stacks/ct-mgmt/dashboard-src/public/icons/openwebui.svg` | Create — dashboard icon |
| `stacks/ct-tunnel/ingress.yml` | Modify — regenerated by `infra tunnel export`, never hand-edited |
| `CLAUDE.md` | Modify — CT section, CTs list, SSH tree, Services line |
| `docs/hardware.md` | Modify — LXC boot-disk allocation row |

`backup-dispatch.sh` is **not** modified — its generic `sqlite-dump <name>` op already does everything needed; only the per-host `/etc/backup-dispatch.conf` differs.

---

## Task 1: Provision and prepare the CT

**Files:**
- Create: `/etc/pve/lxc/115.conf` (on proxmoxmain, via `pct`)
- No repo files.

**Interfaces:**
- Consumes: nothing.
- Produces: a reachable host `ct-chat` at `192.168.3.18` with Docker, `rsync`, `sqlite3` and the `infra` CLI installed.

- [ ] **Step 1: Confirm the target VMID and IP are actually free**

```bash
ssh root@proxmoxmain 'pct list; echo "---"; pvesh get /cluster/resources --type vm --output-format json | grep -o "\"vmid\":1[0-9][0-9]" | sort -u'
ping -c1 -W1 192.168.3.18
```

Expected: no VMID 115 in either listing, and `ping` reports 100% packet loss (host does not exist). If either is occupied, stop and re-derive the next free slot — do not proceed.

- [ ] **Step 2: Find the exact Debian 13 template filename**

```bash
ssh root@proxmoxmain 'pveam list local | grep -i debian-13'
```

Expected: one line naming a `debian-13-standard_*_amd64.tar.zst`. If absent, run `ssh root@proxmoxmain 'pveam update && pveam available | grep debian-13'` then `pveam download local <filename>`. Record the exact filename — the next step needs it verbatim.

- [ ] **Step 3: Create the container**

Substitute `<TEMPLATE>` with the filename from Step 2.

```bash
ssh root@proxmoxmain 'pct create 115 local:vztmpl/<TEMPLATE> \
  --hostname ct-chat \
  --cores 2 --memory 2048 --swap 512 \
  --rootfs local-lvm:16 \
  --net0 name=eth0,bridge=vmbr0,ip=192.168.3.18/24,gw=192.168.3.1 \
  --nameserver 192.168.3.5 \
  --unprivileged 1 \
  --features nesting=1,keyctl=1 \
  --onboot 1 \
  --ssh-public-keys /root/.ssh/authorized_keys \
  --description "ct-chat - AI chat frontend (Open WebUI -> OpenRouter)"'
```

Expected: `extracting archive ...` then no error. The `--description` value becomes the `#`-prefixed comment line the checklist asks for.

- [ ] **Step 4: Add the Docker-in-LXC overrides**

These two lines cannot be set via `pct create` and must be appended to the config file.

```bash
ssh root@proxmoxmain 'cat >> /etc/pve/lxc/115.conf <<EOF
lxc.apparmor.profile: unconfined
lxc.mount.auto: proc:rw sys:rw
EOF
cat /etc/pve/lxc/115.conf'
```

Expected: the printed config contains `unprivileged: 1`, `features: keyctl=1,nesting=1`, `onboot: 1`, both `lxc.*` lines, and the `#ct-chat - AI chat frontend...` description.

- [ ] **Step 5: Start it and confirm SSH works**

```bash
ssh root@proxmoxmain 'pct start 115'
sleep 15
ssh -o StrictHostKeyChecking=accept-new root@192.168.3.18 'hostname; cat /etc/debian_version; nproc; free -m | head -2'
```

Expected: `ct-chat`, a `13.x` version string, `2` CPUs, and roughly 2048 MB total memory. If SSH is refused, the cluster `authorized_keys` did not propagate — fix that before continuing.

- [ ] **Step 6: Install base packages**

`rsync` is not optional: it provides `/usr/bin/rrsync`, which `backup-dispatch.sh` execs. Its absence silently fails every backup pull with rsync error 12 while other dumps still succeed — this exact gap left ct-workout unbacked-up for days. `sqlite3` is needed for the online `.backup` in Task 8.

```bash
ssh root@192.168.3.18 'apt-get update -qq && apt-get install -y -qq rsync sqlite3 curl ca-certificates && command -v rrsync && command -v sqlite3'
```

Expected: `/usr/bin/rrsync` and `/usr/bin/sqlite3` both printed.

- [ ] **Step 7: Install Docker and the infra CLI**

```bash
ssh root@192.168.3.18 'curl -fsSL https://get.docker.com | sh && docker run --rm hello-world | grep -q "working correctly" && echo DOCKER_OK'
ssh root@192.168.3.18 'curl -fsSL http://infra-bin.lan/install.sh | sh && infra --version'
```

Expected: `DOCKER_OK`, then a version string from `infra`.

- [ ] **Step 8: Commit nothing, record state**

No repo changes in this task. Confirm the checklist's provision + base-host sections are fully satisfied before moving on:

```bash
ssh root@192.168.3.18 'systemctl is-enabled docker; systemctl is-active docker'
```

Expected: `enabled`, `active`.

---

## Task 2: Deploy the stack and create the admin account

**Files:**
- Create: `stacks/ct-chat/docker-compose.yml`
- Create: `stacks/ct-chat/.env.example`
- Create: `stacks/ct-chat/.gitignore`

**Interfaces:**
- Consumes: the host from Task 1.
- Produces: Open WebUI reachable at `http://192.168.3.18:8080`, one admin account, `ENABLE_SIGNUP=false`, and every OpenRouter slot configured. Later tasks depend on `/health` returning 200 unauthenticated and on the container being named `open-webui`.

- [ ] **Step 1: Write the compose file**

Create `stacks/ct-chat/docker-compose.yml`:

```yaml
name: ct-chat

services:
  open-webui:
    image: ghcr.io/open-webui/open-webui:v0.11.0
    container_name: open-webui
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      # Bind mount, NOT a named volume: keeps state inside /opt/stacks so the
      # ct-backup full-stack rsync captures it. See stacks/ct-chat/README.md.
      - ./data:/app/backend/data
    environment:
      TZ: "Europe/Rome"

      # --- Identity / auth ---
      WEBUI_URL: ${WEBUI_URL}
      # Signs JWTs and encrypts data at rest. Set explicitly so sessions survive
      # a data-directory restore instead of being regenerated into ./data.
      WEBUI_SECRET_KEY: ${WEBUI_SECRET_KEY}
      # Starts true for first-account creation, flipped to false in Step 7.
      ENABLE_SIGNUP: "${ENABLE_SIGNUP}"
      # Safety net: even a signup leak cannot self-approve.
      DEFAULT_USER_ROLE: pending

      # --- Chat (OpenRouter). Every other OpenAI-compatible base URL in Open
      # WebUI defaults to ${OPENAI_API_BASE_URL}, so the per-slot URLs below are
      # deliberately NOT set — they inherit this one. ---
      OPENAI_API_BASE_URL: https://openrouter.ai/api/v1
      OPENAI_API_KEY: ${OPENROUTER_API_KEY}

      # --- Embeddings. MANDATORY: empty means SentenceTransformers, ~500MB per
      # worker, which does not fit the 2048MB allocation. ---
      RAG_EMBEDDING_ENGINE: openai
      RAG_EMBEDDING_MODEL: openai/text-embedding-3-small

      # --- Speech-to-text. MANDATORY: empty runs a local Whisper instance on
      # the backend. ---
      AUDIO_STT_ENGINE: openai
      AUDIO_STT_MODEL: openai/gpt-4o-mini-transcribe

      # --- Text-to-speech. AUDIO_TTS_VOICE is specific to AUDIO_TTS_MODEL and
      # cannot be carried across a model swap. ---
      AUDIO_TTS_ENGINE: openai
      AUDIO_TTS_MODEL: hexgrad/kokoro-82m
      AUDIO_TTS_VOICE: ${AUDIO_TTS_VOICE}

      # --- Image generation ---
      ENABLE_IMAGE_GENERATION: "true"
      IMAGE_GENERATION_ENGINE: openai
      IMAGE_GENERATION_MODEL: google/gemini-2.5-flash-image

      # --- Web search (Brave). WEB_SEARCH_RESULT_COUNT is left at its default
      # of 3. CONCURRENT_REQUESTS=1 is the documented setting for Brave's free
      # tier. BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL removes the largest
      # latency term ahead of Cloudflare's fixed 100s ceiling (OWUI #16747). ---
      ENABLE_WEB_SEARCH: "true"
      WEB_SEARCH_ENGINE: brave
      BRAVE_SEARCH_API_KEY: ${BRAVE_SEARCH_API_KEY}
      WEB_SEARCH_CONCURRENT_REQUESTS: "1"
      BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL: "true"
    healthcheck:
      # 127.0.0.1, not localhost — avoids the IPv6-refusal trap that bit
      # ct-portfolio's busybox wget healthcheck.
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8080/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 60s

  portainer-agent:
    image: portainer/agent:latest
    container_name: portainer-agent
    restart: unless-stopped
    ports:
      - "9001:9001"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /var/lib/docker/volumes:/var/lib/docker/volumes
```

- [ ] **Step 2: Write `.env.example` and `.gitignore`**

Create `stacks/ct-chat/.env.example`:

```bash
# OpenRouter API key — the single credential for chat, embeddings, STT, TTS and
# image generation. https://openrouter.ai/keys
OPENROUTER_API_KEY=sk-or-v1-REPLACE_ME

# Brave Search API key. Free tier: 2000 queries/month.
# https://brave.com/search/api/
BRAVE_SEARCH_API_KEY=REPLACE_ME

# Signs JWTs and encrypts data at rest. Generate with: openssl rand -hex 32
# Losing this invalidates every session; changing it after a restore does too.
WEBUI_SECRET_KEY=REPLACE_ME

# Public URL, used for generated links.
WEBUI_URL=https://chat.<PERSONAL_DOMAIN>

# true only long enough to create the first (admin) account, then false.
# NOTE: persistent ConfigVar — this seeds the database on FIRST boot only.
# After that, change it in Admin Panel -> Settings -> General, not here.
ENABLE_SIGNUP=true

# Voice name from AUDIO_TTS_MODEL's own voice set. Model-specific — a voice
# valid for one TTS model is usually invalid for another.
AUDIO_TTS_VOICE=REPLACE_ME
```

Create `stacks/ct-chat/.gitignore`:

```
.env
data/
```

- [ ] **Step 3: Commit the repo half**

```bash
git add stacks/ct-chat/
git commit -m "feat(ct-chat): Open WebUI stack targeting OpenRouter"
```

- [ ] **Step 4: Copy the stack to the CT and fill in real secrets**

```bash
ssh root@192.168.3.18 'install -d -m 755 /opt/stacks/ct-chat'
scp stacks/ct-chat/docker-compose.yml stacks/ct-chat/.env.example root@192.168.3.18:/opt/stacks/ct-chat/
ssh root@192.168.3.18 'cd /opt/stacks/ct-chat && cp .env.example .env && chmod 600 .env && sed -i "s|^WEBUI_SECRET_KEY=.*|WEBUI_SECRET_KEY=$(openssl rand -hex 32)|" .env'
```

Then edit `/opt/stacks/ct-chat/.env` on the CT and replace `OPENROUTER_API_KEY`, `BRAVE_SEARCH_API_KEY`, `AUDIO_TTS_VOICE`, and the `<PERSONAL_DOMAIN>` in `WEBUI_URL` with real values from `CLAUDE.local.md`.

Verify no placeholders survive:

```bash
ssh root@192.168.3.18 'grep -c REPLACE_ME /opt/stacks/ct-chat/.env'
```

Expected: `0`.

- [ ] **Step 5: Verify the healthcheck binary exists in the image before relying on it**

```bash
ssh root@192.168.3.18 'docker pull ghcr.io/open-webui/open-webui:v0.11.0 >/dev/null && docker run --rm --entrypoint sh ghcr.io/open-webui/open-webui:v0.11.0 -c "command -v curl || echo NO_CURL"'
```

Expected: a path such as `/usr/bin/curl`. **If it prints `NO_CURL`**, replace the healthcheck test in `docker-compose.yml` with this Python equivalent, and re-commit:

```yaml
      test: ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)\""]
```

- [ ] **Step 6: Bring it up and confirm health**

```bash
ssh root@192.168.3.18 'cd /opt/stacks/ct-chat && docker compose up -d'
sleep 90
ssh root@192.168.3.18 'docker compose -f /opt/stacks/ct-chat/docker-compose.yml ps'
curl -fsS http://192.168.3.18:8080/health
```

Expected: both services `Up`, `open-webui` reporting `(healthy)`, and `/health` returning a success body unauthenticated.

- [ ] **Step 7: Create the admin account, then close signup — via the Admin Panel**

Open `http://192.168.3.18:8080` in a browser and register the first account. Open WebUI grants admin to the first user.

Now close signup. **`ENABLE_SIGNUP` is a persistent `ConfigVar`**, so it was copied into the database on first boot and editing `.env` will no longer affect it. Do it in the UI:

Admin Panel → Settings → General → disable **Enable New Sign Ups** → Save.

Then also update `.env` so a future rebuild from an empty `data/` seeds the closed state rather than reopening signup:

```bash
ssh root@192.168.3.18 'cd /opt/stacks/ct-chat && sed -i "s/^ENABLE_SIGNUP=true/ENABLE_SIGNUP=false/" .env && grep ENABLE_SIGNUP .env'
```

Expected: `ENABLE_SIGNUP=false`. No `docker compose up -d` is needed or useful here — the running instance already took its value from the database.

- [ ] **Step 7b: Verify signup is actually closed**

Do not trust the toggle. Check from a logged-out context:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST http://192.168.3.18:8080/api/v1/auths/signup \
  -H 'Content-Type: application/json' \
  -d '{"name":"probe","email":"probe@example.invalid","password":"probe-probe-probe"}'
```

Expected: a 4xx rejection. **If this returns 200, an account was just created** — delete it in Admin Panel → Users and re-check the toggle before continuing.

- [ ] **Step 8: Verify chat works end-to-end through OpenRouter**

In the UI, add a handful of models to the allowlist (the unfiltered OpenRouter list is 400+ entries and unusable), then send one message and confirm a streamed reply.

```bash
ssh root@192.168.3.18 'docker logs open-webui 2>&1 | tail -30'
```

Expected: no authentication errors against `openrouter.ai`, no tracebacks.

- [ ] **Step 9: Prove no local models loaded**

This is the check that validates the 2048 MB allocation.

```bash
ssh root@192.168.3.18 'docker logs open-webui 2>&1 | grep -icE "sentence-transformers|sentence_transformers|whisper|downloading.*model" || echo NONE'
ssh root@192.168.3.18 'docker stats --no-stream --format "{{.Name}} {{.MemUsage}}"'
ssh root@192.168.3.18 'free -m | head -2'
```

Expected: `NONE` (or `0`), `open-webui` memory well under 1 GB, and meaningful free memory on the host. **If SentenceTransformers or Whisper appear in the logs, one of the two mandatory engine variables is not taking effect — fix it before proceeding.**

- [ ] **Step 10: Commit any healthcheck correction**

```bash
git add stacks/ct-chat/docker-compose.yml
git commit -m "fix(ct-chat): healthcheck probe matching the image's available tooling"
```

Skip this step if Step 5 printed a curl path and no edit was needed.

---

## Task 3: Verify the remaining OpenRouter slots

**Files:**
- No file changes. This task is pure verification of Task 2's configuration.

**Interfaces:**
- Consumes: the running stack from Task 2.
- Produces: confirmation that embeddings, STT, TTS and image generation all route to OpenRouter. Any failure here is fixed by correcting `docker-compose.yml` and re-running `docker compose up -d`.

- [ ] **Step 1: Verify embeddings route to OpenRouter**

Upload a small text document to a Knowledge collection in the UI, then:

```bash
ssh root@192.168.3.18 'docker logs open-webui 2>&1 | tail -40'
```

Expected: evidence of an embeddings request; no local model download. If it fails with a batching error, set the batch size to 1 in **Admin Panel → Settings → Documents** — `RAG_EMBEDDING_BATCH_SIZE` already defaults to 1, and because RAG settings are persistent the running instance takes its value from the database, so editing compose would have no effect on this instance.

- [ ] **Step 2: Ask a question answerable only from that document**

Expected: the answer cites the uploaded document. This proves the full embed → store → retrieve path, not just the API call.

- [ ] **Step 3: Verify speech-to-text**

Use the microphone button to dictate a short prompt.

Expected: transcription appears. Then confirm it was remote:

```bash
ssh root@192.168.3.18 'docker logs open-webui 2>&1 | grep -icE "whisper|loading model" || echo NONE'
```

Expected: `NONE` or `0`.

- [ ] **Step 4: Verify text-to-speech**

Use read-aloud on an assistant message.

Expected: audio plays. If it fails, the most likely cause is `AUDIO_TTS_VOICE` not being a valid voice for `hexgrad/kokoro-82m` — check the model's page on OpenRouter for its voice set and correct `.env`, then `docker compose up -d`.

- [ ] **Step 5: Verify image generation**

Trigger an image generation from the UI.

Expected: an image returns via `google/gemini-2.5-flash-image`.

- [ ] **Step 6: Record findings**

If any step required a compose or `.env` change, commit the compose half:

```bash
git add stacks/ct-chat/docker-compose.yml stacks/ct-chat/.env.example
git commit -m "fix(ct-chat): correct OpenRouter slot configuration"
```

---

## Task 4: Install and verify Adaptive Memory

**Files:**
- No repo files. Open WebUI functions are installed through the UI and stored in `./data`, which the backup covers.

**Interfaces:**
- Consumes: the running stack.
- Produces: working cross-conversation memory. No later task depends on this.

- [ ] **Step 1: Record the pre-install baseline**

Native Personalization memory has an open defect where only ~3 entries take effect ([#19196](https://github.com/open-webui/open-webui/discussions/19196)). This is why a community function is being installed deliberately rather than as an afterthought.

```bash
ssh root@192.168.3.18 'docker exec open-webui sh -c "ls -la /app/backend/data"'
```

Expected: `webui.db` present. **Record the exact filename** — Task 8 needs it for the SQLite dump path.

- [ ] **Step 2: Install the Adaptive Memory function**

In the UI: Workspace → Functions → import Adaptive Memory from the Open WebUI community site (`https://openwebui.com/f/alexgrama7/adaptive_memory_v2` or its current successor). **Record the exact version imported** — it must be re-checked after every OWUI upgrade.

- [ ] **Step 3: Point it at a cheap model**

Configure the function's extraction model to an inexpensive OpenRouter model. It runs per turn regardless of whether anything is worth remembering, so extraction cost is a recurring per-message charge and memory quality does not scale with model price.

- [ ] **Step 4: Verify memory is written**

State a durable fact in a chat ("I run a Proxmox cluster with two nodes"), then check Settings → Personalization → Memory.

Expected: a corresponding entry, visible and editable.

- [ ] **Step 5: Verify memory is recalled in a NEW conversation**

Start a fresh chat and ask something requiring the fact.

Expected: recalled correctly. Testing recall in the *same* conversation proves nothing — context alone would carry it.

- [ ] **Step 6: Verify memory survives a restart**

```bash
ssh root@192.168.3.18 'cd /opt/stacks/ct-chat && docker compose down && docker compose up -d'
sleep 60
```

Then start another new chat and re-ask.

Expected: still recalled, and still logged in — the latter confirms the explicit `WEBUI_SECRET_KEY` is doing its job.

---

## Task 5: Verify web search under the Cloudflare timeout constraint

**Files:**
- Modify (only if needed): `stacks/ct-chat/docker-compose.yml`

**Interfaces:**
- Consumes: the running stack with Brave configured.
- Produces: web search that completes inside 100 seconds. Task 6 depends on this, because the timeout only manifests through Cloudflare.

- [ ] **Step 1: Confirm search works at all, on the LAN**

Enable web search in the UI and ask a question requiring current information.

Expected: an answer with citations. If Brave returns 401, the key is wrong; if 429, the free-tier rate limit was hit despite `WEB_SEARCH_CONCURRENT_REQUESTS=1`.

- [ ] **Step 2: Measure worst-case latency**

Run a deliberately broad query and time the full round trip.

```bash
ssh root@192.168.3.18 'docker logs -f open-webui 2>&1 | ts 2>/dev/null || docker logs -f open-webui'
```

Expected: the search-to-first-token gap stays well under 100 s. Anything approaching 60 s is a warning — Cloudflare's ceiling is fixed and not raisable on the free plan.

- [ ] **Step 3: Escalate only if needed**

The mitigation ladder, in order. Steps 1–3 are already applied in Task 2; step 4 is held in reserve because it is the only one that costs answer quality.

1. `WEB_SEARCH_RESULT_COUNT` at its default of 3 — already the case, no action.
2. `WEB_SEARCH_CONCURRENT_REQUESTS: "1"` — already set.
3. `BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL: "true"` — already set.
4. **Reserve:** enable the web-loader bypass, so only search-engine snippets are used and page content is never fetched — the strongest latency lever and a real quality regression.

If step 4 becomes necessary, apply it in **Admin Panel → Settings → Web Search** on the running instance (the web-search bypass settings are persistent `ConfigVar`s, so a compose edit alone will not take effect), *and* add it to the compose file so a rebuild from an empty `data/` seeds the same state:

```yaml
      BYPASS_WEB_SEARCH_WEB_LOADER: "true"
```

```bash
git add stacks/ct-chat/docker-compose.yml
git commit -m "fix(ct-chat): bypass web loader to stay under Cloudflare's 100s ceiling"
```

- [ ] **Step 4: Note the residual risk**

Even after tuning, a slow tool call can surface a 524 because `/chat/completions` buffers until the tool cycle completes ([OWUI #16747](https://github.com/open-webui/open-webui/issues/16747)). The answer still arrives over the WebSocket, so the failure mode is an ugly error rather than lost work. This is structural and accepted — do not spend time trying to eliminate it.

---

## Task 6: Public exposure via Cloudflare Tunnel and Access

**Files:**
- Modify: `stacks/ct-tunnel/ingress.yml` (regenerated by `infra tunnel export` — never hand-edited)

**Interfaces:**
- Consumes: a healthy stack on `192.168.3.18:8080`.
- Produces: `https://chat.<PERSONAL_DOMAIN>` reachable, gated by Cloudflare Access, with Open WebUI's own auth still authoritative.

- [ ] **Step 1: Capture the pre-change ingress state**

```bash
infra tunnel diff
```

Expected: exit 0, no drift. If it already reports drift, resolve that first — otherwise this task's export will silently bake in someone else's unmirrored change.

- [ ] **Step 2: Add the ingress rule in the Cloudflare dashboard**

In Cloudflare Zero Trust → Networks → Tunnels → the existing tunnel → Public Hostnames, add:

- Hostname: `chat.<PERSONAL_DOMAIN>`
- Service: `http://192.168.3.18:8080`

This is a **manual dashboard step that produces no repo diff.** The tunnel is token-managed (`TUNNEL_TOKEN`), so ingress lives in Cloudflare, not on disk.

- [ ] **Step 3: Mirror it back into the repo immediately**

```bash
infra tunnel export
git diff stacks/ct-tunnel/ingress.yml
```

Expected: the diff adds a `chat.<PERSONAL_DOMAIN>` entry, and the real domain has been substituted for `<PERSONAL_DOMAIN>`. Export refuses to write if the real domain survives; if it refuses, do not work around it.

```bash
git add stacks/ct-tunnel/ingress.yml
git commit -m "feat(ct-tunnel): mirror chat ingress for ct-chat"
```

- [ ] **Step 4: Verify it resolves and serves before adding Access**

```bash
curl -sSI https://chat.<real-domain>/health
```

Expected: HTTP 200. Confirm streaming works by sending a long-response prompt through the public hostname in a browser.

- [ ] **Step 5: Add the Cloudflare Access policy**

Cloudflare Zero Trust → Access → Applications → Add self-hosted application:

- Domain: `chat.<PERSONAL_DOMAIN>`
- Policy: Allow, match on the owner's email address, One-time PIN

Open WebUI's own hardening guidance states it is built for private, trusted networks and pushes brute-force defence to the proxy layer. This is that layer.

- [ ] **Step 6: Verify Access is enforcing**

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://chat.<real-domain>/health
```

Expected: a redirect or 403, **not** 200 — an unauthenticated request must no longer reach the app. **Record the exact status code**; Task 9's Gatus check asserts on it.

- [ ] **Step 7: Add a WAF rate-limit rule**

Cloudflare → Security → WAF → Rate limiting rules: limit requests to the sign-in path on `chat.<PERSONAL_DOMAIN>`. Modest values are fine — this is anti-brute-force, not anti-DDoS.

- [ ] **Step 8: Verify Conduit works through Access**

Install Conduit **v4.0.0** on Android, point it at `https://chat.<real-domain>`, and complete the Access login.

Expected: authenticates, lists existing chat history, streams a new response. Conduit documents support for reverse-proxy auth flows including Cloudflare Tunnel, capturing session state on-device. If the cookie flow proves awkward, Cloudflare Access also supports a single-header service-token form.

- [ ] **Step 9: Verify cross-device sync**

Send a message from the phone, then open the same conversation in the desktop browser, and vice versa.

Expected: one shared history in both directions. This is the requirement that eliminated every per-device BYOK client.

- [ ] **Step 10: Verify header forgery is blocked**

This is the security check that justifies leaving `WEBUI_AUTH_TRUSTED_EMAIL_HEADER` unset.

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H 'Cf-Access-Authenticated-User-Email: attacker@example.com' \
  http://192.168.3.18:8080/api/v1/auths/
```

Expected: **401/403 — never a successful authentication.** Port 8080 is directly LAN-reachable, so if this header were trusted, any LAN host could impersonate any user.

- [ ] **Step 11: Confirm the known attachment cap**

Attempt to upload a file larger than 100 MB through the public hostname.

Expected: rejected at the Cloudflare edge. This confirms the documented free-plan limit rather than a bug — do not attempt to work around it.

- [ ] **Step 12: Set OpenRouter credit posture**

In the OpenRouter dashboard, keep a modest prepaid balance and **leave auto-topup off.** Prepaid credit is the only spend cap that survives a compromised login.

---

## Task 7: Fleet registration

**Files:**
- Modify: `stacks/hosts.yaml`
- Modify: `cli/internal/discover/fleet.json`

**Interfaces:**
- Consumes: a reachable CT.
- Produces: `infra ls`, `infra status`, `infra logs`, `infra deploy` and `infra restart` all working for ct-chat **from any host**, not just a workstation with a repo checkout.

- [ ] **Step 1: Add the host to `stacks/hosts.yaml`**

Insert after the `ct-workout` entry, preserving the file's existing ordering and blank-line style:

```yaml
ct-chat:
  ip: 192.168.3.18
```

- [ ] **Step 2: Add the host and service to `cli/internal/discover/fleet.json`**

In the `hosts` object (keys are alphabetically ordered — `ct-chat` sorts before `ct-dns`):

```json
    "ct-chat": {
      "ip": "192.168.3.18"
    },
```

In the `services` object, add an entry mapping the compose service name to the CT:

```json
    "open-webui": [
      "ct-chat"
    ],
```

Verify the JSON is still valid:

```bash
python3 -m json.tool cli/internal/discover/fleet.json > /dev/null && echo JSON_OK
```

Expected: `JSON_OK`.

- [ ] **Step 3: Verify locally from the checkout**

```bash
cd cli && make install && cd ..
infra ls | grep -i 'chat\|open-webui'
```

Expected: ct-chat and its service appear. **This proves nothing about the fleet** — workstations read `fleet.json` live from the checkout, which masks the embedding gap the next step closes.

- [ ] **Step 4: Commit and cut a release**

`fleet.json` is embedded into the binary at build time. Without a new tag, every CT and Proxmox node keeps the old fleet.

```bash
git add stacks/hosts.yaml cli/internal/discover/fleet.json
git commit -m "feat(cli): register ct-chat in the fleet"
git tag vX.Y.Z   # next patch version
git push origin master
git push origin vX.Y.Z
```

- [ ] **Step 5: Wait for the mirror, then update a CT**

CI builds the Release, and `infra-mirror.timer` on ct-mgmt syncs within 5 minutes.

```bash
sleep 300
ssh root@192.168.3.12 'infra update -y && infra --version'
```

Expected: the new version.

- [ ] **Step 6: Prove it from a CT, not a workstation**

```bash
ssh root@192.168.3.12 'infra ls | grep -i chat'
ssh root@192.168.3.12 'infra status --ct ct-chat'
```

Expected: ct-chat listed, and container state reported. **If this fails while Step 3 passed, the release did not propagate — do not mark this task done.**

---

## Task 8: Backups (repo half AND live half)

**Files:**
- Modify: `stacks/ct-backup/scripts/pre-backup.sh`
- Create: `/etc/backup-dispatch.conf` on ct-chat
- Install: `/usr/local/bin/backup-dispatch.sh` on ct-chat
- Deploy: both scripts to ct-backup

**Interfaces:**
- Consumes: `rsync` and `sqlite3` from Task 1; the `webui.db` filename recorded in Task 4 Step 1.
- Produces: nightly capture of `/opt/stacks/ct-chat` (including `.env`) plus a crash-consistent SQLite snapshot.

- [ ] **Step 1: Add ct-chat to `CT_IPS` in `pre-backup.sh`**

In the `declare -A CT_IPS=(` block, add:

```bash
  [ct-chat]=192.168.3.18
```

- [ ] **Step 2: Add ct-chat to `FULL_STACK_CTS`**

State lives in a bind-mounted `./data`, not a Docker named volume, so volume export would capture **nothing**. Change the line to:

```bash
FULL_STACK_CTS=(ct-ha ct-tools ct-games ct-workout ct-files ct-chat)
```

Also extend the explanatory comment above it so the reason survives:

```bash
# ct-chat is here because Open WebUI's state (SQLite DB, uploads, vector store,
# installed functions) lives in a ./data bind mount under /opt/stacks/ct-chat
# rather than a Docker named volume, and its .env holds the OpenRouter and
# Brave API keys plus WEBUI_SECRET_KEY.
```

- [ ] **Step 3: Add the SQLite consistency dump target**

Rsyncing a live SQLite file can capture a torn database. The dispatcher already has a generic `sqlite-dump` op that uses the online `.backup` API, safe against live writers.

This dump matters more than it first appears. Because most Open WebUI settings are persistent `ConfigVar`s, **`webui.db` holds the live service configuration**, not just chats and memories — the model allowlist, web-search settings, signup state and installed functions all live there. The committed compose file is only a first-boot seed. Losing this database means losing the configuration even though the repo looks complete.

Add to `SQLITE_TARGETS`:

```bash
SQLITE_TARGETS=(
  "ct-ha:ha"
  "ct-nvr:frigate"
  "ct-chat:openwebui"
)
```

- [ ] **Step 4: Install the dispatcher and config on ct-chat**

Substitute the DB filename recorded in Task 4 Step 1 if it was not `webui.db`.

```bash
scp stacks/ct-backup/scripts/backup-dispatch.sh root@192.168.3.18:/usr/local/bin/
ssh root@192.168.3.18 'chmod 755 /usr/local/bin/backup-dispatch.sh'
ssh root@192.168.3.18 'cat > /etc/backup-dispatch.conf <<EOF
ALLOW_RSYNC_PATHS="/opt/stacks"
ALLOW_SQLITE_DUMP=1
SQLITE_DB_OPENWEBUI=/opt/stacks/ct-chat/data/webui.db
EOF
chmod 600 /etc/backup-dispatch.conf; cat /etc/backup-dispatch.conf'
```

Expected: the three lines printed. Note the variable name must be `SQLITE_DB_OPENWEBUI` — the dispatcher upper-cases the dump name `openwebui` to build it.

- [ ] **Step 5: Add ct-backup's forced-command key to ct-chat**

Retrieve ct-backup's public key and install it with the forced command, matching the pattern already on other CTs:

```bash
ssh root@192.168.3.13 'cat /root/.ssh/id_ed25519.pub'
ssh root@192.168.3.11 'grep command= /root/.ssh/authorized_keys'
```

Use the second output as the exact template for the restriction options, then install the equivalent line on ct-chat:

```bash
ssh root@192.168.3.18 'install -d -m 700 /root/.ssh && cat >> /root/.ssh/authorized_keys <<EOF
command="/usr/local/bin/backup-dispatch.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty <ct-backup-pubkey>
EOF
chmod 600 /root/.ssh/authorized_keys'
```

- [ ] **Step 6: Verify the dispatcher is reachable and constrained**

```bash
ssh root@192.168.3.13 'ssh -i /root/.ssh/id_ed25519 -o BatchMode=yes root@192.168.3.18 "sqlite-dump openwebui" | wc -c'
ssh root@192.168.3.13 'ssh -i /root/.ssh/id_ed25519 -o BatchMode=yes root@192.168.3.18 "cat /etc/shadow" 2>&1 | head -1'
```

Expected: a non-trivial byte count from the first (a gzipped DB snapshot), and `Command not permitted` from the second.

- [ ] **Step 7: Deploy the updated `pre-backup.sh` to ct-backup**

`infra deploy` does **not** cover ct-backup. This step is where ct-workout was silently skipped.

```bash
scp stacks/ct-backup/scripts/pre-backup.sh root@192.168.3.13:/usr/local/bin/
ssh root@192.168.3.13 'chmod 755 /usr/local/bin/pre-backup.sh && grep -n "ct-chat" /usr/local/bin/pre-backup.sh'
```

Expected: three matches — `CT_IPS`, `FULL_STACK_CTS`, `SQLITE_TARGETS`.

- [ ] **Step 8: Commit the repo half**

```bash
git add stacks/ct-backup/scripts/pre-backup.sh
git commit -m "feat(ct-backup): back up ct-chat stack tree and Open WebUI SQLite"
```

- [ ] **Step 9: Prove it with a real run**

```bash
ssh root@192.168.3.13 'systemctl start backup.service'
ssh root@192.168.3.13 'grep -E "ct-chat|WARN" /var/log/backup/pre-backup.log | tail -20'
```

Expected: ct-chat lines present with **no WARN** referring to it.

- [ ] **Step 10: Confirm the data actually landed in restic**

```bash
ssh root@192.168.3.13 'restic ls latest | grep -i "ct-chat" | head -20'
ssh root@192.168.3.13 'restic ls -l latest | grep -iE "ct-chat.*(\.env|webui\.db)"'
```

Expected: `/opt/stacks/ct-chat` tree present **including `.env`**, the SQLite dump present, and sizes that are not zero.

- [ ] **Step 11: Rehearse a restore**

A backup that has never been restored is a hypothesis.

```bash
ssh root@192.168.3.13 'restic dump latest /var/backup-staging/sqlite/ct-chat-openwebui.db.gz > /tmp/owui.db.gz && gunzip -f /tmp/owui.db.gz && sqlite3 /tmp/owui.db "pragma integrity_check; select count(*) from chat;"'
```

Expected: `ok` and a plausible chat count. Adjust the restic path if the staging layout differs — confirm it with `restic ls latest | grep sqlite`. Then clean up `/tmp/owui.db`.

---

## Task 9: Monitoring and management

**Files:**
- Modify: `stacks/ct-mgmt/gatus/config.yaml`
- Modify: `stacks/ct-mgmt/dashboard-src/server.js`
- Modify: `stacks/ct-mgmt/dashboard-src/src/services.js`
- Create: `stacks/ct-mgmt/dashboard-src/public/icons/openwebui.svg`

**Interfaces:**
- Consumes: the status code recorded in Task 6 Step 6.
- Produces: Gatus alerting, a dashboard tile, and a registered Portainer environment.

- [ ] **Step 1: Add the Gatus checks**

Insert at the **end of the `important` group block** — immediately before the first endpoint whose `group:` is `background` — so the file's group ordering stays intact. Two checks, because they fail for different reasons: the LAN one catches a dead container, the public one catches a broken tunnel or a dropped Access policy.

```yaml
  - name: Open WebUI (LAN)
    group: important
    url: "http://192.168.3.18:8080/health"
    interval: 60s
    conditions:
      - "[CONNECTED] == true"
      - "[STATUS] == 200"
    alerts:
      - type: telegram
        failure-threshold: 3
        success-threshold: 2
        send-on-resolved: true

  - name: Open WebUI (public, Access enforced)
    group: important
    url: "https://chat.${PERSONAL_DOMAIN}/health"
    interval: 5m
    conditions:
      - "[CONNECTED] == true"
      - "[STATUS] == 302"
      - "[CERTIFICATE_EXPIRATION] > 168h"
    alerts:
      - type: telegram
        failure-threshold: 3
        success-threshold: 2
        send-on-resolved: true
```

**Replace `302` with the exact code recorded in Task 6 Step 6.** Asserting `< 400` here would be wrong: a 200 from this URL means Cloudflare Access has stopped enforcing and the app is bare on the internet, which must alert rather than pass.

- [ ] **Step 2: Deploy Gatus and confirm both checks are green**

```bash
infra deploy gatus
sleep 90
```

Open `http://status.lan` and confirm both new checks pass.

- [ ] **Step 3: Prove the LAN check actually fails when it should**

A monitoring check that has never gone red is unverified.

```bash
ssh root@192.168.3.18 'cd /opt/stacks/ct-chat && docker compose stop open-webui'
sleep 120
```

Expected: "Open WebUI (LAN)" goes red on `http://status.lan`. Then restore:

```bash
ssh root@192.168.3.18 'cd /opt/stacks/ct-chat && docker compose start open-webui'
```

- [ ] **Step 4: Add the dashboard icon**

Place an Open WebUI logo SVG at `stacks/ct-mgmt/dashboard-src/public/icons/openwebui.svg`, matching the dimensions and style of the existing icons in that directory.

- [ ] **Step 5: Add the entry to `server.js`**

In the `services` array (around line 14), add:

```javascript
  { name: "Open WebUI", ping: "http://192.168.3.18:8080/health" },
```

- [ ] **Step 6: Add the entry to `src/services.js`**

In the `Quick Access` section's `services` array, add. The `ping` string **must be byte-identical** to the one in `server.js` — it is the join key between the two files.

`href` uses the IP:port rather than the public hostname, following the existing Sonarr/Radarr/Deluge pattern for services without a `.lan` name. This also keeps the real domain out of this public repo.

```javascript
      { name: 'Open WebUI', desc: 'AI chat', href: 'http://192.168.3.18:8080', ping: 'http://192.168.3.18:8080/health', icon: `${I}/openwebui.svg` },
```

- [ ] **Step 7: Verify the join key matches**

```bash
grep -o 'http://192.168.3.18:8080/health' stacks/ct-mgmt/dashboard-src/server.js stacks/ct-mgmt/dashboard-src/src/services.js
```

Expected: exactly one match in each file. A mismatch silently breaks the health indicator on the tile.

- [ ] **Step 8: Rebuild the dashboard**

`infra deploy dashboard` recreates the container **without** rebuilding, so it will not pick up source changes.

```bash
ssh root@192.168.3.12 'cd /opt/stacks/ct-mgmt && docker compose up -d --build dashboard'
```

Then load `http://home.lan` and confirm the tile appears with a green health indicator.

- [ ] **Step 9: Commit**

```bash
git add stacks/ct-mgmt/gatus/config.yaml stacks/ct-mgmt/dashboard-src/server.js stacks/ct-mgmt/dashboard-src/src/services.js stacks/ct-mgmt/dashboard-src/public/icons/openwebui.svg
git commit -m "feat(ct-mgmt): monitor and surface ct-chat"
```

- [ ] **Step 10: Register the Portainer environment**

Portainer UI → Environments → Add environment → Docker Standalone → Agent → `192.168.3.18:9001`.

**Do this within 72 hours of the agent starting.** After that the agent shuts its API down and needs `docker restart portainer-agent` on ct-chat first.

Expected: the environment connects and lists both containers.

---

## Task 10: Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/hardware.md`
- Create: `stacks/ct-chat/README.md`

**Interfaces:**
- Consumes: everything above, plus the actual measured disk usage.
- Produces: the documentation half of the checklist.

- [ ] **Step 1: Add the CT section to `CLAUDE.md`**

Insert after the `### ct-workout` section, matching the established format exactly. **Keep the `<PERSONAL_DOMAIN>` placeholder** — this file is sanitised for public release.

```markdown
### ct-chat (LXC — VMID 115 on proxmoxmain)
- **IP:** 192.168.3.18
- **User:** root
- **OS:** Debian 13 (Trixie), unprivileged LXC
- **SSH:** Port 22, key-based auth (no password)
- **Resources:** 2 vCPU, 2048MB RAM, 512MB swap, 16GB disk
- **Role:** AI chat frontend. Runs Open WebUI against a single OpenRouter account, publicly reachable at `chat.<PERSONAL_DOMAIN>` via the existing Cloudflare Tunnel and gated by Cloudflare Access. Native Android/iOS client is Conduit.
- **Stack:** `/opt/stacks/ct-chat/docker-compose.yml` (local copy: `stacks/ct-chat/`)
- **Ports:** 8080 (Open WebUI HTTP)
- **Config notes:** AppArmor unconfined + proc/sys rw mount for Docker compatibility. Image pinned to `v0.11.0`; upstream ships breaking changes between minors, and Conduit v4.0.0 is the tested pair. **One OpenRouter key drives every model-backed slot** — chat, embeddings, STT, TTS and image generation — because each OpenAI-compatible base URL defaults to `${OPENAI_API_BASE_URL}`. **`RAG_EMBEDDING_ENGINE=openai` and `AUDIO_STT_ENGINE=openai` are load-bearing RAM controls, not preferences:** their empty defaults load SentenceTransformers (~500MB/worker) and a local Whisper instance, and the 2GB allocation assumes neither ever loads. Web search is Brave (free tier 2k/mo) with `WEB_SEARCH_CONCURRENT_REQUESTS=1` and web-search embedding bypassed — Cloudflare's fixed 100s idle timeout plus OWUI's tool-call buffering (upstream #16747) means a slow search surfaces a 524 even though the answer still arrives over the WebSocket. **`WEBUI_AUTH_TRUSTED_EMAIL_HEADER` is deliberately unset:** port 8080 is LAN-reachable, so trusting Cloudflare Access's email header would let any LAN host forge authentication. State is a `./data` bind mount (not a named volume), so ct-backup captures the full `/opt/stacks` tree plus an online SQLite `.backup`. Memory uses the Adaptive Memory community function because native Personalization memory only honours ~3 entries (upstream #19196) — re-test memory after every OWUI upgrade. No `.lan` hostname by design; LAN traffic round-trips through the WAN. Runbook: `stacks/ct-chat/README.md`.
```

- [ ] **Step 2: Update the three list locations in `CLAUDE.md`**

1. The proxmoxmain **CTs:** line — append `, ct-chat (VMID 115)`.
2. The Network Layout SSH tree — add `  ├── ssh ct-chat        → 192.168.3.18:22   (root, key auth)` in the blvckmain block, matching the existing column alignment.
3. The **Services** prose section — add a line:

```
Open WebUI runs on ct-chat (https://chat.<PERSONAL_DOMAIN> publicly, http://192.168.3.18:8080 on LAN) as a self-hosted ChatGPT equivalent over a single OpenRouter account, with persistent memory, Brave-backed web search, and Conduit as the native Android/iOS client. Public access is gated by Cloudflare Access; there is deliberately no `.lan` hostname.
```

- [ ] **Step 3: Add the `docs/hardware.md` row**

Measure the real usage first:

```bash
ssh root@proxmoxmain 'lvs --noheadings -o lv_name,lv_size,data_percent /dev/pve/vm-115-disk-0 2>/dev/null || lvs | grep 115'
```

Then add to the proxmoxmain **LXC Boot Disk Allocations** table, after the row for 114:

```markdown
| 115 | ct-chat | vm-115-disk-0 | 16GB | <measured>% |
```

- [ ] **Step 4: Write `stacks/ct-chat/README.md`**

Must cover setup **and restore**, not just setup. Required sections:

- **Deploy:** copy the stack, `cp .env.example .env`, generate `WEBUI_SECRET_KEY` with `openssl rand -hex 32`, fill the two API keys and the TTS voice, `docker compose up -d`, register the first account, then close signup **in the Admin Panel**.
- **Configuration semantics — read before editing anything:** most Open WebUI settings are persistent `ConfigVar`s. The compose file seeds the database on **first boot only**; afterwards the database is authoritative and editing `.env` or `docker-compose.yml` changes nothing on a running instance. Change persistent settings in the **Admin Panel**, and mirror the change into compose so a rebuild from an empty `data/` reproduces it. Known persistent here: `ENABLE_SIGNUP`, `DEFAULT_USER_ROLE`, `WEBUI_URL`, `OPENAI_API_BASE_URL`, `ENABLE_WEB_SEARCH`, `BRAVE_SEARCH_API_KEY`, `WEB_SEARCH_CONCURRENT_REQUESTS`, `BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL`. Known *not* persistent (compose is authoritative every boot): `WEBUI_SECRET_KEY`, `WEB_SEARCH_ENGINE`, `AUDIO_STT_ENGINE`, `AUDIO_TTS_ENGINE`, `IMAGE_GENERATION_ENGINE`.

  `ENABLE_PERSISTENT_CONFIG=False` would invert this and make compose authoritative for everything. It was considered and rejected: upstream warns the Admin Panel still looks editable while silently discarding changes. Do not switch without accepting that trade.
- **Restore:** restore `/opt/stacks/ct-chat` from restic **including `.env`**; the SQLite dump at `sqlite/ct-chat-openwebui.db.gz` is the crash-consistent copy and should be preferred over the rsynced live `data/webui.db` if the two disagree. Restoring `data/` without the original `WEBUI_SECRET_KEY` invalidates every session and breaks decryption of data at rest — restore the `.env` first. **`webui.db` carries the service configuration, not just chats** — a restore that skips it comes back with a seeded-from-compose config, losing the model allowlist, web-search settings and installed functions.
- **Upgrading:** bump the pinned tag, and **re-test memory** (Task 4 Steps 4–5) because Adaptive Memory is a third-party function. Check Conduit's supported-version claim before bumping a minor.
- **Rollback:** pin `v0.10.2` *and* install a correspondingly older Conduit. Never mix v0.10.2 with Conduit v4.0.0.
- **Model swaps:** `AUDIO_TTS_VOICE` is specific to `AUDIO_TTS_MODEL` and must change with it.
- **Known limitations:** 524s on slow tool calls; 100 MB attachment cap; no LAN fallback if the tunnel is down.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/hardware.md stacks/ct-chat/README.md
git commit -m "docs(ct-chat): document the Open WebUI deployment and restore path"
```

- [ ] **Step 6: Final checklist audit**

Walk `docs/new-ct-checklist.md` top to bottom against this deployment and confirm every box, especially the live halves:

```bash
ssh root@192.168.3.18 'command -v rrsync && command -v sqlite3 && infra --version'
ssh root@192.168.3.12 'infra ls | grep -i chat'
ssh root@192.168.3.13 'restic ls latest | grep -c ct-chat'
git status --short
```

Expected: all present, a non-zero restic count, and a clean working tree.

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: container and stack → Tasks 1–2; single-credential slot table → Tasks 2–3; memory → Task 4; web search and the 100 s ceiling → Task 5; exposure, Access, header-trust decision, Cloudflare limits, cost containment → Task 6; fleet registration → Task 7; backups → Task 8; monitoring and Portainer → Task 9; documentation → Task 10. The spec's *Resources* section is enforced by Task 2 Step 9 (proving no local models load) rather than by a standalone task, since the allocation is only meaningful in conjunction with that proof.

**Two spec items deliberately not implemented as tasks**, because the spec records them as follow-up rather than launch scope: raising `RAG_EMBEDDING_BATCH_SIZE` (Task 3 Step 1 handles it reactively if batching errors appear) and adding a second family account.

**Known gaps requiring live confirmation**, each with a defined fallback in-line rather than left open: the Debian 13 template filename (Task 1 Step 2), whether `curl` exists in the OWUI image (Task 2 Step 5), the SQLite filename (Task 4 Step 1), the exact Access status code (Task 6 Step 6), Conduit's minimum OWUI version (Task 6 Step 8), and the restic staging path for the SQLite dump (Task 8 Step 11).

**Correction found during review, worth recording.** The first draft closed signup by editing `.env` and re-running `docker compose up -d`. That is wrong: `ENABLE_SIGNUP` is a persistent `ConfigVar`, so the edit would have appeared to succeed while leaving signup open on a publicly exposed instance — exactly the failure mode that stays invisible until someone registers. The spec's configuration table is expressed as environment variables throughout, which quietly implies env is authoritative; it is only authoritative on first boot. Task 2 Step 7 now uses the Admin Panel and Step 7b probes the signup endpoint directly rather than trusting the toggle. The same correction propagates to Task 3 Step 1 (RAG batch size), Task 5 Step 3 (web-loader bypass), Task 8 Step 3 (the SQLite dump is a *configuration* backup) and the README section in Task 10 Step 4.

**Name consistency checked:** `open-webui` container name, the `http://192.168.3.18:8080/health` ping join key across `server.js` and `services.js`, the `openwebui` sqlite-dump name → `SQLITE_DB_OPENWEBUI` → staging file `sqlite/ct-chat-openwebui.db.gz`, and `webui.db` between Task 4 Step 1 and Task 8 Step 4.
