# Image-to-video model selection for the CogVideoX (fidelity) local door

> Deliverable: why THIS local door runs **CogVideoX-5B-I2V**, and how it relates to the LTX door. Desk
> research from model cards / official repos / diffusers docs / reputable community reports -- NO rented
> hardware. Numbers marked **[community]** are community reports, not vendor-official. The real VRAM
> floor + per-clip speed are pinned by the on-card benchmark (`live-benchmark-plan.md`, Milestone 2).
> Sources at the bottom.

## The point of a second local door

The Vivijure `motion.backend` hook is engine-agnostic: the control plane is unchanged and the user
picks the door. The first local door (`vivijure-local-12gb`) runs **LTX-Video**, chosen for **speed**
(few-step distilled, sub-minute class) and a proven 12GB budget. That is the right pick for fast
iteration on a modest card, but it trades away fidelity.

This door is the honest opposite trade on the SAME hook + SAME contract: run the **fidelity leader** for
true image-to-video on a consumer card, for the user who values how the clip looks over how fast it
renders. Among consumer-runnable i2v models, that leader is **CogVideoX-5B-I2V**.

## The comparison (single consumer card)

| Axis | CogVideoX-5B-I2V (**this door**) | LTX-Video (the speed door) | SVD / SVD-XT | AnimateDiff |
|---|---|---|---|---|
| **True i2v quality** | **Best** -- strong first-frame identity + coherent motion + text prompt | Good, fast-improving; lighter fidelity | Good motion, **no text control**, weak faces/large-motion drift | Weakest *true* i2v (SparseCtrl is approximate) |
| **Speed** | **Slowest** -- full-step; minutes/clip on a consumer card [community] | **Fastest** -- few-step distilled | Moderate | Very fast (Lightning 1-8 step) |
| **Consumer fit** | Needs CPU offload (5B DiT + T5-XXL encoder); model-offload fits ~16GB, sequential far lower but slow [community] | Excellent -- lightest real i2v | Good (<10GB with offload) | Excellent (most headroom) |
| **License (free self-host)** | Custom CogVideoX license: register + **1M visits/mo cap** (2B is Apache-2.0) | LTX Open Weights, free commercial < $10M | Stability Community License, free < $1M | Code Apache-2.0; output bound by base checkpoint |
| **diffusers maturity** | First-class `CogVideoXImageToVideoPipeline` (present at 0.32.2, verified) | First-class `LTXImageToVideoPipeline` | First-class `StableVideoDiffusionPipeline` | i2v only via SparseCtrl/IPAdapter |
| **Res / length (consumer)** | Fixed 720x480, 49 frames, 8 fps (~6s) | ~512-768p, up to 257 frames | 576x1024, 14/25 frames (~4s) | 512x512 16f (~2s) |

## Recommendation for this door: CogVideoX-5B-I2V

CogVideoX-5B-I2V is the quality leader for true i2v that still runs on a consumer card:

- **Fidelity.** The best first-frame identity + coherent motion + real text control of the consumer
  options. That is the entire reason this door exists alongside LTX.
- **Consumer fit (with offload).** CogVideoX-5B is a 5B DiT transformer plus a large T5-XXL text
  encoder and a 3D VAE, far heavier than LTX -- so CPU offload is not optional. `enable_model_cpu_offload()`
  + VAE tiling/slicing is the scaffold default (community reports fit a 16GB card); sequential offload
  drops the floor much lower (~5GB [community]) at a heavy speed cost. The real floor is measured in
  Milestone 2 -- prove-then-name, exactly like LTX.
- **Ecosystem.** First-class `CogVideoXImageToVideoPipeline` in diffusers, present at the pinned
  0.32.2 (verified), so the engine is a thin wrapper and this door KEEPS the LTX door's validated cu124
  recipe -- no dependency bump.

**The honest trade-off:** CogVideoX-5B is slow (full-step diffusion; community reports minutes-per-clip
on a consumer card) and license-gated (register + a 1M-visits/month cap; the Apache-2.0 CogVideoX-2B is
the license-clean lower-fidelity alternative). That slowness is the deliberate opposite of LTX's speed;
the user picks the door that fits their card + patience + quality bar.

### Phase B (FUTURE, not this milestone)

**CogVideoX1.5-5B-I2V** as a higher tier (720p, up to 81 frames) is the natural next step once the 5B-I2V
floor is proven. It may require a newer diffusers; if so, that is a separate, justified pin bump gated on
staying torch-2.4-compatible, evaluated then -- NOT in this scaffold.

## The honest tier mapping

The control plane owns the tier vocabulary (`draft` / `standard` / `final`) and injects the chosen tier;
an enum value not in the module's schema is silently dropped (vivijure #124). So this door keeps the
same three names. CogVideoX-5B-I2V is FIXED-GRID (720x480, up to 49 frames), so -- unlike the LTX door,
which scales resolution -- these tiers differ by inference STEPS (and, for draft, a shorter clip), NOT
resolution. Mapping lives in `src/vivijure_local/config.py`; the peak VRAM + speed are PENDING the card
benchmark.

| Tier | Model | Steps | Resolution | Max frames | Offload | Intent |
|---|---|---|---|---|---|---|
| `draft` | CogVideoX-5B-I2V | 30 | 720x480 | 25 | model CPU offload + VAE tiling/slicing | fast preview |
| `standard` | CogVideoX-5B-I2V | 40 | 720x480 | 49 (~6s @ 8fps) | model CPU offload + VAE tiling/slicing | the comfortable middle |
| `final` | CogVideoX-5B-I2V | 50 | 720x480 | 49 | model CPU offload + VAE tiling/slicing | the model's honest ceiling |

The pure VRAM budgeter (`src/vivijure_local/vram.py`) estimates each tier's peak against a provisional
floor and picks the weakest offload that fits, conservatively (it would rather page more than OOM the
user's only GPU). The live benchmark replaces the coarse coefficients with measured peaks.

## What still needs real silicon

The exact VRAM floor (and whether model-offload suffices or the low-VRAM path needs sequential offload /
quantization), plus the real per-clip wall-clock, can only be confirmed on the card. That is the one
step behind the spend gate; it is NOT executed here. The costed plan is in
[`live-benchmark-plan.md`](./live-benchmark-plan.md).

## Sources

- CogVideoX-5B-I2V card (memory table, specs) + LICENSE: https://huggingface.co/zai-org/CogVideoX-5b-I2V ,
  https://huggingface.co/zai-org/CogVideoX-5b-I2V/blob/main/LICENSE ; the model is also published as
  `THUDM/CogVideoX-5b-I2V`. CogVideoX-2B (Apache-2.0): https://huggingface.co/zai-org/CogVideoX-2b ;
  diffusers CogVideoX pipeline + offload/quantization guidance:
  https://huggingface.co/docs/diffusers/main/en/api/pipelines/cogvideox
- CogVideoX consumer-GPU timing + VRAM [community]: https://huggingface.co/zai-org/CogVideoX-5b/discussions/7
- LTX-Video (the speed door), for contrast: https://huggingface.co/Lightricks/LTX-Video ,
  https://huggingface.co/docs/diffusers/main/en/api/pipelines/ltx_video , license: https://ltx.io/model/license
- SVD-XT card + Stability Community License: https://huggingface.co/stabilityai/stable-video-diffusion-img2vid-xt ,
  https://stability.ai/license
- AnimateDiff repo + diffusers pipelines: https://github.com/guoyww/AnimateDiff ,
  https://github.com/huggingface/diffusers/blob/main/docs/source/en/api/pipelines/animatediff.md
