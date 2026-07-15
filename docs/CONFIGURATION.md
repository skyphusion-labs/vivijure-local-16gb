# Every knob, in plain English

This is the full list of every setting in `vivijure-local-16gb`. You should never have to open the
compose file or the source code to learn what a setting does. If a knob exists, it is on this page,
with what it is, why it is there, and an example.

There are three groups:

1. **The `.env` settings you fill in** -- the ones you touch. All optional except the R2 keys.
2. **Built-in settings** -- set for you inside `docker-compose.yml`. You do not need to change these,
   but they are listed so nothing is hidden.
3. **Per-clip settings** -- sent by the Studio with each render request, not set by you.

Plus the **ports**, the **volumes**, and the **quality tiers** at the end.

---

## 1. The `.env` settings you fill in

Copy `.env.example` to `.env` and fill these in. Only the R2 keys are required.

### `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`
- **What it is:** your Cloudflare R2 storage keys.
- **Why:** this backend shares one storage bucket with your Studio. It reads the starting picture (the
  "keyframe") from that bucket and writes the finished clip back to it. That is the only way bytes move,
  so these keys are the one credential the backend needs.
- **Required?** Yes, for real renders. Without them the container starts, then tells you in plain words
  which values are missing.
- **Where to get them:** Cloudflare dashboard -> R2 -> Manage R2 API Tokens. Scope the token to your
  bucket only (read the keyframes, write the clips). Do not reuse an admin key.
- **Example:**
  ```
  R2_ACCOUNT_ID=1a2b3c4d5e6f7890abcdef1234567890
  R2_ACCESS_KEY_ID=6b1f...
  R2_SECRET_ACCESS_KEY=9e2c...
  ```

### `R2_BUCKET`
- **What it is:** the name of that shared bucket.
- **Why:** it must match the bucket your Studio uses. Almost everyone leaves it at the default.
- **Required?** No.
- **Default:** `vivijure`
- **Example:** `R2_BUCKET=vivijure`

### `R2_S3_ENDPOINT`
- **What it is:** the storage endpoint the backend talks to.
- **Why:** by default the backend talks to Cloudflare R2, and it works the address out from your
  `R2_ACCOUNT_ID`. If you self-host your storage on MinIO (or another S3-compatible server), point the
  backend at it here instead.
- **Required?** No.
- **Default:** unset -- Cloudflare R2 (the derived `https://<account-id>.r2.cloudflarestorage.com`).
- **Example:** `R2_S3_ENDPOINT=https://minio.local:9000` (self-host); leave unset for Cloudflare R2.
- **Note:** a custom endpoint automatically uses path-style addressing (`host/bucket`), which MinIO
  requires; the Cloudflare R2 default is unchanged.

### `LOCAL_BACKEND_TOKEN`
- **What it is:** a secret password that every render request must carry.
- **Why:** your backend is reachable over a public web address (the tunnel, below). The password stops a
  stranger from using your graphics card. The render endpoint refuses to work without it.
- **Required?** No. If you leave it blank, the container makes a strong random one for you at startup and
  prints it in the ready banner. Set your own only if you want the same password to survive a restart.
- **Default:** auto-generated (a fresh random one each restart).
- **Example:** `LOCAL_BACKEND_TOKEN=` (blank, recommended for a first run) or a fixed value from
  `openssl rand -hex 32`.

### `TUNNEL_TOKEN`
- **What it is:** a Cloudflare "named tunnel" token, for a web address that never changes.
- **Why:** by default the backend gets a free throwaway web address (a TryCloudflare quick tunnel) that
  changes every time you restart. That is perfect for a first try. If you run this backend all the time,
  a named tunnel gives you one fixed address you can paste into the Studio once and forget.
- **Required?** No.
- **Default:** blank, which means a zero-setup quick tunnel.
- **Example:** `TUNNEL_TOKEN=` (blank, recommended for a first run) or your named-tunnel token.

