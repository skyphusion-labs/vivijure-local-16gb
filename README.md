# vivijure-local-16gb

The **local-consumer** render backend for Vivijure, fidelity variant: image-to-video on a **single
16GB consumer GPU** running **CogVideoX-5B-I2V** in your own homelab. The higher-fidelity sibling of the
[LTX door](https://github.com/skyphusion-labs/vivijure-local-12gb) (12GB, lean + fast) and the
deliberate opposite of [vivijure-backend](https://github.com/skyphusion-labs/vivijure-backend) (the
RunPod datacenter engine, Wan 2.2 on H200/B200).

> **16GB floor, PROVEN.** The VRAM floor was measured on real silicon (RTX 4090 24GB, cap-swept down):
> the full 49-frame tiers fit a 16GB card and OOM at 14GB and 12GB. Numbers below are measured, not
> estimated. Full proof: [docs/proof/RESULTS.md](docs/proof/RESULTS.md).

**One studio, many honest doors.** The studio's `motion.backend` hook makes the clip engine pluggable.
The control plane is unchanged; the user picks the door: rent datacenter GPU, or run it on silicon they
already own. **Pick this 16GB door for fidelity; pick the 12GB LTX door for speed** (see the trade
below).

```
control plane --> local-gpu module (CF Worker) --/run--> tunnel --> THIS backend (CogVideoX-5B-I2V, 16GB)
```

## Quickstart (your first run)

You need **one** thing before you start: your Vivijure studio's **Cloudflare R2 credentials**. This
backend shares that bucket -- it reads the keyframe and writes the finished clip there. Everything else
(the tunnel, the access token) is automatic.

**1. Put your R2 credentials in `.env`:**

```sh
cp .env.example .env
# edit .env and set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY
# (R2_BUCKET defaults to "vivijure")
```

Where to get them: Cloudflare dashboard -> R2 -> Manage R2 API Tokens (scope the token to your bucket).

**2. Start it:**

```sh
docker compose up
```

First run downloads the CogVideoX weights (~22GB, once). Then the `ready` service prints a banner with
your **Backend URL + token**, copy-paste ready.

**3. Paste that URL + token** into your Vivijure studio's "Local (your GPU)" door, pick it, and render.
A real clip comes back from your own card. That is the whole setup -- no tunnel to configure, no account.

> Forgot the R2 creds? The logs tell you exactly which values to set and to run `docker compose up`
> again -- a plain message, not a stack trace.

Needs an NVIDIA GPU with **16GB+ VRAM** + the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
CogVideoX-5B needs CPU offload on any consumer card; a 12GB or 14GB card OOMs on the full 49-frame tiers
(measured). The full walkthrough (tunnel, trade-offs, troubleshooting) is
**[docs/HOMELABBER.md](docs/HOMELABBER.md)**; studio-side wiring is
**[docs/INTEGRATION.md](docs/INTEGRATION.md)**.

## Configuration (`.env`)

Copy `.env.example` to `.env` and fill it in. Every setting is an environment variable:

| Var | Required | Default | What it does |
|---|---|---|---|
| `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | yes | -- | The one credential: the shared-R2 key (read the keyframe, write the clip). Scope it to the bucket. |
| `R2_BUCKET` | no | `vivijure` | The shared bucket name. |
| `LOCAL_BACKEND_TOKEN` | no | auto-generated | The bearer token every i2v request must carry (the tunnel is public). Blank => a strong one is generated and printed in the banner; set it for a stable token across restarts. |
| `TUNNEL_TOKEN` | no | quick tunnel | A Cloudflare named-tunnel token for a STABLE hostname. Blank => a zero-config TryCloudflare quick tunnel (URL changes each restart). |
| `VIVIJURE_MAX_VRAM_GB` | no | full card | Cap the VRAM vivijure claims, in GB, when you share the card with other workloads. The backend pins torch to that fraction of the card at startup. Blank (or a value >= your card's size) => use the whole card. On a 16GB card, leave it blank -- the full 49-frame tiers need the whole card. |

## What it runs

**CogVideoX-5B-I2V** (`THUDM/CogVideoX-5b-I2V`), the true-i2v **fidelity leader** on a consumer card
(strong first-frame identity, coherent motion, real text control), chosen here as the deliberate
opposite trade-off to the LTX door's speed (the full comparison is
[docs/i2v-model-selection.md](docs/i2v-model-selection.md)). CogVideoX-5B-I2V is a **fixed-grid** model:
it renders 720x480 at up to 49 frames @ 8 fps (~6s), and degrades off that grid, so the three quality
tiers differ by inference **steps** (and, for `draft`, a shorter clip) -- NOT resolution. `final` is
the card's honest ceiling, not datacenter parity.

Measured on the real shipped container (RTX 4090 24GB Ada, `enable_model_cpu_offload()` + VAE
tiling/slicing, bf16; cold model-load 29s). Peak VRAM below is `max_memory_allocated` (the true need):

| Tier | Resolution | Frames | Steps | Peak VRAM (alloc) | sec/clip |
|---|---|---|---|---|---|
| `draft` | 720x480 | 25 (~3.1s) | 30 | 12.35 GB | ~98s (~1.6 min) |
| `standard` | 720x480 | 49 (~6.1s) | 40 | 13.57 GB | ~243s (~4 min) |
| `final` | 720x480 | 49 (~6.1s) | 50 | 13.57 GB | ~299s (~5 min) |

Peak is **flat** across `standard`/`final` (the 49-frame VAE decode bounds it), so higher steps cost
**time, not VRAM**. All tiers use model-CPU-offload + VAE tiling/slicing.

> **SPEED CAVEAT.** Those sec/clip figures were measured on an **RTX 4090 24GB**. A 3060 / 4070 /
> 4060 Ti-class **16GB** card runs **slower** -- expect longer per-clip times on a smaller card. The
> 16GB floor is about FIT (does it run without OOM), proven by cap-sweep; speed scales with your card.

### The trade vs the 12GB LTX door

| | This door (16GB, CogVideoX) | LTX door (12GB) |
|---|---|---|
| Strength | **Fidelity** (best local i2v quality) | **Speed** (few-step, sub-minute class) |
| Engine | CogVideoX-5B-I2V (5B DiT + T5-XXL) | LTX-Video (light, distilled) |
| VRAM floor | 16GB (proven) | 12GB (proven) |
| Per-clip | minutes | sub-minute to ~2 min |

Heavier model, higher fidelity, bigger card, slower clips -- the honest opposite of the lean/fast LTX
door. Run whichever fits your card and your patience.

## The job API (RunPod-compatible)

A long-running server (`src/vivijure_local/server.py`) the `local-gpu` module talks to exactly as
`own-gpu` talks to RunPod:

```
POST /run          { "input": { action: "i2v_clip", project, shot_id, prompt, keyframe_key?, config } } -> { "id" }
GET  /status/<id>  -> { id, status: IN_QUEUE|IN_PROGRESS|COMPLETED|FAILED, output?, error? }
POST /cancel/<id>  -> { ok: true }   (idempotent)
GET  /health       -> { ok: true, ... }
POST /run { "selftest": true } -> a no-GPU transport probe
```

The server owns an in-process serial job registry (a consumer card runs one i2v job at a time), the
RunPod-lifecycle stand-in for a box with no serverless platform. This contract is byte-identical to the
LTX door, so a self-hoster swaps the container without touching the studio.

## Develop (CPU: no GPU, no model weights)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                       # the full CPU suite (config, vram, frame math, jobs, server routing)
python -m py_compile src/vivijure_local/*.py
```

The pure logic is CPU-tested and green; the torch/diffusers generation body is deferred-imported and
validated on the card. The body raises a clear error rather than faking output if the GPU runtime is
absent -- a producer stage never ships a fake clip.

## The benchmark (proof gate)

`scripts/benchmark.py` runs the CogVideoX i2v engine across the three tiers on the card, capturing fit
(peak VRAM / OOM), speed (sec/clip), and a real sample clip per tier, then writes a report. It is how
the 16GB floor was proven ([docs/proof/RESULTS.md](docs/proof/RESULTS.md)); it does NOT run without a
GPU. See [docs/live-benchmark-plan.md](docs/live-benchmark-plan.md) for the method.

## Security boundary

One credential: the shared-R2 key (read the keyframe, write the clip). Input is control-plane-trusted
(the module only reaches the box through the studio's service binding + your tunnel). The
`LOCAL_BACKEND_TOKEN` is REQUIRED on every i2v request (the tunnel is public; an unconfigured token
makes the i2v endpoint refuse to serve). The backend holds no studio secrets and no submitter identity.

## License

**AGPL-3.0-only.** A labor of love, given freely: use it, learn from it, self-host it, build your own
creative visions on it. Run it as a network service and the AGPL has you share your changes back, so it
stays a commons. It is not for sale, and not to be resold as a SaaS.
