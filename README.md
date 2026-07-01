# vivijure-local-cogvideox

> **WORKING NAME / PRE-PROOF.** This is the CogVideoX local door, scaffolded from the shipped LTX door
> ([vivijure-local-12gb](https://github.com/skyphusion-labs/vivijure-local-12gb)). The public repo name
> will encode the **proven** VRAM tier once the card benchmark measures it (prove-then-name, exactly
> like LTX). The VRAM numbers below are targets/estimates, NOT measured -- see
> [docs/live-benchmark-plan.md](docs/live-benchmark-plan.md).

The **local-consumer** render backend for Vivijure, fidelity variant: image-to-video on a **single
consumer GPU** running **CogVideoX-5B-I2V** in your own homelab. The sibling of the LTX door (which
trades fidelity for speed) and the deliberate opposite of
[vivijure-backend](https://github.com/skyphusion-labs/vivijure-backend) (the RunPod datacenter engine,
Wan 2.2 on H200/B200).

**One studio, many honest doors.** The studio's `motion.backend` hook makes the clip engine pluggable.
The control plane is unchanged; the user picks the door: rent datacenter GPU, or run it on silicon they
already own (LTX for speed, CogVideoX for fidelity). This backend is a local door -- no rent, no cloud
GPU at all, reached over a Cloudflare tunnel that terminates at the box.

```
control plane --> local-gpu module (CF Worker) --/run--> tunnel --> THIS backend (CogVideoX-5B-I2V)
```

## Run it on your own box (one command)

```sh
cp .env.example .env        # your R2 creds (+ optional LOCAL_BACKEND_TOKEN)
docker compose up -d        # first start caches the CogVideoX weights, then serves :8000
curl localhost:8000/health  # {"ok":true,"engine":"cogvideox",...}
```

Then expose `:8000` over a Cloudflare tunnel and point your studio's `local-gpu` module at it. The full
homelabber walkthrough (prereqs, tunnel, honest trade-offs, troubleshooting) is
**[docs/HOMELABBER.md](docs/HOMELABBER.md)**; the studio-side wiring is
**[docs/INTEGRATION.md](docs/INTEGRATION.md)**.

Needs an NVIDIA GPU + the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
The minimum card is pinned by the benchmark (Milestone 2); CogVideoX-5B needs CPU offload on any
consumer card, so expect a larger VRAM floor (and slower clips) than the LTX door.

## Configuration (`.env`)

Copy `.env.example` to `.env` and fill it in. Every setting is an environment variable:

| Var | Required | Default | What it does |
|---|---|---|---|
| `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | yes | -- | The one credential: the shared-R2 key (read the keyframe, write the clip). Scope it to the bucket. |
| `R2_BUCKET` | no | `vivijure` | The shared bucket name. |
| `LOCAL_BACKEND_TOKEN` | no | auto-generated | The bearer token every i2v request must carry (the tunnel is public). Blank => a strong one is generated and printed in the banner; set it for a stable token across restarts. |
| `TUNNEL_TOKEN` | no | quick tunnel | A Cloudflare named-tunnel token for a STABLE hostname. Blank => a zero-config TryCloudflare quick tunnel (URL changes each restart). |
| `VIVIJURE_MAX_VRAM_GB` | no | full card | Cap the VRAM vivijure claims, in GB, when you share the card with other workloads. The backend pins torch to that fraction of the card at startup. Blank (or a value >= your card's size) => use the whole card. |

## What it runs

**CogVideoX-5B-I2V** (`THUDM/CogVideoX-5b-I2V`), the true-i2v **fidelity leader** on a consumer card
(strong first-frame identity, coherent motion, real text control), chosen here as the deliberate
opposite trade-off to the LTX door's speed (the full comparison is
[docs/i2v-model-selection.md](docs/i2v-model-selection.md)). CogVideoX-5B-I2V is a **fixed-grid** model:
it renders 720x480 at up to 49 frames @ 8 fps (~6s), and degrades off that grid, so the three quality
tiers differ by inference **steps** (and, for `draft`, a shorter clip) -- NOT resolution. `final` is
the card's honest ceiling, not datacenter parity.

| Tier | Resolution | Frames | Steps | Offload | Peak VRAM | sec/clip |
|---|---|---|---|---|---|---|
| `draft` | 720x480 | 25 | 30 | model-CPU-offload + VAE tiling/slicing | TBD (Milestone 2) | TBD |
| `standard` | 720x480 | 49 (~6s) | 40 | model-CPU-offload + VAE tiling/slicing | TBD | TBD |
| `final` | 720x480 | 49 (~6s) | 50 | model-CPU-offload + VAE tiling/slicing | TBD | TBD |

> **NOT YET PROVEN.** Unlike the LTX door (proven at a 12GB budget), CogVideoX-5B's real VRAM floor and
> per-clip wall-clock are pinned on real silicon in Milestone 2 (the spend gate,
> [docs/live-benchmark-plan.md](docs/live-benchmark-plan.md)). CogVideoX-5B is much heavier than LTX (a
> 5B DiT transformer + a large T5-XXL text encoder), so it needs CPU offload on any consumer card;
> model-CPU-offload + VAE tiling/slicing is the scaffold default, and sequential offload is the
> low-VRAM (but slow) fallback the budgeter down-shifts to if a smaller card needs it. Community reports
> put a full-step CogVideoX-5B clip in the minutes-per-clip range on a consumer card -- the fidelity/
> speed trade against LTX is deliberate.

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
(peak VRAM / OOM), speed (sec/clip), and a real sample clip per tier, then writes a report. It is
**ready to fire** the instant the hardware is chosen; it does NOT run without a GPU (the spend gate).
See [docs/live-benchmark-plan.md](docs/live-benchmark-plan.md) for the costed plan.

## Security boundary

One credential: the shared-R2 key (read the keyframe, write the clip). Input is control-plane-trusted
(the module only reaches the box through the studio's service binding + your tunnel). The
`LOCAL_BACKEND_TOKEN` is REQUIRED on every i2v request (the tunnel is public; an unconfigured token
makes the i2v endpoint refuse to serve). The backend holds no studio secrets and no submitter identity.

## License

**AGPL-3.0-only.** A labor of love, given freely: use it, learn from it, self-host it, build your own
creative visions on it. Run it as a network service and the AGPL has you share your changes back, so it
stays a commons. It is not for sale, and not to be resold as a SaaS.
