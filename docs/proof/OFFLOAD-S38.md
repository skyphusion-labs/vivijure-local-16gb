# S38 offload-knob proof: VIVIJURE_OFFLOAD on 24GB and 48GB cards (CogVideoX-5B-I2V)

The hard fact Conrad asked for: on a card with real headroom, how much does `VIVIJURE_OFFLOAD=none`
(whole model resident) actually buy, and does it even FIT on exactly 24GB? S37 answered the 20GiB case
(none OOMs every tier); this is the 24GB and 48GB answer. Measured 2026-07-12.

**Image:** `ghcr.io/skyphusion-labs/vivijure-local-16gb:0.3.1` (the shipped runtime; the
`VIVIJURE_OFFLOAD` knob is live in it). Engine `CogVideoXImageToVideoPipeline` + `THUDM/CogVideoX-5b-I2V`,
VAE tiling + slicing, bf16. Synthetic gradient keyframe, one motion prompt.
**Cards (both SECURE cloud, prod account):**
- **NVIDIA A40, 46068 MiB (~45.0 GiB)** -- the 48GB-class headroom card. $0.44/hr, CA-MTL-1.
- **NVIDIA GeForce RTX 4090, 24564 MiB (23.52 GiB usable after the CUDA context)** -- the 24GB
  fits-or-not card. ~$0.69/hr.

**Method:** driver `scripts/benchmark.py` drives `i2v_cogvideox.animate` directly (no HTTP/R2, no job
server), so the numbers are the MODEL on the CARD and nothing else. Per offload mode the tier schedule
is `draft,draft,standard,final`: the pipeline is process-cached per `(model, offload)`, so the FIRST
draft is a cold load into VRAM (kept below as the labeled COLD-START row) and every later tier is WARM
(render only). Resident VRAM is sampled at 1 Hz from `nvidia-smi memory.used`; `peak alloc` is
`torch.cuda.max_memory_allocated`. Weights were pre-downloaded once (untimed) so no ~20GB HF download
pollutes any render number.

## Result 1 (48GB, A40): none FITS and is MODESTLY faster

| tier | mode | cold s/clip | warm s/clip | none speedup (warm) | peak alloc | peak resident |
|---|---|---|---|---|---|---|
| draft    | model | 134.4 | 133.0 | -- | 13.26 GB | 16590 MiB |
| draft    | none  | 122.0 | 114.4 | 14.0% (1.16x), -18.6s | 25.27 GB | 28436 MiB |
| standard | model | -- | 348.4 | -- | 14.57 GB | 16590 MiB |
| standard | none  | -- | 331.5 | 4.9% (1.05x), -16.9s | 25.33 GB | 28436 MiB |
| final    | model | -- | 429.5 | -- | 14.58 GB | 16590 MiB |
| final    | none  | -- | 411.5 | 4.2% (1.04x), -18.0s | 25.33 GB | 28436 MiB |

(peak resident is the per-mode whole-run high-water mark from the sampler; both modes fit the 45 GiB card.)

**The saving is a near-constant ~17-18s/clip at every tier, not a per-step win.** That is the mechanism:
`enable_model_cpu_offload` keeps the transformer resident for the whole denoise loop (no per-step
paging), and its only real cost is a ONE-TIME eviction of the ~11GB T5-XXL text encoder after it computes
the prompt embedding (plus the VAE shuffle at decode). `none` skips that one-time cost, so it saves a
fixed ~17-18s no matter how many steps the tier runs. Consequence for operators: the PERCENT benefit is
biggest on short clips (14% on `draft`) and shrinks as renders lengthen (4% on `final`), but the ABSOLUTE
~17-18s/clip is real and compounds over a many-shot film.

## Result 2 (24GB, RTX 4090): none does NOT fit; model is the only setting that runs

| tier | mode | warm s/clip | peak alloc | peak resident | outcome |
|---|---|---|---|---|---|
| draft    | model | 95.3  | 13.26 GB | 16500 MiB | OK (cold draft 105.9s) |
| standard | model | 239.8 | 14.58 GB | 16500 MiB | OK |
| final    | model | 290.2 | 14.58 GB | 16500 MiB | OK |
| draft    | none  | --    | -- | 24074 MiB | **CUDA OOM** |
| standard | none  | --    | -- | 24074 MiB | **CUDA OOM** |
| final    | none  | --    | -- | 24074 MiB | **CUDA OOM** |

