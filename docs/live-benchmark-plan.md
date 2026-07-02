# Live benchmark plan -- Milestone 2 (EXECUTED 2026-07-01; results in `proof/RESULTS.md`)

> Spend gate. Everything else in this repo (research, scaffold, the dry fit analysis, the CPU test
> suite) needed ZERO GPU spend and is Milestone 1. THIS is Milestone 2: the one step that needs real
> silicon to finalize -- the VRAM floor CogVideoX-5B-I2V actually fits, the offload mode required, and
> the real per-clip wall-clock. **NOT executed here. Costed and flagged for approval before any paid
> GPU is spun up.**

## Why a live run is needed at all

The model-selection (CogVideoX-5B-I2V) and the tier ladder are settled on paper from the model card +
community reports. What desk research CANNOT give us:

1. The TRUE peak VRAM of each tier on the card (the `vram.py` coefficients are coarse, conservative
   first-order estimates for a 5B DiT + T5-XXL encoder + 3D VAE). A benchmark replaces them with
   measured peaks, and pins the honest minimum card (prove-then-name, exactly like LTX's 12GB).
2. Whether **model-CPU-offload + VAE tiling/slicing** actually fits a target consumer card, or whether
   the low-VRAM path needs **sequential offload** (much slower) or quantization. This decides the tier.
3. Real per-clip wall-clock -- the number that decides whether the CogVideoX door is pleasant or
   painful, and how it honestly compares to the LTX door's speed.
4. The exact diffusers pipeline kwargs for the deployed version (the `i2v_cogvideox.animate` body is a
   scaffold; the CogVideoX conditioning argument names are pinned against the real package here).

## The card

CogVideoX-5B is heavier than LTX, so the honest floor is unknown until measured (it will likely be
higher than LTX's 12GB, or require sequential offload to reach a smaller card).

- **RunPod SECURE cloud ONLY -- NEVER a community pod (a hard rule).** Community pods cap GPU-pod disk
  at ~20GB, which cannot hold the image + CogVideoX weights (5B + T5-XXL); secure gives configurable
  disk. Provision a secure pod with a card in the target consumer range and enough disk for the weights
  cache.
- Run the harness below on the pod; capture peak VRAM + sec/clip + a real sample clip per tier.

## The harness

`scripts/benchmark.py` is scripted + ready to fire. On the pod, after `pip install -r requirements.txt`:

```sh
python scripts/benchmark.py --out results/                 # all three tiers, synthetic keyframe
python scripts/benchmark.py --keyframe kf.png --out results/  # a real keyframe
python scripts/benchmark.py --tiers draft,standard --out results/  # subset
```

It drives the engine (`i2v_cogvideox.animate`) directly (no R2, no job server) so it measures the MODEL
on the CARD, nothing else: peak VRAM (`torch.cuda.max_memory_allocated`), OOM at the provisional ceiling,
wall-clock per clip, and a real sample `.mp4` per tier to eyeball. It writes
`results/benchmark-<host>.md` + `.json` + the clips.

## After the run

1. Record measured peaks + sec/clip + sample clips in `docs/proof/RESULTS.md` and the running
   `docs/RUN-LOG.md`.
2. Update `src/vivijure_local/config.py` (tier offload/frames if needed) + `vram.py` (real coefficients
   + the proven `FLOOR_VRAM_GB`) from the measured numbers.
3. Name the tier + the public repo from the proven floor (prove-then-name), exactly like LTX 12GB.
4. Only THEN flip the repo public + un-hold the studio-side name references.