### `VIVIJURE_MAX_VRAM_GB`
- **What it is:** a cap, in gigabytes, on how much graphics memory (VRAM) this backend is allowed to use.
  Applied via `torch.cuda.set_per_process_memory_fraction` in the render worker **before** any model
  loads, so it is a hard ceiling (not just a reporting number).
- **Why:** CogVideoX-5B-I2V with model CPU offload fits a 16GB card, but PyTorch's allocator can
  **reserve** more than the live tensor footprint when free VRAM exists on a bigger card. The shipped
  default caps that so homelabbers on real 16GB silicon do not OOM silently.
- **Required?** No.
- **Default:** **15.5** (in `.env.example` and `docker-compose.yml`). Leaves ~0.5GB outside the fraction
  for the CUDA driver context on a nominal 16GB card. All three 49-frame tiers complete under this cap
  (live proof on RTX 4000 Ada, 2026-07-15; see `docs/proof/RESULTS.md`).
- **Bigger cards:** raise the value in `.env` (e.g. `20` on a 24GB card) or set a number >= your card
  size to use the whole card. You can also lower it when sharing the GPU with other workloads.

### `VIVIJURE_OFFLOAD`
- **What it is:** how the render trades speed for graphics memory (VRAM). Three modes:
  `none` (keep the whole model resident on the GPU: fastest, but needs a big card), `model` (evict the
  big text encoder to system RAM after it runs, keeping on the GPU only what the denoise loop needs: the
  consumer-card default), `sequential` (page piece-by-piece: slowest, smallest footprint, the low-VRAM
  fallback).
- **Why (measured, S38):** the whole model resident needs **more than 28GB** of VRAM (peak 28436 MiB,
  measured on a 48GB card). On a card with that headroom, `none` is faster than the default `model` by a
  near-constant ~17-18s per clip -- that is the one-time cost `model` pays to evict the ~11GB T5-XXL text
  encoder, not a per-step penalty. In percent it is ~14% on `draft` and ~4-5% on the 49-frame tiers
  (biggest on short clips; real but modest).
- **Required?** No.
- **Default:** blank, which keeps each quality tier its own safe setting (`model` on this door). Nothing
  changes unless you set this.
- **Applies to:** every tier at once (draft, standard, final).
- **Set `none` ONLY on a >28GB card (in practice 32GB+, i.e. 48GB-class).** On 24GB or below it does not
  fit and OOMs on every tier (proven on a real RTX 4090 24GB, and on a 20GiB card in S37; see
  [proof/OFFLOAD-S38.md](proof/OFFLOAD-S38.md)). On any card this door targets (24GB and down), leave it
  blank.
- **Bad value:** the backend refuses to start and tells you the valid modes, rather than quietly using
  the default. Fix the value (or unset it) and start again.
---

## 2. Built-in settings (set for you in `docker-compose.yml`)

You do not set these. They live in the compose file so the pieces find each other. Listed here so
nothing about the running system is a mystery.

### `HOST`
- **What it is:** the network address the render server listens on inside its container.
- **Default:** `0.0.0.0` (all addresses inside the private container network).
- **Why fixed:** the tunnel container reaches the server over the private compose network; this must
  stay open inside that network. It is not exposed to your real network (see Ports).

### `PORT`
- **What it is:** the port the render server listens on inside the container.
- **Default:** `8000`.
- **Why fixed:** the tunnel and the healthcheck both point at `8000`. Changing it means changing them too.

### `HF_HOME`
- **What it is:** where the model files (the CogVideoX-5B-I2V weights) are cached inside the container.
- **Default:** `/models/hf`.
- **Why fixed:** it points at the `vivijure_models` volume (below) so the ~22GB of weights are downloaded
  once (during your FIRST render) and reused after, instead of re-downloading each time.

### `ANNOUNCE_BACKEND`
- **What it is:** the internal address the "ready" banner service checks for health before it prints your
  connect details.
- **Default:** `http://vivijure-local-16gb:8000`.
- **Why fixed:** it is the container name plus the internal port; the banner waits for this to answer
  before it tells you the backend is ready.

---

## 3. Per-clip settings (sent by the Studio, not by you)

