# BLVCKFlow — Local LLM Inference Server — Design

**Status:** Draft 2026-08-23 — phase 1 (server only), not yet built
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
| Headroom for KV + compute | ~6.5 GiB after a 15.65 GiB model in the device-local heap | 32K context is plausible but is the thing to watch; see *Fallbacks* |
| `llama-cpp` build | `0.2.0-dev`, build **10566**, commit `bb4caa7540` | Postdates the 2026-05 merge |
| `qwen35` arch support | **present in `libllama.so`** (with `qwen35moe`, `qwen3vl`, `qwen3next`) | **No source build needed.** Verified by inspecting the arch table directly rather than by a 16.8 GB trial download |
| Device memory as llama.cpp sees it | `Vulkan0` — **34,045 MiB total, 33,009 MiB free** | It aggregates both heaps, so headroom is far better than the 22.16 GiB device-local figure alone implies; 32K context should be comfortable |
| `--flash-attn` on this build | tri-state, defaults to `auto` | Correctly omitted from the unit; hardcoding it would only remove llama.cpp's own judgement |
| `--jinja` on this build | **already default-enabled** | Kept explicit anyway, so a future default flip cannot silently break tool-calling |
| `orcarouter` repo access | **gated** — HTTP 401, `x-error-code: GatedRepo` | Requires an approved HF account; owner chose to authenticate rather than substitute |
| `douyamv` alternative | **empty repo** — no GGUF files despite its description | Ruled out; recorded so nobody re-evaluates it |

### Model facts

`Qwen3.8-27B` shipped 2026-08-14 under Apache 2.0, after this author's knowledge cutoff; every figure below was checked against the live model card rather than recalled.

| Fact | Value |
|---|---|
| Architecture | **Dense** 27B (not MoE), 64 layers, hidden dim 5120, hybrid linear + full attention |
| Modality | Native multimodal — image and video understanding |
| Context | 262K native, extensible to 1M via YaRN |
| BF16 size | 55.6 GB; official FP8 30.9 GB |
| Chosen build | `orcarouter/Qwen3.8-27B-Uncensored-GGUF`, **Q4_K_M, 16.8 GB** |
| Vision projector | separate `mmproj` f16, 0.9 GB — required only for image input |
| MTP head | embedded `nextn` block retained; usable for speculative decode |
| llama.cpp requirement | `qwen35` arch + MTP head merged **2026-05** — older builds will not load these files |

## Requirements

**In scope:**

- `llama.cpp` serving Q4_K_M over HTTP on `127.0.0.1` only.
- GPU offload genuinely on the iGPU via Vulkan, confirmed — not silently on CPU.
- An on-demand systemd **user** unit; explicitly *not* enabled at boot.
- Measured prefill and decode throughput, recorded in `~/dotfiles`.
- A sustained-load thermal test that doubles as the first real test of the EC power-cut mitigation.

**Out of scope:**

- Any `ct-chat` / Open WebUI integration.
- Any Anthropic-API shim, and any change to `claude-personal`.
- Client/agent configuration (`opencode`, `crush`, `aider`) — deferred to phase 2.
- ROCm. Vulkan/RADV is the reliable backend on Strix Halo; a tuned ROCm build wins roughly 3× only at very long context (128K–200K), which no phase-1 use case reaches.
- Vision input. The `mmproj` file is optional and left for later.
- Network exposure of any kind. The endpoint binds loopback.

## Architecture

```
opencode / crush / aider          (phase 2 — deferred)
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

Default GTT on recent `amdgpu` is approximately half of system RAM — around 29 GiB here — which already exceeds a 16.8 GB model plus its KV cache. **The likely outcome is that no memory configuration is needed at all.**

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

Q4_K_M (16.8 GB) into a user-owned path — `~/.local/share/models/qwen3.8-27b-uncensored/`. User-owned rather than `/var/lib` because this is a single-user workstation and the service runs as a user unit. 566 GiB free makes size irrelevant.

`IQ4_XS` (15.3 GB) is the documented fallback if the working set proves tight. `Q5_K_M` (18.2 GB) is the upgrade path if there is headroom to spare.

### 4. Service definition

`~/dotfiles/system/hosts/BLVCKFlow/etc/systemd/user/llama-server.service`, matching the host-specific pattern already used there for `notify-profile.service` and the `z13ctl` drop-in. Note this installs to **`/etc/systemd/user/`**, not `~/.config/systemd/user/` — that is what `system/install.sh` does with `hosts/<HOST>/etc/`, and following the repo's existing pattern beats matching this spec's first draft.

#### Fallbacks if the 32K context will not allocate

~6.5 GiB of device-local headroom remains after the weights. If the KV cache exceeds it, apply in this order, cheapest first:

1. **Quantise the KV cache** — `--cache-type-k q8_0 --cache-type-v q8_0`, roughly halving it for little quality cost.
2. **Reduce context** to 16K, which most real use never reaches.
3. **`--no-kv-offload`**, keeping the cache in host memory. Slower, but heap[0] has 11.08 GiB spare.

```
llama-server \
  --model ~/.local/share/models/qwen3.8-27b-uncensored/Q4_K_M.gguf \
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

Phase 1 therefore treats the benchmark as the first real test of that mitigation:

1. Cap PPT with `asusctl armoury set ppt_pl1_spl 60` (range is 28–80 W, default 60).
2. Hold sustained generation for at least 10 minutes.
3. Watch for the EC cut.

If the machine survives, that retires a known unknown in the fleet documentation. If it does not, that finding is more valuable than any throughput number, and the next step is to walk PPT down rather than to tune inference. Either result gets written back to the Z13 spec and to `~/dotfiles`.

Power profiles come from **asusd**, not power-profiles-daemon, which is masked and uninstalled on this host.

### 6. Performance expectations

Stated up front so that measurement can contradict it. Strix Halo's 256-bit LPDDR5X-8000 gives roughly **256 GB/s**. A dense 27B model at Q4_K_M has a ~17 GB working set that must be read once per token, putting the hard ceiling near **15 tok/s** and the realistic figure around **8–12 tok/s**.

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
