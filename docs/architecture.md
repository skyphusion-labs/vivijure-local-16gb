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
single-worker serial queue: extra submits wait IN_QUEUE. Cancel is best-effort + cooperative -- a
queued job is dropped; a running job is flagged so the engine's progress callback aborts between
denoise steps (a torch step is not externally interruptible). A box restart loses the in-memory job;
the module's grace window then treats the resulting 404 as a real loss and fails the shot honestly,
rather than polling a dead job forever.

## Module layout (mirrors vivijure-backend + the LTX door)

| Module | Role |
|---|---|
| `contract.py` | the i2v_clip job I/O + the shared R2 key conventions (identical to the datacenter backend's) |
| `config.py` | the honest tier->engine mapping (`draft`/`standard`/`final` -> CogVideoX configs; tiers differ by steps, not resolution) |
| `vram.py` | a pure, conservative VRAM budgeter: does a config fit the card, and which offload it needs |
| `i2v_cogvideox.py` | the CogVideoX engine: pure frame/dimension math (4k+1, /16) + the deferred-torch `animate` body |
| `jobs.py` | the in-process async job registry (the RunPod-lifecycle stand-in) |
| `server.py` | the RunPod-compatible HTTP server (pure `route()` + a stdlib http shell) + the i2v run_fn |
| `r2.py` | minimal shared-bucket object I/O (the one credential the backend holds) |

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