Under `none`, resident VRAM climbed to **24074 MiB (the whole card)** and then CUDA OOM on EVERY tier.
Verbatim (draft):

> CUDA out of memory. Tried to allocate 338.00 MiB. GPU 0 has a total capacity of 23.52 GiB of which
> 123.75 MiB is free. Including non-PyTorch memory, this process has 23.39 GiB memory in use. Of the
> allocated memory 21.81 GiB is allocated by PyTorch, and 1.12 GiB is reserved by PyTorch but unallocated.

`model` on the same card evicts the T5-XXL encoder and peaks at **16500 MiB (~16.1 GiB)**, which fits
with room to spare (consistent with the RTX 4090 proof in `docs/proof/RESULTS.md`).

## The measured fit threshold: none needs a >28GB card

The A40 run (which has the headroom to load the model fully) puts a hard number on it: the
whole-model-resident high-water mark is **28436 MiB (~27.8 GiB)**. So `none` needs a card with **more
than 28GB** of VRAM. A 24GB card is ~4GB short and OOMs on every tier (proven above on a real 4090); a
20GiB card OOMs too (S37). In rental practice that means **32GB+**, and the real market step is the
**48GB-class** card. This supersedes the earlier "20GB+ is enough" guidance, which was an estimate, not
a measurement.

## Cross-card observation (operator gold): model-mode renters should prefer the Ada 4090

At the SAME `model` setting the 24GB RTX 4090 (Ada) beats the 48GB A40 (Ampere) at every tier:

| tier | RTX 4090 model | A40 model |
|---|---|---|
| draft (warm)    | 95.3s  | 133.0s |
| standard (warm) | 239.8s | 348.4s |
| final (warm)    | 290.2s | 429.5s |

You do not need the 48GB card for speed in the default `model` mode; you need
it only to unlock `none`, and `none` only buys ~4-14%. For most operators the cheaper, faster Ada 24GB
card in `model` mode is the better price/performance, and it is what the shipped default already targets.

## Conclusion / what the knob buys per card class

- **<= 24GB (incl. 20GiB and 24GB):** `none` only OOMs. Never set it. `model` (the default) is the only
  offload that fits, and it is not the bottleneck (the 4090 in `model` is the fastest card we measured).
- **>28GB (32GB+, practically 48GB-class):** `none` FITS and is faster, but MODESTLY: ~14% on `draft`,
  ~4-5% on the 49-frame tiers, a near-constant ~17-18s/clip saving. Safe to set if you own the headroom
  and render many clips; do not expect a step change.
- The shipped default (`VIVIJURE_OFFLOAD` unset = `model`, uncapped) remains the right default for every
  card 24GB and below, which is every card this door targets. No OOM-crashing config ships as a default
  (the honesty rule).

## Reproducibility (the pod recipe)

- **Provider:** RunPod **SECURE** cloud only (community caps pod disk ~20GB; the baked runtime + weights
  will not fit), prod account (`t9wcvlxh8rc5la`).
- **Image:** the shipped `ghcr.io/skyphusion-labs/vivijure-local-16gb:0.3.1` (public on GHCR, no
  registry auth). The render image ships **no sshd**, so the pod was created via the RunPod REST v1 API
  (`POST /v1/pods`) with a `dockerEntrypoint` override that installs + starts `openssh-server`, injects
  `PUBLIC_KEY`, and holds the container (`sleep infinity`) -- this leaves the door server UNstarted, so
  the GPU is clean for the benchmark. The `VIVIJURE_OFFLOAD` code path is byte-identical to shipped.
- **Container disk:** 80GB on the A40 host; **40GB** on the 4090 host (24GB-class hosts expose less
  writable disk; the weights are ~21GB, so 40GB is enough and 50GB+ was rejected with "does not have the
  resources to deploy").
- **Weights:** `THUDM/CogVideoX-5b-I2V` (public, ~21GB) pre-downloaded once with `huggingface_hub`, then
  `HF_HUB_OFFLINE=1` for every timed run.
- **Runs:** `VIVIJURE_OFFLOAD=<mode> python3 scripts/benchmark.py --tiers draft,draft,standard,final`
  per mode (`model` then `none`), with a 1 Hz `nvidia-smi --query-gpu=memory.used` sampler alongside.
  Pods were torn down the moment numbers were captured.

Total bench spend: ~\$0.67 (A40 ~40 min, RTX 4090 ~28 min, plus a scrapped no-shell A40 attempt ~8 min).
