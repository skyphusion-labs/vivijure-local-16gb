# RUN-LOG

The running build/validation log for the CogVideoX local door. Newest first.

## 2026-07-01 -- Milestone 1: scaffold (CPU-only, $0, no GPU)

CogVideoX-5B-I2V local door scaffolded from the shipped LTX door (`vivijure-local-12gb`), swapping ONLY
the model-load + inference layer; the model-agnostic parts (HTTP server, R2 store, job/status/cancel
registry, `VIVIJURE_MAX_VRAM_GB` cap, docker-compose homelabber stack) are unchanged in shape. Working
name `vivijure-local-16gb` (prove-then-name: the public name encodes the proven VRAM tier after
Milestone 2). No GitHub repo yet (held for lead's go). Package stays `vivijure_local` (internal name).

What changed vs the LTX scaffold:
- New engine `src/vivijure_local/i2v_cogvideox.py`: `CogVideoXImageToVideoPipeline`, frame math on the
  CogVideoX grid (num_frames 4k+1 capped at 49, dims /16, 8 fps default, guidance 6.0), model-CPU-offload
  + VAE tiling AND slicing. Deferred torch/diffusers import (CPU-importable); raises rather than faking a
  clip. Replaces `i2v_ltx.py` (removed).
- `config.py`: tiers map to CogVideoX configs. FIXED 720x480 grid across all tiers (the model degrades
  off-grid), so tiers differ by STEPS (draft 30 / standard 40 / final 50) and draft by a shorter clip
  (25 frames vs 49). Model `THUDM/CogVideoX-5b-I2V`.
- `vram.py`: CogVideoX-5B footprint table (fully-resident ~24GB -> never NONE-offload on a consumer
  card), latent-volume divisor on the CogVideoX compression (/16 spatial, /4 temporal). `FLOOR_VRAM_GB`
  is PROVISIONAL (16GB working target) until Milestone 2 measures it.
- `server.py`: engine string `cogvideox`, run_fn wired to `i2v_cogvideox.animate`. Contract, R2, jobs,
  auth (token-required i2v behind the public tunnel), and the RunPod-compatible envelope are UNCHANGED.
- docker-compose / Dockerfile / .env.example: service + image + container renamed to
  `vivijure-local-16gb`; cloudflared + ready/announce services unchanged. Weights-cache comments
  updated (CogVideoX weights are larger than LTX).
- Docs: README / CLAUDE.md / architecture.md / HOMELABBER.md / INTEGRATION.md / i2v-model-selection.md
  reframed for the CogVideoX (fidelity) door; all LTX 12GB PROOF numbers removed (they are the LTX
  door's, not this one's) and replaced with honest "not-yet-proven / Milestone 2" framing.

Dependency decision: **KEPT the LTX door's validated cu124 recipe** (diffusers==0.32.2,
transformers==4.46.3, torch==2.4.1+cu124, torchvision==0.19.1). Verified `CogVideoXImageToVideoPipeline`
is present in the diffusers 0.32.2 public API (it landed ~0.30.3), so no bump is needed -- a shared
cu124 base with the LTX door. `.github/dependabot.yml` ignores the pinned pair (mirrors the LTX door).

Verification (CPU, no GPU, no weights): `python -m py_compile src/vivijure_local/*.py scripts/benchmark.py`
green; `pytest -q` green (44 tests: config clamping, VRAM/frame math, the serial job registry, server
routing, the run_fn with a fake store). The torch/diffusers body is deferred and validated on the card
in Milestone 2.

NEXT (Milestone 2, spend-gated, awaiting go): the on-card benchmark on a SECURE RunPod pod (never
community) to pin the VRAM floor + offload mode + per-clip speed, then prove-then-name. See
`docs/live-benchmark-plan.md`.
