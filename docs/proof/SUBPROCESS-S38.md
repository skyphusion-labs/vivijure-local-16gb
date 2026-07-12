# S38 subprocess root-fix proof: /status stays sub-second during a render (CogVideoX-5B-I2V door)

The defect (vivijure#719, this door = 16gb#77): the door served HTTP from a `ThreadingHTTPServer` whose
`/status` handler thread shared the GIL with the in-process render. Each CogVideoX sampler step holds the
GIL in a single ~6.4s C-level torch call, so a `/status` poll landing in that window stalled ~6.4s and
the caller (cloudflared -> the local-gpu module fetch) timed it out on a HEALTHY render. The root fix
(#77, merged in #78): isolate the render in a persistent worker SUBPROCESS so the HTTP process never
shares the render's GIL. This is the live proof, measured 2026-07-12.

**Card:** propagandhi, NVIDIA RTX 4000 SFF Ada Generation, 20 GiB (the standing-door box; the SAME card
the S37 stall was measured on, so the before/after is apples-to-apples).
**Image:** the subprocess build from merged `main` (be47283), rebuilt locally on the box for the window
(byte-identical to the code that ships as `:0.4.0`). Engine `CogVideoXImageToVideoPipeline` +
`THUDM/CogVideoX-5b-I2V`, VAE tiling + slicing, bf16, `VIVIJURE_OFFLOAD` unset (= `model`, the shipped
default that fits this card).
**Method:** the standing door was recreated onto the subprocess build (`docker compose up -d --no-deps`,
one health-check blip), then a STANDARD `i2v_clip` was submitted against a synthetic 720x480 keyframe in
the shared R2 bucket. A probe hit the REAL `GET /status/<id>` route over HTTP (the exact call the studio
+ the local-gpu module make) at a 0.2s cadence for the whole render, recording each response latency.
This measures the actual contract, stronger than the S37 GIL-re-acquire proxy.

## Result: /status latency DURING the render

Standard render, job `4afa093b`, IN_PROGRESS wall time **578.7s**, cadence 0.2s.
Samples: **2877 total (2876 during IN_PROGRESS)** -- spanning all ~40 sampler steps, so a 6.4s-class
stall (which the S37 probe saw at ~1 poll per ~17s step) could not hide in the tail.

| window | median | p90 | p99 | max |
|---|---|---|---|---|
| IN_PROGRESS-only (2876 polls) | 0.9 ms | 1.0 ms | 1.1 ms | 3.7 ms |
| all polls (2877) | 0.9 ms | 1.0 ms | 1.1 ms | 3.8 ms |

## Before / after on the SAME card

| door build | /status p99 (during render) | /status max |
|---|---|---|
| S37 in-process (shipped v0.3.1) | ~6166 ms | ~6395 ms |
| S38 subprocess (this build) | **1.1 ms** | **3.7 ms** |

That is roughly a **5600x** improvement at p99. The stall is gone, not reduced: p99 is now
indistinguishable from an idle-door `/status` (a lock-free registry read), because the render no longer
runs in this process at all.

## Isolation evidence (the mechanism)

During the render, the container process tree showed the split directly:

```
PID   PPID  RSS         CMD
  1      0  ~21 MB      python3 -m vivijure_local.core.server        <- HTTP process (answers /status)
158      1  ~11 GB      python3 -m vivijure_local.core.render_worker <- the render (model resident)
```

The GIL-holding CogVideoX denoise runs entirely in PID 158; the HTTP handler thread in PID 1 shares
nothing with it, so a `/status` poll is served immediately regardless of where the sampler is in its
step. GPU was pegged at **16228 MiB / 100% util, sustained** for the whole 578.7s (the same 16228 MiB
flat-VRAM figure S37 measured, confirming this is inherent per-step compute, not offload paging).

## Completion evidence (DoD: a full render completes with progress advancing)

- Final status: **COMPLETED** (the probe observed IN_QUEUE -> IN_PROGRESS -> COMPLETED).
- Clip written to R2: `renders/s38verify/clips/probe_i2v.mp4`, **468864 bytes**, `video/mp4`.
- Render advanced normally: 578.7s of sustained 100% GPU util for a standard-tier render (warm weights;
  consistent with the standard tier, faster than the S37 cold-load baseline of ~708s because the weights
  were resident in the `/models` cache volume).

## DoD verdict

| criterion | result |
|---|---|
| /status p99 sub-second at every percentile DURING render | **PASS** (1.1 ms; max 3.7 ms) |
| a full render completing | **PASS** (COMPLETED; 468864-byte clip in R2) |
| progress advancing | **PASS** (GPU 100% sustained; worker isolated; completed in 578.7s) |

## S37 GIL-probe history (why only process isolation fixes this)

The S37 evidence (banked in 16gb#77) ruled out every in-process fix before S38:
- `sys.setswitchinterval` 10x lower was a NULL result (p99 6166 ms vs 6212 ms) -- the hold is a single C
  call, not preemptible Python.
- VRAM was flat at 16228 MiB through the render -- the hold is inherent per-step GPU compute, not
  model-offload paging, so no targeted paging fix existed.
- A per-step `callback_on_step_end` yield could not help -- the callback fires only at the step boundary,
  AFTER the ~6.4s hold.
- Distribution was p99-only (~1 poll per step of ~84 at 0.2s landed in the hold; the rest were 0 ms), so
  p90 was already sub-second and only p99/max were the defect.

Only moving the render out of the HTTP process removes the shared GIL, which the table above proves.

## Reproducibility

- Box: propagandhi (RTX 4000 SFF Ada, 20 GiB), the standing 16gb door stack (`docker compose`, watchdog,
  cloudflared, `/models` weights cache volume).
- Build: `git pull` to merged `main`, `docker compose build vivijure-local-16gb` (only the source layer
  rebuilds; torch/diffusers layers are cached), then `docker compose up -d --no-deps vivijure-local-16gb`.
- Keyframe: a synthetic 720x480 PNG at `renders/s38verify/keyframes/probe.png` in the shared R2 bucket.
- Probe: submit `{"input":{"action":"i2v_clip","project":"s38verify","shot_id":"probe","config":
  {"quality":"standard"}, ...}}`, then poll `GET /status/<id>` at 0.2s recording each HTTP latency; report
  median/p90/p99/max over the IN_PROGRESS window with sample count + duration.
- No GPU rental spend: run on the existing standing-door card; the door was kept on the subprocess build
  after the window (it IS merged, reviewed, and now live-proven) and flipped to the published GHCR
  `:0.4.0` once CI built it.