Each render the Studio sends carries a small `config`. You do not edit these; the Studio and the
`local-gpu` module fill them in. They are documented so you know exactly what the backend accepts and
how it protects your card. Any value is clamped to what the chosen tier can honestly fit -- a request can
narrow a tier but never push the card past its limit.

| Setting | What it is | Default | Notes |
|---|---|---|---|
| `quality` | Which tier to render: `draft`, `standard`, or `final`. | `standard` | Picks the inference steps from the tier table below. An unknown value falls back to `standard`. |
| `num_frames` | Legacy shared-door field. | **fixed at 49** | Ignored by this door. CogVideoX-5B-I2V silently produced latent tile noise at off-grid 25/41-frame counts, so every tier uses its native 49-frame grid. |
| `fps` | Playback speed, frames per second. | **fixed at 8** | CogVideoX-5B-I2V generates its frames FOR 8 fps. A higher requested value is ignored so the clip is not sped up; the model's cadence cannot be changed by a knob. |
| `seed` | The random seed, for repeatable output. | `-1` (random) | Same seed + same inputs = the same clip. |
| `flow_shift` | A sampling knob that trades motion smoothness against sharpness. | `5.0` | Advanced; the Studio rarely changes it. |
| `negative_prompt` | Text describing what you do NOT want in the clip. | empty | Optional. |
| `width` / `height` | Clip size in pixels. | 720x480 (the model's fixed grid) | CogVideoX-5B-I2V is a fixed-grid model; a request is clamped to the grid, never widened. |

---

## Ports

| Port | Where | Open to your network? | Why |
|---|---|---|---|
| `8000` | inside the container only (`expose`, not `ports`) | **No** | The render server listens here. Nothing is published to your computer's real network. The only way in is the Cloudflare tunnel, which reaches `8000` over the private compose network. This is deliberate: a graphics-card render endpoint left open to the internet is an invitation to run up your electricity bill. |

If you ever DO want a local port for testing on the same machine, add a `ports:` entry mapped to
`127.0.0.1` only. The default ships with none, which is the safe choice.

## Volumes (saved data)

| Volume | Mounted at | Holds | Why it persists |
|---|---|---|---|
| `vivijure_models` | `/models` | the downloaded CogVideoX-5B-I2V model weights (~22GB, pulled during your first render) | So the big download happens once, not again. |
| `vivijure_runtime` | `/shared` | the generated token and the tunnel's log line | So the "ready" banner can read the token and the tunnel web address and print them for you. |

To start completely fresh (re-download models, new token), remove these with
`docker compose down -v`.

## The quality tiers

The Studio's tier names map to CogVideoX settings a 16GB card can honestly deliver. CogVideoX-5B-I2V is
a fixed-grid model: every tier renders 720x480 at 49 frames @ 8 fps and differs only by inference
**steps**. Off-grid 25/41-frame diagnostics completed without an error but decoded as latent tile noise,
so shorter frame counts are no longer accepted as a duration control. `final` is the card's honest
ceiling, not datacenter quality. All tiers use model-CPU-offload plus VAE tiling/slicing.

| Tier | Resolution | Frames | Steps | Peak VRAM (alloc) | sec/clip (RTX 4090) | sec/clip (RTX 4000 Ada) |
|---|---|---|---|---|---|---|
| `draft` | 720x480 | 49 (~6.1s) | 30 | 13.57 GB | ~98s (~1.6 min) | ~511s (~8.5 min) |
| `standard` | 720x480 | 49 (~6.1s) | 40 | 13.57 GB | ~243s (~4 min) | ~682s (~11 min) |
| `final` | 720x480 | 49 (~6.1s) | 50 | 13.57 GB | ~299s (~5 min) | ~850s (~14 min, estimated) |

Peak VRAM is flat across all three tiers at 49 frames (the VAE decode sets the ceiling). The shipped
**15.5GB** `VIVIJURE_MAX_VRAM_GB` default keeps real 16GB cards from OOMing.

---

*Every setting in this repo is on this page. If you find one that is not, that is a documentation bug --
please open an issue.*
