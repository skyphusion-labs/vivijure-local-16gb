# CLAUDE.md

Guidance for Claude Code (and the crew) working in this repo.

## What this is

**A local-consumer render backend for Vivijure: the CogVideoX motion door.** A long-running server that
does image-to-video on a SINGLE consumer GPU running in your own homelab, with **CogVideoX-5B-I2V**. The
fidelity sibling of the LTX door ([vivijure-local-12gb](https://github.com/skyphusion-labs/vivijure-local-12gb),
which trades fidelity for speed) and the deliberate opposite of
[vivijure-backend](https://github.com/skyphusion-labs/vivijure-backend) (the RunPod datacenter engine,
Wan 2.2 on H200/B200): no rent, no cloud GPU, reached over a Cloudflare tunnel that terminates at the box.

**One studio, many doors.** The studio's `motion.backend` hook makes the clip engine pluggable; the
control plane is unchanged and the user picks the door (rent datacenter GPU, or run it on silicon they
already own -- LTX for speed, CogVideoX for fidelity). **Public repo** (`skyphusion-labs/vivijure-local-16gb`).
**Production-ready** as of v1.0.0 (July 2026). AGPL-3.0-only.

```
control plane --> local-gpu module (CF Worker) --/run--> CF tunnel --> THIS backend (CogVideoX-5B-I2V)
```

## How it relates to the other doors

Same studio hook (`motion.backend`), different engine + trade-off. `vivijure-backend` is the cloud door
(RunPod serverless, Wan 2.2, datacenter cards, the `own-gpu` path); the LTX door and THIS CogVideoX door
are local (your box, the `local-gpu` path). The studio talks to THIS server with the SAME job lifecycle
it uses for RunPod (`/run` -> `/status/<id>` -> output), and the contract is byte-identical to the LTX
door, so a self-hoster swaps the container without touching the studio. The tiers map to what the card
can HONESTLY deliver: `final` is the card's ceiling, NOT datacenter parity, and the generation body
raises a clear error rather than faking a clip when the GPU runtime is absent (a producer stage never
ships a fake clip).

## Documentation map

Deep docs live in `docs/`; this file is the working method. When a change touches one of these, update
the matching doc.

- `docs/architecture.md` -- the server / job-registry / engine design.
- `docs/HOMELABBER.md` -- the run-it-on-your-box walkthrough (prereqs, tunnel, trade-offs, troubleshooting).
- `docs/INTEGRATION.md` -- the studio-side wiring (pointing the `local-gpu` module at your backend).
- `docs/i2v-model-selection.md` -- why CogVideoX-5B-I2V (the fidelity door) vs LTX / SVD / AnimateDiff.
- `docs/live-benchmark-plan.md` -- the costed, spend-gated plan for the on-card benchmark (executed 2026-07-01).
- `docs/proof/RESULTS.md` -- the validated fit/speed numbers (populated; the 16GB floor proof).
- `docs/RUN-LOG.md` -- the running build/validation log.

## The job API (RunPod-compatible, `src/vivijure_local/core/server.py`)

```
POST /run          { "input": { action:"i2v_clip", project, shot_id, prompt, keyframe_key?, config } } -> { "id" }
GET  /status/<id>  -> { id, status: IN_QUEUE|IN_PROGRESS|COMPLETED|FAILED, output?, error? }
POST /cancel/<id>  -> { ok: true }   (idempotent)
GET  /health       -> { ok: true, engine:"cogvideox", ... }
POST /run { "selftest": true } -> a no-GPU transport probe
```

The server owns an in-process **serial** job registry (a consumer card runs ONE i2v job at a time), the
RunPod-lifecycle stand-in for a box with no serverless platform. CogVideoX-5B-I2V is a FIXED-GRID model
(720x480, 49 frames @ 8 fps), so the tiers differ by inference STEPS, not resolution or length: `draft`
(30 steps), `standard` (40 steps), `final` (50 steps), all at 49 frames with model
CPU offload + VAE tiling/slicing. **VRAM floor + per-clip speed are MEASURED** (docs/proof/RESULTS.md):
the honest floor is a 16GB card, and the tier speeds in the docs are the benchmark numbers.

## Commands

This is a Python package, NOT an npm project. The pure logic is CPU-tested; the torch/diffusers
generation body is deferred-imported and validated on the card.

```bash
# Develop / test on CPU (no GPU, no model weights):
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                  # the full CPU suite (config, vram, frame math, jobs, server routing)
python -m py_compile src/vivijure_local/*.py

# Run it on a GPU box (one command): caches the CogVideoX weights on first start, then serves :8000 + its tunnel.
cp .env.example .env                    # your R2 creds (+ optional LOCAL_BACKEND_TOKEN)
docker compose up -d                    # builds deploy/Dockerfile; the `ready` service prints the copy-paste URL + token
curl localhost:8000/health

# The on-card proof gate (spend-gated; does NOT run without a GPU):
python scripts/benchmark.py             # CogVideoX i2v across the three tiers: fit (peak VRAM/OOM), speed, a sample clip
```

`pytest.ini` sets `pythonpath = src`, `testpaths = tests`. Tests live in `tests/` (`test_config`,
`test_vram`, `test_i2v_cogvideox`, `test_jobs`, `test_server`).

## Verifying changes

`pytest` (the CPU suite) is the everyday gate and must be green; it covers config clamping, VRAM/frame
math, the serial job registry, and server routing without a GPU or weights. The torch/diffusers body is
validated on the card (`scripts/benchmark.py`, the spend-gated proof) and recorded in
`docs/proof/RESULTS.md` + `docs/RUN-LOG.md`. Always run `pytest` (and `py_compile` on the package)
before pushing; the card benchmark is the deliberate, costed step before claiming a tier works. NEVER
run the benchmark on a RunPod COMMUNITY pod (secure cloud only; a hard rule).

## Architecture

- **One server, serial registry.** `core/server.py` is a long-running process with an in-process job
  registry; a consumer card runs one i2v job at a time. The engine is `i2v_cogvideox.py`;
  transport/contract helpers are `core/contract.py` / `core/r2.py`; VRAM math is `core/vram.py`; the tier config is `config.py` (per-door);
  the docker-compose `ready` banner is `core/announce.py`. The shared surface (`r2` / `contract` / `jobs` / `vram` / `announce` / `server`) lives in the byte-identical `vivijure_local.core` package; `door.py` is the per-door identity + engine seam (see docs/architecture.md).
- **One-command, secure-by-default deploy.** `docker compose up` brings up the backend + a Cloudflare
  tunnel + a `ready` banner that prints the copy-paste Backend URL + token. Default is a TryCloudflare
  QUICK tunnel (no CF account); set `TUNNEL_TOKEN` for a stable named tunnel.
- **Security boundary.** One credential: the shared-R2 key (read the keyframe, write the clip). The
  tunnel is PUBLIC, so the i2v endpoint HARD-REJECTS any request without `LOCAL_BACKEND_TOKEN` (it
  auto-generates if unset; the banner shows it). The backend holds no studio secrets and no submitter
  identity; input is control-plane-trusted (reached only through the studio binding + your tunnel).

## Conventions

- **No em-dashes (U+2014) or en-dashes (U+2013) anywhere.** Use commas, semicolons, parentheses, or `--`.
- Handle / username is `skyphusion` across all services.
- **A producer stage never ships a fake clip.** The generation body raises a clear error when the GPU
  runtime is absent rather than faking output; tiers advertise only what a consumer card honestly delivers.
- **Prove-then-name.** The public repo name encodes the proven VRAM tier (16GB), measured on real silicon.
- Minimal runtime deps. The `requirements.txt` diffusers/transformers pins are the LTX door's validated
  cu124 set, KEPT here because CogVideoXImageToVideoPipeline is present at diffusers 0.32.2 (verified).
  torch/torchvision install from the CUDA index in `deploy/Dockerfile` (matched to the card), NOT pinned
  in `requirements.txt`.

## Crew + identity

- The FIRST command in any op is the member's own login shell: `sudo -u <member> bash -lc '<ops>'`
  (loads their `$HOME`, their `~/dev/vivijure-local-16gb` clone, their gh / R2 creds). Commits and
  PRs land under the member's `skyphusion-<member>` identity, never Conrad's.
- Operating memory for the vivijure family lives in the per-project memory under
  `~/.claude/projects/-home-conrad-dev-vivijure/memory/` (`seg-vivijure-modules` covers the cost doors,
  `seg-vivijure-backend-deploy` the cloud counterpart); load it before acting.
- **HARD AUP line:** the CSAM bright line is absolute (see the vivijure project memory). Non-negotiable.

## Commits & versioning

Conventional Commits (`feat(scope):`, `fix(scope):`, `docs:`); body explains the why. SemVer from
**v1.0.0** onward (MAJOR for breaking API changes, MINOR for features, PATCH for fixes).
