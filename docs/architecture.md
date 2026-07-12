# Architecture: the CogVideoX local-consumer render backend

> The CogVideoX (fidelity) local door. How this backend fits the Vivijure studio without changing the
> control plane, how it is the deliberate opposite of the RunPod datacenter backend, and how it is the
> fidelity sibling of the LTX (speed) local door.

## One studio, many honest doors

Vivijure is a host, not a monolith: the control plane owns project / storyboard / cast / bundle / the
render spine + a module registry, and every capability beyond that is an opt-in module behind a typed
hook contract. The `motion.backend` hook (keyframe + motion prompt -> shot clip, pick-one) is the seam.
The control plane invokes the hook; it does not know who answers. So multiple backends can serve the
same hook, and the USER picks the door:

```
                         vivijure control plane (UNCHANGED)
                                     |  motion.backend hook (pick one)
            +------------------------+------------------------+------------------------+
            |                                                 |                        |
   DATACENTER door                                    LOCAL door (LTX)         LOCAL door (CogVideoX, this repo)
   module: alibaba-wan / own-gpu                      module: local-gpu        module: local-gpu
            |  POST /run (i2v_clip)                            |  [same wire body]      |  [same wire body]
            v                                                  v                        v
   RunPod serverless                                  CF tunnel -> homelab     CF tunnel -> homelab
            |                                                  |                        |
   vivijure-backend                                   vivijure-local-12gb      THIS backend
   Wan 2.2 A14B MoE (H200 / B200)                     LTX-Video (speed)        CogVideoX-5B-I2V (fidelity)
```

All backends speak the IDENTICAL `i2v_clip` job contract (`{ action, project, shot_id, prompt,
keyframe_key?, config }`) and write the clip to the SAME shared R2 bucket, returning a pointer-only
result. That sameness IS the swappability: the same control plane, the same per-shot `buildI2vBody`,
drive any door. The only difference is the box behind the endpoint and the engine on it.

## Why "local-consumer" is genuinely different from "own-gpu"

The existing `own-gpu` module is "bring your own keys" -- but it still runs on a **RunPod endpoint the
user provisions**. It is own-keys, not own-silicon. This backend is the real homelab door: the work
happens on a consumer card the user already owns, reached over a Cloudflare tunnel. No rent, no cloud
GPU at all. That is the point of the local door: the deliberate opposite of the datacenter backend.

## LTX door vs CogVideoX door (the two local trade-offs)

Both are local doors on the SAME `local-gpu` module + the SAME contract; they differ only in the engine
and its trade-off. LTX-Video is few-step distilled -- fast, the lightest real i2v, proven at a 12GB
budget. CogVideoX-5B-I2V is the fidelity leader (strong first-frame identity, coherent motion, real text
control) at the cost of speed (full-step diffusion, minutes-per-clip class on a consumer card) and a
higher VRAM floor. A self-hoster picks by what they value and what their card + patience allow. See
`i2v-model-selection.md`.

## The two halves

| Half | Where | What |
|---|---|---|
| `local-gpu` module worker | `vivijure/modules/local-gpu/` (a CF Worker) | the contract bridge: serves `/module.json` `/invoke` `/poll` `/cancel`; submits `i2v_clip` to the box, polls `/status`, surfaces the clip_key. A near-clone of `own-gpu` + `/cancel`. |
| this backend | this repo (runs on the box) | the engine: a long-running server exposing a RunPod-compatible job API, an in-process async job registry, and the CogVideoX-5B-I2V engine scoped to a consumer card. |

## Why a RunPod-compatible job API on the local box

The datacenter backend is RunPod serverless: RunPod owns the `/run` + `/status` + `/cancel` lifecycle
and the queue. The local box has no such platform, so this backend provides that lifecycle itself
(`server.py` + `jobs.py`). Exposing the SAME endpoints + the SAME status envelope (IN_QUEUE /
IN_PROGRESS / COMPLETED / FAILED) means the `local-gpu` module's poll loop is a near-clone of
`own-gpu`'s -- minimum new surface, maximum reuse of the proven #141 grace-window discipline.

A consumer card runs ONE i2v job at a time (it cannot fit two pipelines), so the registry is a
single-worker serial queue: extra submits wait IN_QUEUE. The render itself does NOT run in the HTTP
process; it runs in a persistent worker subprocess (see the next section), so cancel is clean: a queued
job is simply dropped, and a running job is cancelled by terminating the worker and respawning a fresh
one, which reclaims all CUDA/VRAM cleanly (sub-second). A box restart loses the in-memory job; the
module grace window then treats the resulting 404 as a real loss and fails the shot honestly, rather
than polling a dead job forever.

## The render runs in a worker subprocess (off the HTTP GIL)

