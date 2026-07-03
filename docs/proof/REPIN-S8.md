# S8 ML re-pin proof -- vivijure-local-16gb (CogVideoX-5B-I2V)

Ratified on real SECURE-cloud silicon (RunPod, RTX 4090, driver 580.126.20, CUDA 12.4) on 2026-07-03.
This supersedes the torch-2.4.1 container proof (`RESULTS.md`) for the ML stack; the non-ML render
behaviour (tiers, the 8fps CogVideoX pin, 720x480 grid, VRAM fit) is unchanged.

## Validated pin set
`torch==2.5.1+cu124`, `torchvision==0.20.1+cu124`, `diffusers==0.38.0`, `transformers==4.57.6`,
`accelerate==1.14.0`, `safetensors==0.8.0`.

## Method
A fresh cu124 venv replicating `deploy/Dockerfile` (torch/torchvision from the cu124 wheel index, then
`requirements.txt`). `CogVideoXImageToVideoPipeline` loaded on the card and an i2v render completed; the
full server entrypoint was then exercised (health, the token 401 / unknown-id 404 / bad-input 400
hardening, and the R2 keyframe-in + clip-out round-trip).

## Result (draft tier)
- Pipeline loads; render COLD 127.3s / WARM 95.7s (the process pipeline cache holds on the new stack),
  peak VRAM 14.66GB (fits the 16GB budget with model-cpu-offload + VAE tiling).
- ffprobe: h264 720x480, 8/1 fps, 25 frames, 3.125s (confirms the CogVideoX 8fps pin + native grid).
- Hardening: 401 / 401 / 404 / 400 all pass. R2 clip round-trip: 406534B `video/mp4`, re-fetched + ffprobe sane.

## RESOLVE-THEN-PIN note (why transformers 4.57.6, not 5.x)
Unconstrained, `diffusers` 0.38.0 pulls `transformers` 5.12.1, which FAILS the T5 tokenizer load on-card
(`transformers` 5.x requires a new `tiktoken` dependency, and `diffusers` 0.38 is not built against the
5.x major line). Constraining `transformers<5` resolves 4.57.6 (the latest 4.x), which loads cleanly and
renders. The pin is therefore 4.57.6, captured from this proof -- not the bare-latest 5.x.
