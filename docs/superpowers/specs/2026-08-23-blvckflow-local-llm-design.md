# BLVCKFlow — Local LLM Inference Server — Design

**Status:** Complete 2026-08-24 — phases 1 and 2 done (server + opencode client); only the sustained thermal hold remains deferred
**Goal:** Serve `Qwen3.8-27B` (abliterated) from a localhost-only OpenAI-compatible endpoint on `BLVCKFlow`, on the Radeon 8060S iGPU via Vulkan, and establish measured performance and thermal figures for the machine under sustained inference load.

## Background

`BLVCKFlow` is an ASUS ROG Flow Z13 (2025) with a Ryzen AI Max+ 395 "Strix Halo" APU (gfx1151), 64 GB of soldered LPDDR5X-8000, and a Radeon 8060S iGPU sharing that memory. The unified-memory design makes it capable of holding models that would need a discrete card with far more VRAM than any laptop carries. This spec puts that capability to use.

Two consumers were considered and both were cut from phase 1:

- **`ct-chat`'s Open WebUI** was the original target. It was dropped because the topology is hostile: `ct-chat` is not a tailnet node, and `main-gateway` advertises the LAN *into* the tailnet, which is one-way. `ct-chat` could therefore only reach this laptop by LAN address, and only while it was home and awake. Connecting them properly would have meant installing Tailscale into an unprivileged LXC. The value did not justify the infrastructure change.
- **`claude-personal`** (`~/.local/bin/claude-personal`, Claude Code against OpenRouter's Anthropic-compatible endpoint with DeepSeek v4 slugs) was the second target. Claude Code speaks the Anthropic Messages API, so pointing it at llama.cpp would require a translating proxy such as LiteLLM or claude-code-router. That shim is avoided entirely by choosing a client that speaks OpenAI-compatible natively — `opencode`, `crush`, or `aider`.

**Client selection is deliberately deferred** until measured tok/s exists. All three candidates talk to the same endpoint, so the choice costs nothing to postpone and is better made against real numbers than against an estimate.

### Goals driving the design

Stated priorities, in the owner's words: **privacy**, **offline capability**, and **capability/tinkering**. Cost reduction was explicitly *not* a driver. The design therefore optimises for the best model the hardware can hold and for working without a network, not for the cheapest tokens.

### Verified facts

| Fact | Value | How verified |
|---|---|---|
| CPU / iGPU | Ryzen AI Max+ 395 w/ Radeon 8060S, 32 threads | `lscpu`, `lspci` |
| iGPU PCI ID | `1002:1586`, `c4:00.0`, `card1` / `renderD128` | `lspci -nn`, `/sys/class/drm` |
| RAM visible to OS | 58 GiB total, ~55 GiB available at rest | `free -h` |
| iGPU carve-out (current) | **4 GiB** (`mem_info_vram_total` = 4294967296) | `/sys/class/drm/card1/device/` |
| Vulkan driver | `vulkan-radeon` (RADV) 1:26.1.6-1, ICD loader 1.4.357.0 | `pacman -Q` |
| ROCm / HIP | **not installed** | `pacman -Q` |
| Existing inference tooling | **none** — no ollama, no llama.cpp; `docker` present | `command -v` |
| Free space on `/` | 566 GiB of 677 GiB | `df -h /` |
| `mkinitcpio` HOOKS | includes `modconf` | `/etc/mkinitcpio.conf:55` |
| Kernel cmdline | no `amdgpu` parameters set | `/proc/cmdline` |
| Loader entries | `arch.conf`, `cachyos.conf`; booting `linux-cachyos` | `/boot/loader/entries` |
| `llama-cpp` in Arch `extra` | `0.2.0-1`, installed size 17.73 MiB, depends on `ggml` | `pacman -Si` |
| `opencode` in Arch `extra` | `1.18.16-1` | `pacman -Ss` |

### Facts established during build (2026-08-23)

| Fact | Value | Consequence |
|---|---|---|
| `ggml-vulkan` exists | **yes** — `0.21.0-1`, installed | The stale-db risk is retired; no source build needed on that account |
| Vulkan device | `AMD Radeon 8060S Graphics (RADV STRIX_HALO)`, radv, API 1.4.354 | Backend confirmed present and correct |
| `mem_info_gtt_total` | **31,403,888,640 B — 29.25 GiB** | Above the 24 GiB bar; **no memory configuration required** |
| RADV heap[1] `DEVICE_LOCAL` | **22.16 GiB** | RADV sizes the device-local heap from GTT, **not** from the 4 GiB carve-out — this is the direct proof the carve-out never needed raising |
| RADV heap[0] `HOST_VISIBLE` | 11.08 GiB | ~33 GiB total exposed to Vulkan |
| Headroom for KV + compute | **~1.27 GiB** after the chosen 20.89 GiB Q6_K model in the device-local heap (~6.5 GiB had Q4_K_M been kept) | 32K context is the main open risk; see *Quantisation* and *Fallbacks* |
| `llama-cpp` build | `0.2.0-dev`, build **10566**, commit `bb4caa7540` | Postdates the 2026-05 merge |
| `qwen35` arch support | **present in `libllama.so`** (with `qwen35moe`, `qwen3vl`, `qwen3next`) | **No source build needed.** Verified by inspecting the arch table directly rather than by a 20+ GB trial download |
| Device memory as llama.cpp sees it | `Vulkan0` — **34,045 MiB total, 33,009 MiB free** | It aggregates both heaps. Comfortable in total, but the *device-local* heap is what matters for speed, and Q6_K nearly fills it — so "33 GiB free" must not be read as reassurance |
| `--flash-attn` on this build | tri-state, defaults to `auto` | Correctly omitted from the unit; hardcoding it would only remove llama.cpp's own judgement |
| `--jinja` on this build | **already default-enabled** | Kept explicit anyway, so a future default flip cannot silently break tool-calling |
| `orcarouter` repo access | **gated** — HTTP 401, `x-error-code: GatedRepo` | Requires an approved HF account; owner chose to authenticate rather than substitute |
| `douyamv` alternative | **empty repo** — no GGUF files despite its description | Ruled out; recorded so nobody re-evaluates it |
| `hf` 1.28 **Xet backend stalls** | Download dies silently at ~200 MB — process stays alive, 0 bytes for 40 s+. Reproduced on two separate files | **Always pass `HF_HUB_DISABLE_XET=1`** for large pulls on this host. The process does not exit or error, so a stalled download looks identical to a slow one — check byte deltas, not liveness |

### Model facts

`Qwen3.8-27B` shipped 2026-08-14 under Apache 2.0, after this author's knowledge cutoff; every figure below was checked against the live model card rather than recalled.

| Fact | Value |
|---|---|
| Architecture | **Dense** 27B (not MoE), 64 layers, hidden dim 5120, hybrid linear + full attention |
| Modality | Native multimodal — image and video understanding |
| Context | 262K native, extensible to 1M via YaRN |
| BF16 size | 55.6 GB; official FP8 30.9 GB |
| Chosen build | `orcarouter/Qwen3.8-27B-Uncensored-GGUF`, **Q6_K, 22.43 GB / 20.89 GiB** (revised — see *Quantisation*) |
| Vision projector | separate `mmproj` f16, 0.9 GB — required only for image input |
| MTP head | embedded `nextn` block retained; usable for speculative decode |
| llama.cpp requirement | `qwen35` arch + MTP head merged **2026-05** — older builds will not load these files |

## Requirements

**In scope:**

- `llama.cpp` serving Q6_K over HTTP on `127.0.0.1` only.
- GPU offload genuinely on the iGPU via Vulkan, confirmed — not silently on CPU.
- An on-demand systemd **user** unit; explicitly *not* enabled at boot.
- Measured prefill and decode throughput, recorded in `~/dotfiles`.
- A sustained-load thermal test that doubles as the first real test of the EC power-cut mitigation.

**Out of scope:**

- Any `ct-chat` / Open WebUI integration.
- Any Anthropic-API shim, and any change to `claude-personal`.
- Client/agent configuration — was deferred to phase 2, now **done**: see *Phase 2*.
- ROCm. Vulkan/RADV is the reliable backend on Strix Halo; a tuned ROCm build wins roughly 3× only at very long context (128K–200K), which no phase-1 use case reaches.
- Vision input. The `mmproj` file is optional and left for later.
- Network exposure of any kind. The endpoint binds loopback.

## Architecture

```
opencode 1.18.21                  (phase 2 — DONE)
        │  OpenAI-compatible HTTP
        ▼
llama-server  127.0.0.1:8088      (systemd --user, on demand)
        │  ggml Vulkan backend
        ▼
RADV → Radeon 8060S (gfx1151)
        │  GTT — dynamic, system RAM
        ▼
58 GiB LPDDR5X-8000, ~256 GB/s
```

The bandwidth figure is the load-bearing number in this design. See *Performance expectations*.

## Design

### 1. Memory — measure before configuring

The obvious move is to raise the iGPU carve-out. **That is the wrong move on this hardware.** A large fixed UMA reserve reduces OS-visible RAM *and* GTT capacity; the established Strix Halo guidance is to keep the carve-out as small as the firmware permits (512 MB where offered) and let the GPU reach system memory through **GTT**, which is dynamic. The current 4 GiB carve-out is already acceptable and will not be touched.

Default GTT on recent `amdgpu` is approximately half of system RAM — around 29 GiB here — which already exceeds the draft's 15.65 GiB Q4_K_M plus its KV cache. **The likely outcome is that no memory configuration is needed at all.**

> **Resolved 2026-08-23: measured, and no configuration is needed.**
> `mem_info_gtt_total` = 29.25 GiB, and RADV exposes a **22.16 GiB `DEVICE_LOCAL` heap** — sized from GTT, not from the 4 GiB carve-out. The override below was never applied and the carve-out was never touched. The prediction held; this section is retained because the reasoning is the part worth keeping.

Therefore: measure GTT first (`amdgpu_top`, or `/sys/class/drm/card1/device/mem_info_gtt_total`). Only if it proves short, add:

```
# /etc/modprobe.d/amdgpu-gtt.conf
options amdgpu gttsize=49152
```

delivered through `~/dotfiles/system/hosts/BLVCKFlow/etc/modprobe.d/`, followed by `mkinitcpio -P` and a reboot. The `modconf` hook is present, so the setting reaches the initramfs.

This route is chosen over a kernel cmdline parameter for one specific reason: **it never touches a loader entry, so Secure Boot and `sbctl` stay entirely out of scope.** Loader entries on this host are hand-written and the machine boots with custom keys enrolled; keeping that path untouched removes the only genuinely risky failure mode in this design.

### 2. Runtime — llama.cpp with the Vulkan ggml backend

Arch splits the backends out of `llama-cpp` into `ggml`, which explains the 17 MiB package. `archlinux.org` lists **`ggml-vulkan`** as a split package and optional dependency of `ggml`, but the local sync database is 11 days stale and resolves only `ggml` and `ggml-sycl`.

**Consequently `pacman -Sy` comes first, and `ggml-vulkan` must be confirmed to resolve before proceeding.** If it does not exist under that name, fall back to a source build with `-DGGML_VULKAN=ON`.

A second, independent reason a source build may be forced: the model card states the `qwen35` architecture and MTP head merged in 2026-05 and that older releases will not load these files. `b10333` is expected to postdate that, but this is unverified until a load is attempted. **If the GGUF fails to load, build from source rather than debugging the packaged build.**

### 3. Model placement

Into a user-owned path — `~/.local/share/models/qwen3.8-27b-uncensored/`. User-owned rather than `/var/lib` because this is a single-user workstation and the service runs as a user unit. 566 GiB free makes size irrelevant.

#### Quantisation — revised to Q6_K (2026-08-23)

The draft specified **Q4_K_M**. The owner chose **Q6_K** instead after the reasoning was laid out, and that decision is recorded here with its consequence rather than quietly applied.

The argument for Q4_K_M was that on this hardware quantisation is a **speed** decision more than a quality one. Decode is memory-bandwidth-bound at ~256 GB/s, so throughput scales roughly inversely with working-set size:

| Quant | Size | vs Q4_K_M | Est. decode |
|---|---|---|---|
| Q4_K_M | 15.65 GiB | baseline | ~8–12 tok/s |
| Q5_K_M | 16.95 GiB | +8% | ~7–11 tok/s |
| **Q6_K** | **20.89 GiB** | **+33%** | **~6–9 tok/s** |
| Q8_0 | 25.24 GiB | +61% | ~5–7 tok/s |

Quality gains above Q4_K_M are small and diminishing; the speed cost is linear. Q6_K trades roughly a quarter of generation speed for a near-lossless working set. That is a legitimate preference, not an error — but it has a second-order effect the draft did not anticipate.

**The tight fit.** Q6_K at 20.89 GiB sits against a **22.16 GiB `DEVICE_LOCAL` heap** — about **1.27 GiB of slack**, which a 32K KV cache will very likely exceed. This partially reverses the "no memory configuration needed" finding above, which was measured against Q4_K_M's 15.65 GiB.

The failure mode is probably *not* a crash. llama.cpp reports ~33 GiB free because it aggregates the 11.08 GiB host-visible heap, so the likely outcome is the cache landing in the slower heap and costing throughput silently. **That makes it something to measure for, not merely to wait for a crash to reveal.** Compare measured tok/s against the ~6–9 estimate; a large shortfall points here.

Escalation ladder, cheapest first:

1. `--cache-type-k q8_0 --cache-type-v q8_0` — roughly halves the cache, minimal quality cost.
2. `--ctx-size 16384` — most real use never reaches 32K.
3. Raise GTT via `/etc/modprobe.d/amdgpu-gtt.conf` (`options amdgpu gttsize=49152`) + `mkinitcpio -P` + reboot — the override written off earlier, now back in play. Still Secure-Boot-safe, still no loader-entry edit.

Only step 3 costs a reboot, so it is the last resort rather than the opening move.

`Q5_K_M` (16.95 GiB) is the fallback that would restore comfortable headroom if Q6_K proves not worth its cost.

### 4. Service definition

`~/dotfiles/system/hosts/BLVCKFlow/etc/systemd/user/llama-server.service`, matching the host-specific pattern already used there for `notify-profile.service` and the `z13ctl` drop-in. Note this installs to **`/etc/systemd/user/`**, not `~/.config/systemd/user/` — that is what `system/install.sh` does with `hosts/<HOST>/etc/`, and following the repo's existing pattern beats matching this spec's first draft.

#### Fallbacks if the 32K context will not allocate

With Q6_K, only ~1.27 GiB of device-local headroom remains after the weights (it was ~6.5 GiB under the draft's Q4_K_M). If the KV cache exceeds it, apply in this order, cheapest first:

1. **Quantise the KV cache** — `--cache-type-k q8_0 --cache-type-v q8_0`, roughly halving it for little quality cost.
2. **Reduce context** to 16K, which most real use never reaches.
3. **`--no-kv-offload`**, keeping the cache in host memory. Slower, but heap[0] has 11.08 GiB spare.

```
llama-server \
  --model ~/.local/share/models/qwen3.8-27b-uncensored/Qwen3.8-27B-Uncensored-Q6_K.gguf \
  --host 127.0.0.1 --port 8088 \
  -ngl 99 -c 32768 --flash-attn --jinja
```

Each choice, and why:

- **`--host 127.0.0.1`** — loopback only. Privacy is a stated goal and there is no remote consumer in scope. No API key is needed because nothing off-machine can reach it.
- **Port 8088** — avoids 8080 (widely contended, and Open WebUI's port elsewhere in the fleet) and 11434 (ollama's).
- **`-ngl 99`** — offload every layer to the iGPU.
- **`-c 32768`** — the model's 262K native context would make the KV cache enormous on a 64-layer dense model. 32K is a working starting point, to be tuned against measured memory.
- **`--flash-attn`** — conditional. Flash-attention support on the Vulkan backend is less complete than on CUDA; if startup errors or output degrades, **drop this flag first** before investigating anything else. It is an optimisation, not a requirement.
- **`--jinja`** — loads the model's own chat template, which is what makes tool-calling work. Cheap now, and phase 2's agent clients depend on it.

Two observability packages are prerequisites and are not currently installed: **`vulkan-tools`** (for `vulkaninfo`) and **`amdgpu_top`** (for confirming real offload). Install both before the first run — without them, "GPU offload works" cannot be evidenced, only assumed.
- **Not enabled at boot.** The service pins ~17 GB and this is a battery-powered convertible. It is started on demand with `systemctl --user start llama-server`.

### 5. Thermal risk — the most important part of phase 1

The existing Z13 spec records that **sustained inference load on this machine can trigger an EC power cut**, with `asusctl armoury set ppt_pl1_spl 60` noted as the *untested* candidate mitigation. Sustained LLM decode is precisely the workload that provokes it.

> **Correction 2026-08-24: `asusctl` is not installed on this host and `asusd` is inactive.** The repo's `CLAUDE.md` claims power profiles come from asusd and that `asusctl armoury` exposes the PPT limits; neither is true here. **`z13ctl` owns all hardware control** — lighting, profiles, fan curves, TDP, undervolt and battery limit — having replaced asusctl because asusd 6.3.11 exposes no `xyz.ljones.Slash` interface for the GZ302EA and so cannot reach the rear lightbar. `CLAUDE.md` needs fixing.
>
> The working equivalent is `z13ctl tdp --set 60` (uniform) or `--set N --pl2/--pl3` to shape individual limits; `z13ctl tdp --get` reads them, `z13ctl status` reports temp/fans/profile/TDP, `--dry-run` previews, `--reset` restores stock. **As found, PL1 was 70W and PL2/PL3 86W** — above the ≤60W the mitigation calls for. Applied: uniform 60W. Note `--pl1` is an override *of* `--set`, not a standalone flag, and `--set 60 --pl2 86` would *raise* `apu_sppt`/`platform_sppt` from 70 to 86, so uniform 60 is the conservative choice.

Phase 1 therefore treats the benchmark as the first real test of that mitigation:

1. Cap PPT — `z13ctl tdp --set 60` (**not** `asusctl armoury`, which does not exist here).
2. Hold sustained generation for at least 10 minutes.
3. Watch for the EC cut.

If the machine survives, that retires a known unknown in the fleet documentation. If it does not, that finding is more valuable than any throughput number, and the next step is to walk PPT down rather than to tune inference. Either result gets written back to the Z13 spec and to `~/dotfiles`.

> **Status 2026-08-24: DEFERRED at the owner's decision. The EC power cut is neither reproduced nor retired.**
> Incidental evidence only: a ~2-minute `llama-bench` run at a uniform 60W held fine, with the APU at 51°C and fans at 4300 RPM. That is encouraging but **not** the test — the recorded failure mode is *sustained* load, so a 2-minute pass proves little. TDP has been left capped at a uniform 60W; `z13ctl tdp --reset` restores stock (which was PL1 70W / PL2-3 86W).
> Whoever runs this later: close anything unsaved first, since the failure mode is a hard power-off.
>
> **Trap, learned the hard way: `z13ctl tdp --set` is not a transient knob.** It edits the profile you are *currently running*, and when a firmware profile is active it **silently creates and activates a new profile literally named `custom`**. Running it here replaced the active `performance` rung (PL1 70W / PL2-3 86W) with a fabricated 60W `custom` profile that persisted in `~/.local/state/z13ctl/state.json`. Worse, custom profiles never write `platform_profile` and so **inherit the fan behaviour of whatever preceded them** — measured 4300 RPM under the custom profile versus **0 RPM** on firmware auto at the same temperature.
>
> **Always pass `--profile <name>` to edit a profile without applying it.** To undo: `z13ctl profile --set performance` restores the firmware rung, `z13ctl profile --delete custom` removes the stray, and the daemon may keep `custom` listed as an empty reserved slot afterwards (harmless — check `state.json` for the truth). All of this is documented in `~/dotfiles/CLAUDE.md`; it was not read before acting, which is the actual lesson.
>
> **The benchmark figures in this document were taken at a uniform 60W**, i.e. *below* the machine's stock 70W sustained limit. They are therefore conservative, and re-running at stock `performance` should be slightly faster on prefill (decode is bandwidth-bound and will barely move).

Power profiles come from **asusd**, not power-profiles-daemon, which is masked and uninstalled on this host.

### 6. Performance expectations

Stated up front so that measurement can contradict it. Strix Halo's 256-bit LPDDR5X-8000 gives roughly **256 GB/s**. A dense 27B model at **Q6_K** has a 20.89 GiB working set that must be read once per token, putting the hard ceiling near **11 tok/s** and the realistic figure around **6–9 tok/s**. (Under the draft's Q4_K_M those figures were ~15 and ~8–12.)

The consequence matters for phase 2: this is comfortable for chat and ad-hoc questions, but agentic coding loops consume tens of thousands of tokens per task and will feel slow. The retained **MTP speculative-decoding head** is the available lever, and measuring with and without it is part of phase 1.

Note the asymmetry of a dense model here: had this been a comparable MoE with ~3B active parameters, decode would be several times faster on the same hardware. Model choice was the owner's, made knowing this.

## Verification

No step is considered done without the stated evidence.

| Check | Command | Passing evidence |
|---|---|---|
| `ggml-vulkan` exists | `pacman -Sy && pacman -Si ggml-vulkan` | Package resolves with a `Repository` line |
| Vulkan sees the iGPU | `vulkaninfo --summary` | gfx1151 / 8060S listed under RADV |
| GTT capacity | `amdgpu_top` | GTT ≥ 24 GiB, else apply the modprobe.d override |
| Model loads | `llama-server` startup log | `qwen35` arch accepted, all 64 layers offloaded |
| Offload is real | `amdgpu_top` during generation | GPU busy, VRAM+GTT occupied; **CPU not carrying it** |
| Endpoint answers | `curl 127.0.0.1:8088/v1/models` | Model listed |
| Completion works | `curl` to `/v1/chat/completions` | Coherent response |
| Abliteration effective | a prompt the base model refuses | Answered rather than refused |
| Throughput | `llama-bench` | pp and tg figures recorded |
| MTP benefit | `llama-bench` with and without speculative decode | Delta recorded |
| Thermal hold | 10 min sustained generation at PPT 60 | No EC power cut |
| Not autostarted | `systemctl --user is-enabled llama-server` | `disabled` |

## Measured results (2026-08-24)

Built and verified. Everything below is measured on this machine, at **PL1/PL2/PL3 = 60W** and Q6_K.

### Throughput

`llama-bench -ngl 99 -p 512 -n 128 -r 2`:

| Test | Result |
|---|---|
| **pp512** (prefill) | **189.69 ± 1.75 tok/s** |
| **tg128** (decode) | **9.39 ± 0.01 tok/s** |
| Backend | **Vulkan**, RADV STRIX_HALO, `uma: 1`, `matrix cores: KHR_coopmat` |
| CPU backend resolved | `libggml-cpu-zen4.so` (correct microarch) |

**The prediction held.** The spec estimated 6–9 tok/s for Q6_K; actual is 9.39–9.48, marginally above the top of the range. Decode is confirmed bandwidth-bound: 20.88 GiB × 9.39 tok/s ≈ **210 GB/s effective against a 256 GB/s ceiling, ~82% efficiency**. That figure is also the proof that offload is genuine.

**The `gpu_busy_percent` and GRBM sensors are unreliable on this APU** — they read 1% and ~9W while the GPU was demonstrably saturated. Do not use them to verify offload here; use `llama-bench`'s backend line and the bandwidth arithmetic instead. An earlier revision of this document treated a 1% reading as alarming; it was noise.

### The Q6_K heap risk did not materialise

32K context allocates and runs at **the same speed as 4K** (105.44 vs 105.55 ms/token). No KV spill penalty was observed, and none of the three escalation steps was needed. The device-local/host-visible split turned out not to matter in practice — llama.cpp spanned both pools (VRAM 3994/4096 MiB full, GTT 20505 MiB) without incident.

### Capability verification

| Check | Result |
|---|---|
| Model loads | `qwen35` accepted, 27.32B params, 20.88 GiB |
| Endpoint | `127.0.0.1:8088`, 4 slots, `n_ctx_slot = 32768`, unified KV |
| Loopback only | **Verified** — `ss` shows `127.0.0.1:8088`; probe to `192.168.1.144:8088` refused |
| Completion correctness | `17 × 23 equals 391` |
| **Tool-calling** | **Works** — `finish_reason: tool_calls`, correct function name and valid JSON arguments. Phase 2's clients are unblocked |
| Abliteration | **Effective** — complied with a request stock models decline |

### Findings that change the design

**1. The MTP head is discarded.** llama.cpp logs every `blk.64.nextn.*` tensor as `unused tensor … ignoring`. The speculative-decoding head the model card advertises is present in the file (~320 MB of it) but unused by this build's graph. **The speculative-decode lever the spec counted on to recover throughput is therefore unavailable**, and would need explicit draft-model plumbing rather than working automatically.

**2. This is a heavy reasoning model, and that is the real usability problem — not the 9.4 tok/s.** With template defaults it spent **900 tokens reasoning and emitted no answer at all** (`finish_reason: length`, `content` empty) for a request as trivial as a limerick. At 9.4 tok/s that is ~96 seconds before a single visible word.

Controls, as measured against this model's template:

| Lever | Result |
|---|---|
| `chat_template_kwargs: {"enable_thinking": false}` | **Works.** Same question answered in 3 tokens |
| `reasoning_effort: low` / `medium` | Accepted |
| `reasoning_effort: high` / `minimal` | **HTTP 500** — the template raises on unexpected values |
| Server-side `--reasoning off`, `--reasoning-budget N`, `--reasoning-effort` | Available as flags |

Because the useful lever is per-request and client-driven, the unit does **not** hardcode a reasoning policy. Phase 2 must configure its client to pass `enable_thinking: false` for routine work, or set a `--reasoning-budget` server-side as a runaway guard. Left unmanaged, this model is unusable interactively regardless of tok/s.

**3. Arch builds `llama-cpp` with asserts enabled** — `warning: asserts enabled, performance may be affected`. There is likely free throughput in a source build with `NDEBUG`. Worth a phase-2 experiment; not a reason to abandon the package.

**4. `ggml-cpu` is a separate package and is required even at `-ngl 99`.** Without it, load fails with `make_cpu_buft_list: no CPU backend found` — a confusing error that looks like a memory problem but is not. Arch splits every backend (`ggml-cpu`, `ggml-blas`, `ggml-vulkan`, `ggml-cuda`, `ggml-hip`, `ggml-openvino`, `ggml-sycl`).

**5. `llama-cli` and `llama-completion` auto-enable conversation mode** when the model ships a chat template, and spin at 100% CPU on EOF stdin, emitting `> ` forever. Irrelevant to `llama-server`, but it wastes time when smoke-testing.

**6. `asusctl` does not exist on this host.** The spec's mitigation command was wrong. `z13ctl` owns all hardware control here — see *Thermal*.

## Phase 2 — client (done 2026-08-24)

**`opencode` 1.18.21** from Arch `extra`, chosen over `crush` and `aider` on documentation quality for exactly this pairing. Config at `~/.config/opencode/opencode.jsonc`.

The unit is installed and registered: `/etc/systemd/user/llama-server.service`, `systemctl --user is-enabled llama-server` → **`disabled`** as designed, starts on demand, model loads in ~3.5 s from page cache.

### Config shape

Provider uses `npm: "@ai-sdk/openai-compatible"` with `options.baseURL = http://127.0.0.1:8088/v1`. Two details matter:

- **The model key must equal the server's `--alias`** (`qwen3.8-27b`), because it is sent as the `model` field.
- **Per-model `options` is spread verbatim into the request body**, which is how `chat_template_kwargs: {"enable_thinking": false}` reaches llama-server. This is the mechanism that makes the model usable at all; `reasoning: false` alongside it only tells opencode not to expect a reasoning stream.

### Verified working

```
> build · qwen3.8-27b
→ Read buggy.py
`add` returns `a - b` (subtraction) instead of `a + b` (buggy.py:2).
```

Tool invocation, correct diagnosis, `file:line` citation, and a direct answer with no reasoning detour.

### The real cost is prefill, not decode

Server-side timings from that session:

| Request | Prompt tokens | Prefill | Decode |
|---|---|---|---|
| First | **7,561** | 48,274 ms @ 156.63 tok/s | 86 tok @ 9.02 tok/s |
| Second | **129** | 947 ms @ 136.23 tok/s | 28 tok @ 9.02 tok/s |

opencode's system prompt plus tool definitions is ~7.5K tokens, so **the first message of a session costs ~48 seconds before anything appears.** The second request processed only 129 prompt tokens, which proves llama-server's slot prompt cache reused the prefix — so this is a **one-off session-start tax, not a per-message one**.

That reframes the usability story: 9 tok/s decode is livable, and the thing a user actually notices is the cold-start wait. If it becomes annoying, the levers are keeping the server warm (it already is, being on-demand-but-persistent) and avoiding needless session restarts — not chasing decode throughput.

## Consequences and follow-ups

- **`~/dotfiles` gains** the user unit, the measured figures, and (conditionally) the modprobe.d override. The Z13 spec gains the EC power-cut result either way.
- **This repo's `CLAUDE.md`** gains a line under `blvckflow` once the endpoint is real, so future sessions know it exists.
- **Phase 2** picks a client against measured numbers. `opencode` is the current front-runner on documentation quality for exactly this pairing; `crush` and `aider` remain open.
- **Deferred, revisit only if wanted:** `ct-chat` integration via Tailscale-in-LXC, and a LiteLLM shim to put a local model behind `claude-personal`. Both were considered and consciously cut, not overlooked.

## Sources

- [Qwen3.8-27B release coverage](https://www.yottalabs.ai/post/qwen-3-8-27b-specs-hardware-requirements-how-to-run-2026)
- [orcarouter/Qwen3.8-27B-Uncensored-GGUF](https://huggingface.co/orcarouter/Qwen3.8-27B-Uncensored-GGUF)
- [Strix Halo local LLM guide — Vulkan vs ROCm, GTT sizing](https://github.com/hogeheer499-commits/strix-halo-guide)
- [Framework Strix Halo LLM setup — BIOS/kernel/GTT](https://github.com/Gygeek/Framework-strix-halo-llm-setup)
- [opencode with llama.cpp](https://www.mykolaaleksandrov.dev/posts/2026/07/blog-opencode-llamacpp/)
