# Proof results -- PENDING Milestone 2

> This backend follows **prove-then-name**: no VRAM tier or speed is claimed until it is measured on
> real silicon. This file is the destination for those measured numbers; it is **intentionally empty of
> results** until Milestone 2 (the on-card benchmark) runs.

Milestone 1 (the scaffold) is CPU-only and needed zero GPU spend: the pure logic (config clamping,
VRAM/frame math, the job registry, server routing) is unit-tested and green, and the torch/diffusers
generation body is deferred-imported (it raises rather than faking a clip when the GPU runtime is
absent). None of that measures the model on a card.

## What Milestone 2 will populate here

Per quality tier (draft / standard / final), from `scripts/benchmark.py` on a SECURE RunPod pod (never
community -- a hard rule):

- **FIT**: measured peak VRAM (`torch.cuda.max_memory_allocated`) and whether it OOMs, at each candidate
  card size; the offload mode that actually fits (model-CPU-offload vs sequential); the resulting honest
  minimum card (the number the repo name will encode).
- **SPEED**: real wall-clock seconds per clip.
- **QUALITY**: a real sample `.mp4` per tier to eyeball.

Until then, the tier table in `README.md` / `docs/HOMELABBER.md` and the coefficients in
`src/vivijure_local/vram.py` are conservative scaffold estimates, labeled as such. See
`docs/live-benchmark-plan.md` for the costed plan.
