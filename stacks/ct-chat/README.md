# ct-chat — Open WebUI on OpenRouter

Self-hosted ChatGPT equivalent. One container, one OpenRouter credential, no
local model inference. Public at `chat.<PERSONAL_DOMAIN>` via the existing
Cloudflare Tunnel, gated by Cloudflare Access. Native mobile client is
[Conduit](https://github.com/cogwheel0/conduit).

- Spec: `docs/superpowers/specs/2026-07-30-ct-chat-openwebui-design.md`
- Plan: `docs/superpowers/plans/2026-07-30-ct-chat-openwebui.md`

## Read this before editing any setting

**Most Open WebUI settings are persistent `ConfigVar`s.** They are read from the
environment **only on first boot**, copied into `data/webui.db`, and from then on
**the database wins and environment changes are silently ignored.**

Practical consequences:

- To change a persistent setting on a running instance, use the **Admin Panel**.
  Editing `.env` and running `docker compose up -d` will appear to succeed and
  change nothing.
- Mirror any Admin Panel change back into `docker-compose.yml` so a rebuild from
  an empty `data/` reproduces it.
- `docker-compose.yml` is a **first-boot seed, not live state**. `data/webui.db`
  is the live configuration.

Verified persistent: `ENABLE_SIGNUP`, `DEFAULT_USER_ROLE`, `WEBUI_URL`,
`OPENAI_API_BASE_URL`, `OPENAI_API_KEY`, `ENABLE_WEB_SEARCH`,
`BRAVE_SEARCH_API_KEY`, `WEB_SEARCH_CONCURRENT_REQUESTS`,
`BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL`.

Verified **not** persistent (compose wins every boot): `WEBUI_SECRET_KEY`,
`WEB_SEARCH_ENGINE`, `AUDIO_STT_ENGINE`, `AUDIO_TTS_ENGINE`,
`IMAGE_GENERATION_ENGINE`.

`ENABLE_PERSISTENT_CONFIG=False` would invert this and make compose
authoritative for everything. Considered and **rejected**: upstream warns the
Admin Panel still looks editable while silently discarding changes, which is a
worse failure mode than seed semantics. Do not switch without accepting that.

## Two things that are load-bearing, not preferences

**1. `RAG_EMBEDDING_ENGINE=openai` and `AUDIO_STT_ENGINE=openai` are RAM
controls.** Left empty, the first loads SentenceTransformers (~500 MB *per
worker*) and the second runs a local Whisper instance on the backend. The
2048 MB allocation assumes neither ever loads. If you ever null either one, this
CT needs 4 GB.

**2. Every OpenRouter slot is configured explicitly — do not "simplify".**
The env reference claims each per-slot OpenAI-compatible base URL defaults to
`${OPENAI_API_BASE_URL}`. **That is false at first-boot seeding** (verified on
v0.11.0, 2026-07-30): `rag.openai.*`, `audio.stt.openai.*`, `audio.tts.openai.*`
and `image_generation.openai.*` all seeded to the literal
`https://api.openai.com/v1` with an **empty** key. Chat worked; embeddings, STT,
TTS and image generation would every one have failed. All eight variables are
therefore set explicitly in `docker-compose.yml`.

## Deploy

```bash
ssh root@192.168.3.18 'install -d -m 755 /opt/stacks/ct-chat'
scp docker-compose.yml .env.example root@192.168.3.18:/opt/stacks/ct-chat/
ssh root@192.168.3.18 'cd /opt/stacks/ct-chat && cp .env.example .env && chmod 600 .env \
  && sed -i "s|^WEBUI_SECRET_KEY=.*|WEBUI_SECRET_KEY=$(openssl rand -hex 32)|" .env'
```

Then fill in `OPENROUTER_API_KEY`, `BRAVE_SEARCH_API_KEY`, `WEBUI_URL` and
`AUDIO_TTS_VOICE`. Validate both keys **before** first boot — a bad key becomes
a persistent ConfigVar:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://openrouter.ai/api/v1/key \
  -H "Authorization: Bearer $OPENROUTER_API_KEY"
curl -s -o /dev/null -w '%{http_code}\n' \
  "https://api.search.brave.com/res/v1/web/search?q=test&count=1" \
  -H "X-Subscription-Token: $BRAVE_SEARCH_API_KEY"
```

Both must return `200`. Then `docker compose up -d`, register the first account
(it becomes admin), and **close signup in Admin Panel → Settings → General**.

Confirm signup is actually closed:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST http://192.168.3.18:8080/api/v1/auths/signup \
  -H 'Content-Type: application/json' \
  -d '{"name":"probe","email":"probe@example.invalid","password":"probe-probe-probe"}'
```

Must be 4xx. If it returns 200 an account was just created — delete it in
Admin Panel → Users.

## Restore

Order matters.

1. **Restore `.env` first.** It carries `WEBUI_SECRET_KEY`, which signs JWTs and
   encrypts data at rest. Restoring `data/` under a *different* secret key
   invalidates every session and breaks decryption of at-rest fields.
2. **Restore the stack tree**, from the ct-backup full-stack rsync:
   `stacks/ct-chat/` in the restic snapshot.
3. **Prefer the SQLite dump over the rsynced database.** The online `.backup`
   snapshot at `sqlite/ct-chat-openwebui.db.gz` is taken through SQLite's own
   backup API and is therefore crash-consistent.

   The rsynced copy is *not*. The database runs in **WAL mode**, and while the
   rsync does capture all three files (`webui.db`, `-wal`, `-shm` — verified in
   the 2026-07-30 snapshot), it copies them at three different instants with a
   live writer running, so they can be mutually inconsistent. If the two sources
   disagree, the dump is authoritative.

   Verified end-to-end on 2026-07-30 (snapshot `67de5f4d`): the 20,227-byte
   gzip restored to a 659,456-byte database, `pragma integrity_check` returned
   `ok`, and all 341 config rows survived.

   ```bash
   restic dump latest /var/backup-staging/sqlite/ct-chat-openwebui.db.gz \
     | gunzip > data/webui.db
   sqlite3 data/webui.db 'pragma integrity_check;'
   ```

4. `docker compose up -d`, then verify: login works, a chat streams, memories are
   present, and the model allowlist is intact.

**`webui.db` is the service configuration, not just chat history.** A restore
that skips it comes back seeded from compose and silently loses the model
allowlist, web-search settings and installed functions.

## Upgrading

1. **Check disk first.** The image unpacks to ~7 GB and `docker compose pull`
   fetches the new one before releasing the old, so an upgrade needs ~14 GB of
   images at once. The rootfs is 32 GB for this reason. `df -h /` before pulling.
2. Check Conduit's supported Open WebUI version before bumping a minor. The
   tested pair is **OWUI v0.11.0 + Conduit v4.0.0**.
3. Bump the pinned tag in `docker-compose.yml`, `docker compose pull && up -d`.
4. **Re-test memory.** Adaptive Memory is a third-party function; an OWUI
   upgrade can break it silently. Store a fact, start a *new* chat, confirm
   recall.
5. Confirm no local models loaded:
   `docker logs open-webui | grep -icE 'sentence-transformers|whisper'` → 0.

**Rollback:** pin `v0.10.2` *and* install a correspondingly older Conduit.
Never mix v0.10.2 with Conduit v4.0.0.

## Voice, image generation and the HTTPS requirement

**Voice input and hands-free call mode only work over `https://chat.<PERSONAL_DOMAIN>`,
never over `http://192.168.3.18:8080`.** Browsers gate
`navigator.mediaDevices.getUserMedia` behind a secure context, and a bare LAN IP
over plain HTTP is not one (only `localhost` is exempt). The microphone button
will be unavailable or fail there, with nothing wrong server-side.

This makes the public hostname *required* for voice, not merely convenient — a
consequence of the deliberate no-`.lan`-hostname decision. Conduit is unaffected:
it is a native app with its own microphone permission.

**Text-to-speech returns raw PCM**, not mp3 — `audio/pcm;rate=24000;channels=1`.
Open WebUI transcodes it via pydub + ffmpeg (`transcode_audio_to_mp3`, which
explicitly handles "raw PCM audio (e.g. Gemini-TTS via OpenRouter/LiteLLM)"), so
no configuration is needed. It does mean **ffmpeg must be present in the image**
and `BYPASS_PYDUB_PREPROCESSING` must stay unset, or you get unplayable audio.

**Speech-to-text auto-detects language and can get it wrong on short clips.** A
synthetic English test clip round-tripped as Japanese katakana. Real dictation
should be fine; if it consistently mis-detects, look for an STT language setting
rather than assuming the model is broken.

**`IMAGE_SIZE` must be set explicitly.** Open WebUI defaults to `512x512` and
*does* send it — `routers/images.py` builds `{'size': form_data.size or
IMAGE_SIZE}` — but 512x512 is below the minimum pixel budget of current image
models, so generation fails with an opaque 400. Verified 2026-07-31:

| Model | 512x512 | 1024x1024 | no size |
|---|---|---|---|
| `google/gemini-2.5-flash-image` | 400 "Request contains an invalid argument" | **200** | 200 |
| `openai/gpt-image-2` | 400 "below the current minimum pixel budget" | — | — |

Pinned to `1024x1024`. Omitting `size` also works for Gemini, but the UI's size
selector can reintroduce a value, so an explicitly valid size is the safer fix.
Note `IMAGE_AUTO_SIZE_MODELS_REGEX_PATTERN` defaults to `^gpt-image`, so Gemini
models never get the `auto` escape hatch.

Image generation costs **~$0.039 per image** — roughly 114× a memory-extraction
turn. `USER_PERMISSIONS_FEATURES_IMAGE_GENERATION` can scope it per user.

## Rotating the OpenRouter key — three places, not one

The key is stored in three independent locations. Missing any one leaves a
component silently broken:

1. **`.env`** — `OPENROUTER_API_KEY`. Seeds first boot only.
2. **`data/webui.db` config** — `openai.api_keys`, plus `rag.openai.api_key`,
   `audio.stt.openai.api_key`, `audio.tts.openai.api_key` and
   `image_generation.openai.api_key`. These are persistent ConfigVars, so change
   them in **Admin Panel → Settings** (Connections / Documents / Audio / Images),
   not by editing `.env`.
3. **The Adaptive Memory function's valves** — `llm_api_key` on
   `adaptive_memory_v3`, set via Admin Panel → Functions → its gear icon.

Symptom of forgetting (3): chat keeps working while memory silently stops
recording anything, because the filter logs its failure and returns normally.

## Adaptive Memory configuration

Installing the function and setting `llm_model_name` is **not sufficient**. Its
`llm_provider_type` defaults to `"ollama"` and its endpoint to
`http://host.docker.internal:11434/api/chat`, so out of the box it tries to
reach a local Ollama that does not exist here, fails after three retries, logs
`No valid memories to process after filtering/identification`, and returns
normally — the UI shows no error at all.

Four valves are required:

| Valve | Value |
|---|---|
| `llm_provider_type` | `openai_compatible` |
| `llm_api_endpoint_url` | `https://openrouter.ai/api/v1/chat/completions` |
| `llm_api_key` | the OpenRouter key (mandatory — the code raises without it) |
| `llm_model_name` | `qwen/qwen3.7-flash` |

Note the endpoint needs the **full path** including `/chat/completions`, unlike
the base URLs used elsewhere in this stack.

The function must also be both **Active** and **Global** — a filter that is
imported but not enabled does nothing, and one that is active but not global
must be attached to each model individually.

### ⚠ The installed function is PATCHED — do not blindly re-import

`adaptive_memory_v3` as published is **incompatible with Open WebUI 0.11.0**.
OWUI 0.11 made `MemoriesTable.get_memories_by_user_id` async; the function calls
it synchronously inside the already-async `_get_formatted_memories`, so
retrieval raises:

```
Error getting formatted memories: 'coroutine' object is not iterable
RuntimeWarning: coroutine 'MemoriesTable.get_memories_by_user_id' was never awaited
```

Consequence: **stored memories are never injected into context.** Extraction can
appear to work while recall silently never happens. It also produced a knock-on
400 from OWUI's own chat pipeline (`Input required: specify "prompt" or
"messages"`).

**Four** calls needed `await`, in two rounds — the first pass fixed only the
`Memories` call and the store path then failed separately with
`'coroutine' object has no attribute 'role'` (an unawaited `Users` lookup handed
to `add_memory(user=...)`, which reads `user.role`). Both classes are the same
underlying cause: OWUI 0.11 made these model-layer APIs async.

| Line (approx) | Enclosing function | Call |
|---|---|---|
| 2010 | `_get_formatted_memories` | `await Memories.get_memories_by_user_id(...)` |
| 1130 | `_summarize_old_memories_loop` | `await Users.get_user_by_id(...)` |
| 2199 | `_process_user_memories` | `await Users.get_user_by_id(...)` |
| 3753 | `process_memories` | `await Users.get_user_by_id(...)` |

All four sites are inside `async def` bodies, so `await` is valid at each. The
memory helpers it imports from `open_webui.routers.memories` (`add_memory`,
`query_memory`, `delete_memory_by_id`) were already awaited correctly.

To confirm the patch is intact:

```bash
sqlite3 data/webui.db 'select content from function where id="adaptive_memory_v3";' \
  | grep -c "await Users.get_user_by_id"            # want 3
sqlite3 data/webui.db 'select content from function where id="adaptive_memory_v3";' \
  | grep -c "await Memories.get_memories_by_user_id" # want 1
```

- Unmodified original: **`/root/adaptive_memory_v3.orig.py`** on ct-chat
  (`/root/adaptive_memory_v3.prepatch2.py` is the intermediate state).
- The patched body lives in `function.content` in `data/webui.db`, so it **is**
  captured by the nightly SQLite dump and survives a restore.
- **Re-importing or updating the function from the community site reverts this
  patch.** If memory stops recalling after any such update, this is the first
  thing to check: `grep -c "await Memories.get_memories_by_user_id"` against the
  stored content should be 1.

This is the concrete form of the plugin-maintenance risk the spec accepted when
choosing Open WebUI's community memory over LibreChat's native implementation.

To confirm it is working, check for the absence of `11434` in the logs
*since the container started* (mind the timezone: log timestamps are local while
`docker logs --since` is UTC):

```bash
docker logs --since <container-start-utc> open-webui 2>&1 | grep -c 11434   # want 0
sqlite3 data/webui.db 'select count(*) from memory;'                        # want > 0
```

Model choice is cost-insensitive here: at ~2k in / 200 out tokens per turn,
`qwen3.7-flash` runs about $0.09 per 1,000 turns, against $0.0387 for a single
generated image. Optimise for extraction reliability, not price.

## Model swaps

`AUDIO_TTS_VOICE` is specific to `AUDIO_TTS_MODEL` — each TTS model publishes its
own voice set, so a voice valid for one is usually invalid for another. Change
both together. Current: `hexgrad/kokoro-82m` with `af_bella`.

OpenRouter's catalogue moves fast. Alternatives per slot: STT
`openai/whisper-large-v3-turbo`; TTS `deepgram/aura-2`,
`microsoft/mai-voice-2-flash`; images `black-forest-labs/flux.2-klein-4b`,
`openai/gpt-image-2`.

## Known limitations

1. **524s are possible on slow tool calls.** `/chat/completions` buffers until
   the whole tool cycle finishes ([OWUI #16747]), and Cloudflare's 100 s idle
   timeout is not raisable on the free plan. The answer still arrives over the
   WebSocket, so it is an ugly error rather than lost work. Mitigations already
   applied: `WEB_SEARCH_RESULT_COUNT` at its default of 3,
   `WEB_SEARCH_CONCURRENT_REQUESTS=1`, web-search embedding bypassed. Reserve
   lever if it persists: `BYPASS_WEB_SEARCH_WEB_LOADER=true` — snippets only, no
   page fetches, a real quality regression.
2. **100 MB attachment cap** through the tunnel (Cloudflare free plan).
3. **LAN traffic round-trips through the WAN.** There is no `.lan` hostname by
   design. Add one with
   `infra dns add chat.lan http://192.168.3.18:8080` if that becomes annoying —
   but note it makes the LAN a second untrusted entry point, which is exactly why
   `WEBUI_AUTH_TRUSTED_EMAIL_HEADER` stays unset (see below).
4. **No access at all if the tunnel is down**, as a consequence of (3).
5. **Native memory is capped.** Open WebUI's own Personalization memory only
   honours ~3 entries ([OWUI #19196]), which is why Adaptive Memory is installed.
6. **Per-turn memory extraction costs tokens** on every message, whether or not
   anything was worth remembering. Point it at a cheap model.

## Security notes

**`WEBUI_AUTH_TRUSTED_EMAIL_HEADER` is deliberately unset.** Cloudflare Access
injects `Cf-Access-Authenticated-User-Email`, and trusting it would give
seamless SSO — but port 8080 is directly reachable on the LAN, so any LAN host
could forge that header and authenticate as any user. Open WebUI's own password
auth stays authoritative; Access is a strictly additive outer gate. Verify with:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H 'Cf-Access-Authenticated-User-Email: attacker@example.com' \
  http://192.168.3.18:8080/api/v1/auths/
```

Must not authenticate.

**Spend containment.** This is a public endpoint spending real money. Keep a
modest prepaid OpenRouter balance with auto-topup **off** — prepaid credit is the
only cap that survives a compromised login. Note the key currently in use is
shared with the `claude-personal` Claude Code wrapper, so ct-chat and local
coding draw on the same limit; minting a dedicated key with its own limit would
isolate them.

[OWUI #16747]: https://github.com/open-webui/open-webui/issues/16747
[OWUI #19196]: https://github.com/open-webui/open-webui/discussions/19196