The HTTP server and the GPU render live in TWO processes, not one. The door serves HTTP from a
`ThreadingHTTPServer`; if the render ran in that same process, every CogVideoX sampler step would hold
the Python GIL in a single ~6.4s C-level torch call, so a `/status` poll landing in that window stalled
~6.4s and the caller (cloudflared -> the module fetch) timed out on a HEALTHY render (vivijure#719 /
16gb#77). The root fix (v0.4.0) moves the render into a persistent worker SUBPROCESS:

```
python3 -m vivijure_local.core.server         <- HTTP process: owns the job registry, answers /status
        |  newline-JSON IPC over stdin/stdout
python3 -m vivijure_local.core.render_worker   <- render process: model resident, runs the denoise
```

- `core/render_worker.py` is the child: it runs the SAME render body (fetch the keyframe from R2,
  animate, upload the clip, return the pointer) the door always ran, but in its own process. It keeps
  ONE model warm for its whole lifetime, so only its first job pays the load.
- `core/worker_client.py` is the parent (HTTP) side: it drives the worker over a small newline-JSON
  protocol and BLOCKS on a queue read while the worker renders. That blocking read RELEASES the GIL, so
  `/status` (a lock-free registry read in the HTTP process) stays sub-second at every percentile,
  regardless of where the sampler is in its step. Live-proven: `/status` p99 dropped from ~6166 ms to
  1.1 ms (`docs/proof/SUBPROCESS-S38.md`).

The subprocess boundary also makes two behaviors honest for free:

- **Cancel = terminate + respawn.** Killing the worker process reclaims all CUDA/VRAM cleanly, unlike an
  in-process cooperative cancel that would leave the pipeline resident.
- **A worker crash fails the job honestly.** Worker death (an OOM SIGKILL, a segfault, a cold-start
  import failure) is detected as an EOF from the worker and surfaces as a real job failure, never a hang.

Two stdout-hygiene rules keep the protocol clean: the worker reserves fd 1 for the JSON protocol and
redirects everything else (torch / tqdm / stray prints) to stderr BEFORE any heavy import, and the
parent logs-and-skips any stdout line it cannot decode. This shared core stays byte-identical across
both doors, so the LTX (12gb) door has the same process model.

## Module layout (mirrors vivijure-backend + the LTX door)

| Module | Role |
|---|---|
| `core/contract.py` | the i2v_clip job I/O + the shared R2 key conventions (identical to the datacenter backend's) |
| `config.py` | the honest tier->engine mapping (`draft`/`standard`/`final` -> CogVideoX configs; tiers differ by steps, not resolution) |
| `door.py` | the per-door identity + engine binding (`SERVICE`, `ENGINE`, `WEIGHTS_NOTE`, `animate`) -- the only seam `core` reads |
| `core/vram.py` | a pure, conservative VRAM budgeter: does a config fit the card, and which offload it needs |
| `i2v_cogvideox.py` | the CogVideoX engine: pure frame/dimension math (4k+1, /16) + the deferred-torch `animate` body |
| `core/jobs.py` | the in-process async job registry (the RunPod-lifecycle stand-in) |
| `core/server.py` | the RunPod-compatible HTTP server (pure `route()` + a stdlib http shell); its i2v run_fn drives the render worker over IPC instead of rendering in-process |
| `core/worker_client.py` | the parent-side IPC client: spawns + drives the render worker, blocks off-GIL while it renders, detects worker death |
| `core/render_worker.py` | the render worker subprocess: the deferred-torch render body, model kept warm, isolated from the HTTP GIL |
| `core/r2.py` | minimal shared-bucket object I/O (the one credential the backend holds) |

## Shared core with the sibling door (vivijure_local.core -- extracted)

This door and its sibling (`vivijure-local-12gb`, LTX-Video / `vivijure-local-16gb`, CogVideoX) are
~90% the same code. That shared surface now lives in the `vivijure_local.core` package, kept
BYTE-IDENTICAL across both repos: `r2`, `contract`, `jobs`, the pure `vram` math, the `announce` ready
banner, and the RunPod-compatible `server` scaffold. Each door keeps ONLY its own `config.py` tier
table, its engine module (`i2v_ltx` / `i2v_cogvideox`), and a tiny `door.py` identity + binding
(`SERVICE`, `ENGINE`, `WEIGHTS_NOTE`, `animate`) that the core reads through the stable `..door` /
`..config` seam. That is the honest per-model part; everything else is shared.

DONE (S6, extraction): the `core/` package replaced the duplicated top-level modules. The two copies are
proven identical in each PR by `diff -r` of the two `core/` trees (only `door.py` + `config.py` + the
engine module differ between the repos, as they should). A change to any core file MUST be mirrored to
the sibling door in the same change and the byte-identical invariant re-checked.

Promoting `core` to a TRUE single source (its own repo, or a git submodule both doors vendor) is now a
trivial later lift -- the package is already self-contained and seam-clean. It was deliberately NOT done
now: a new repo would trigger the full new-repo governance standard mid-sprint, and the vendored-copy
form (like the studio's `src/modules/types.ts` contract) already closes the drift with a mechanical
`diff` gate. This closes out the earlier new-repo-vs-vendored question: vendored, for now.

## The CPU / GPU split (testing)

Exactly like vivijure-backend + the LTX door: everything CPU-testable is pure and unit-tested (config,
vram, frame math, the job registry, the server router, the run_fn with a fake store) -- no torch, no
GPU, no spend. The torch/diffusers generation body is deferred-imported and validated on the card (the
spend gate, see `live-benchmark-plan.md`). The body raises a clear error rather than faking a clip if
the runtime is absent: a producer stage never ships fake output.

## What stays in the control plane (unchanged)

Nothing in `vivijure/src/` changes, and nothing in the `local-gpu` module changes either -- this door
is just a different container the self-hoster points `LOCAL_BACKEND_URL` at. The studio discovers
`local-gpu` from a `MODULE_LOCAL_GPU` service binding, reads its manifest, indexes it under
`motion.backend`, and renders its stage from its `config_schema` -- the same path every other module
uses. Wiring the binding + the tunnel is infra (Strummer's lane); this repo + the module are the
backend + the contract surface.
