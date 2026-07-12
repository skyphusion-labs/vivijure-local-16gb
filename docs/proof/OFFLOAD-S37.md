# S37 offload-knob proof: VIVIJURE_OFFLOAD on a 20GiB card (CogVideoX-5B-I2V)

The honest answer to "can this card render faster if we let it use all its VRAM." Measured on the
standing-door box, 2026-07-12.

**Card:** NVIDIA RTX 4000 SFF Ada Generation, 20 GiB (19.55 GiB usable after the CUDA context).
**Image:** `ghcr.io/skyphusion-labs/vivijure-local-16gb:0.3.0` (shipped runtime + the new
`VIVIJURE_OFFLOAD` knob; the v0.3.1 engine is byte-identical). Engine
`CogVideoXImageToVideoPipeline` + `THUDM/CogVideoX-5b-I2V`, VAE tiling + slicing. Synthetic gradient
keyframe, one motion prompt, WARM (weights preloaded; the first draft render is discarded so no
cold-load time pollutes the numbers). Driver `scripts/benchmark.py` (drives `i2v_cogvideox.animate`
directly, no HTTP/R2); resident VRAM sampled from `nvidia-smi memory.used`.

## Why the knob exists

S36 proved the VRAM cap (`VIVIJURE_MAX_VRAM_GB`) is NOT the perf binding on this card: uncapped ~=
capped across every tier, because every tier hardcoded `MODEL_CPU_OFFLOAD` and the per-step CPU paging
(not the VRAM ceiling) sets the wallclock. `VIVIJURE_OFFLOAD=none` (whole model resident) is the only
setting that removes that paging. This bench answers whether `none` actually fits a 20GiB card.

## Result: none does NOT fit a 20GiB card; model is the fastest that fits

| tier | `offload=none` (resident) | `offload=model` (default) | peak alloc (model) |
|---|---|---|---|
| draft    | CUDA OOM | OK, 252.7 s/clip | 13.26 GB |
| standard | CUDA OOM | OK, 708.0 s/clip | 14.58 GB |
| final    | CUDA OOM | OK, 876.7 s/clip | 14.58 GB |

- Under `none`, resident VRAM climbed to **20004 MiB** (the whole card) and then CUDA OOM on EVERY
  tier. Root cause: CogVideoX-5B fully resident needs ~24GB because the T5-XXL text encoder (~11GB)
  stays on the card the whole denoise. `MODEL_CPU_OFFLOAD` is exactly what evicts the encoder after it
  computes the prompt embedding, dropping peak resident to **16236 MiB (~15.86 GiB)**, which fits
  (consistent with the RTX 4090 proof in `docs/proof/RESULTS.md`: 49-frame tiers peak ~15.6 GB).
- `sequential` offload only pages harder (slower), so it is never faster than `model`.
- The `model` wallclocks match the S36 warm baseline (260 / 718 / 889 s) within noise, confirming the
  v0.3.x engine did not regress.

## Conclusion

On a 20GiB card, `model` (the default) is already the fastest offload that fits CogVideoX-5B; there is
no faster-fitting setting, so the knob yields NO speedup here. It is a real win only for a 24GB+ operator
who can hold the model resident. The standing door runs on the default (`VIVIJURE_OFFLOAD` unset =
`model`), uncapped. No OOM-crashing config ships as a default recommendation (the honesty rule).
