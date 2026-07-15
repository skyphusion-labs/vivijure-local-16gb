# Proof gate: CogVideoX-5B-I2V i2v -- the honest VRAM floor (PASSED)

> **Historical frame-shape warning (2026-07-15):** this proof established fit and throughput with a
> synthetic gradient, not visual correctness. Later real-content diagnostics found that the 25-frame
> draft shape can complete but decode as latent tile noise; a 41-frame control failed the same way,
> while 49 frames rendered coherently. The shipped config now forces 49 frames for every tier. The
> 25-frame measurements below remain as historical VRAM/timing evidence, not a supported output shape.

Live benchmark of the REAL shipped container (`deploy/Dockerfile` runtime) on a secure RunPod pod,
2026-07-01. Both legs green; the honest consumer floor is a **16GB card**. This is the fidelity door's
counterpart to the LTX door's 12GB proof (`vivijure-local-12gb`) -- CogVideoX-5B is materially heavier,
so the floor is 16GB, not 12GB, and clips are minutes-per-clip, not sub-minute. Prove-then-name: the
tier NAME derives from the cap-sweep floor below, NOT the 4090's raw 24GB.

**Conditions.** Container `ghcr.io/skyphusion-labs/vivijure-local-16gb:proof-ssh` (the real runtime
+ an sshd overlay; crash-loop-safe idle boot). GPU: **NVIDIA GeForce RTX 4090, 24GB Ada** (the consumer
analog; 24GB headroom lets us measure the true peak AND cap-sweep to the floor in one pod). Engine:
`CogVideoXImageToVideoPipeline` + `THUDM/CogVideoX-5b-I2V` (bf16), `enable_model_cpu_offload()` + VAE
tiling + VAE slicing. Recipe: torch 2.4.1+cu124, torchvision 0.19.1, diffusers 0.32.2, transformers
4.46.3 (the LTX door's validated cu124 set, KEPT -- CogVideoXImageToVideoPipeline is present at 0.32.2).
Synthetic gradient keyframe, one motion prompt. Weights fetch 24s; cold model-load **29.1s**.

## Uncapped peak per tier (true peak on the 24GB card)

| tier | res | frames | steps | peak alloc | peak reserved | OOM | sec/clip (engine) | sec/clip (HTTP+R2) | sample |
|---|---|---|---|---|---|---|---|---|---|
| draft | 720x480 | 25 (~3.1s) | 30 | 12.35 GB | 13.65 GB | no | 97.8s | 99.5s | `sample_draft.mp4` |
| standard | 720x480 | 49 (~6.1s) | 40 | 13.57 GB | 15.64 GB | no | 243.0s | 251.3s | `sample_standard.mp4` |
| final | 720x480 | 49 (~6.1s) | 50 | 13.57 GB | **15.64 GB** | no | 299.2s | 295.3s | `sample_final.mp4` |

`peak alloc` / `peak reserved` = `torch.cuda.max_memory_allocated` / `max_memory_reserved`. The
49-frame tiers (standard/final) share the same peak (the VAE decode of 49 frames bounds it, flat across
step count -- so higher steps cost TIME, not VRAM, exactly like the LTX door's flat-peak finding).
Draft (25 frames) is lighter.

## The honest floor -- cap-sweep on the worst tier (final, 49f/50steps)

`VIVIJURE_MAX_VRAM_GB` pins `torch.cuda.set_per_process_memory_fraction`, emulating a smaller card's
PyTorch budget (the CUDA context, ~0.5-1GB, sits OUTSIDE the fraction, so a real card of the named size
has slightly MORE usable headroom than the raw cap):

| cap (emulates) | fits | peak reserved | result |
|---|---|---|---|
| 15 GB (a 16GB card) | **YES** | 14.83 GB | renders, no OOM |
| 13 GB (a 14GB card) | no | -- | **CUDA OOM** mid-denoise |
| 11 GB (a 12GB card) | no | -- | **CUDA OOM** mid-denoise |

**FLOOR = a 16GB card.** The full 49-frame tiers fit a ~15GB PyTorch budget and OOM at 13GB; a 12GB or
14GB card cannot run standard/final. `draft` (25 frames, ~12.35GB alloc) is lighter and may serve a
smaller card, but the honest full-experience floor is **16GB** (the proven value).

## Two independent legs, both green

1. **Direct engine (torch peaks).** `proof_measure.py` ran the shipped engine (`i2v_cogvideox.animate`)
   uncapped for the true peak, then cap-swept the final tier (15/13/11) for the floor. Numbers above.
2. **Real HTTP `/run` + R2 round-trip.** `proof_http_smoke.py` drove the LIVE server API
   (`POST /run` -> poll `/status` -> clip in R2) for all three tiers: every tier returned **COMPLETED**,
   keyframe in and finished clip out of the shared `vivijure` bucket, exactly as the `local-gpu` module
   would. Proof objects were namespaced (`_proof_cogvideox`) and deleted after (zero remnants).

## What this proves

- **FIT:** a 16GB card runs all three tiers (model-cpu-offload + VAE tiling/slicing bound the peak at
  ~13.6GB alloc / ~15.6GB reserved uncapped); 12GB and 14GB cards OOM on the 49-frame tiers.
- **SPEED (measured on RTX 4090 24GB):** draft ~1.6 min, standard ~4 min, final ~5 min per clip. **A
  3060 / 4070 / 4060 Ti-class 16GB card runs SLOWER than this 4090** -- a homelabber should expect
  longer per-clip times on a smaller card. This is the deliberate fidelity/speed trade against the LTX
  door (sub-minute, 12GB).
- **QUALITY:** real i2v clips of the correct shape were produced end to end via BOTH legs (ffprobe:
  720x480, 8 fps, 25/49 frames as configured) -- not placeholders. NOTE: these used a SYNTHETIC gradient
  keyframe, so visual motion quality is not asserted here; a real-content eyeball is a follow-up (same
  posture as the LTX proof).
- **THE CAP IS HONEST:** `VIVIJURE_MAX_VRAM_GB` fired exactly as designed and OOM'd the process when the
  budget was below the real need, which is how the floor was found.

## Cost + hygiene

One SECURE pod (RTX 4090 24GB, $0.69/hr), ~40 min GPU, removed via `runpodctl remove` after.
Per-identity R2 token (freshly minted least-privilege after an earlier admin-token leak was revoked);
proof objects deleted; the fresh trio was injected pod-side via env, never to a transcript. Prod
untouched throughout.

## Follow-up (known, out of scope of this proof)

- The shipped server reloads the pipeline per `/run` (`from_pretrained` inside `animate`), so each job
  pays the ~29s cold load; a persistent-pipeline cache is a clean follow-up (lowers latency, not peak).
- Visual quality on REAL keyframes (not the synthetic gradient) is a follow-up eyeball.
- CogVideoX1.5-5B-I2V (720p, up to 81 frames) is the Phase B higher tier, a separate milestone.
