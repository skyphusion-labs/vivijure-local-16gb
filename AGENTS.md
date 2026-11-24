# Vivijure Local 16GB

This file is the guide for any coding agent and any contributor. Follow it.

## What this project is

This repo is a local-consumer render backend for Vivijure. It is the CogVideoX motion door.

It is a long-running server. It does image-to-video on one consumer GPU in a homelab you run. The model is CogVideoX-5B-I2V. There is no rented GPU in this door. Reach it over a Cloudflare tunnel.

The speed sibling is [vivijure-local-12gb](https://github.com/skyphusion-labs/vivijure-local-12gb) (LTX-Video). The cloud keyframe engine is [vivijure-backend](https://github.com/skyphusion-labs/vivijure-backend).

Studio panels (`vivijure-cf`, `vivijure-local`) use `motion.backend`. The user picks the door. This repo is public. The license is AGPL-3.0-only.

See git tags, GHCR, and compose for image pins. Do not freeze a version here. Do not write "production-ready as of" a date in this file.

```
vivijure-cf / vivijure-local --> local-gpu module --/run--> tunnel --> this door (CogVideoX-5B-I2V)
```

## Honest tiers

The contract matches the LTX door. A self-hoster can swap the container and leave the studio alone.

A tier advertises only what the card can deliver. `final` is that card's ceiling. It is not datacenter parity. A producer stage never ships a fake clip when the GPU runtime is absent.

The public name encodes the proven VRAM tier (16GB). That figure was measured on real silicon. See [docs/proof/RESULTS.md](docs/proof/RESULTS.md).

## Docs

- [docs/architecture.md](docs/architecture.md) -- server, job registry, engine
- [docs/HOMELABBER.md](docs/HOMELABBER.md) -- run it on your box
- [docs/INTEGRATION.md](docs/INTEGRATION.md) -- point the `local-gpu` module here
- [docs/i2v-model-selection.md](docs/i2v-model-selection.md) -- why this model
- [docs/live-benchmark-plan.md](docs/live-benchmark-plan.md) -- on-card benchmark plan
- [docs/proof/RESULTS.md](docs/proof/RESULTS.md) -- measured fit and speed
- [docs/RUN-LOG.md](docs/RUN-LOG.md) -- build and validation log
- [CONTRIBUTING.md](CONTRIBUTING.md)
- `deploy/runtime.Dockerfile` and `.github/workflows/runtime-build.yml` -- heavy CUDA base (`runtime-t<N>`)
- `deploy/Dockerfile` is `FROM runtime@digest` plus a copy of `src`

## Job API

The server is `src/vivijure_local/core/server.py`.

```
POST /run          { "input": { action:"i2v_clip"|"preview"|"train_lora", ... } } -> { "id" }
GET  /status/<id>  -> { id, status, output?, error? }
POST /cancel/<id>  -> { ok: true }
GET  /health       -> { ok: true, engine:"cogvideox", ... }
POST /run          { "selftest": true } -> a no-GPU transport probe
```

Status values are `IN_QUEUE`, `IN_PROGRESS`, `COMPLETED`, and `FAILED`. Cancel is idempotent.

Actions: `i2v_clip` (CogVideoX motion), `preview` (SDXL keyframes), `train_lora` (SDXL cast UNet LoRA). Wan family training is rejected. One GPU job runs at a time.

CogVideoX-5B-I2V uses a fixed grid: 720 by 480, 49 frames, 8 fps. Tiers differ by inference steps. `draft` is 30. `standard` is 40. `final` is 50. The run uses model CPU offload plus VAE tiling and slicing. VRAM and speed are measured in [docs/proof/RESULTS.md](docs/proof/RESULTS.md).

## Commands

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest
python -m py_compile src/vivijure_local/*.py
cp .env.example .env
docker compose up -d
docker compose ps
python scripts/benchmark.py
```

`pytest.ini` sets `pythonpath = src` and `testpaths = tests`. The CPU suite does not load weights.

`docker compose up` pulls the pinned GHCR image. The `ready` service prints the URL and the token. Port 8000 stays internal.

`scripts/benchmark.py` needs a GPU you control. Do not run it on a RunPod community pod. Record results in [docs/proof/RESULTS.md](docs/proof/RESULTS.md) and [docs/RUN-LOG.md](docs/RUN-LOG.md).

Run `pytest` and `py_compile` before you push. Claim a tier only after the on-card benchmark.

## Architecture

`core/server.py` is one long-running process with an in-process job registry. The engine is `i2v_cogvideox.py`. Transport helpers are `core/contract.py` and `core/r2.py`. VRAM math is `core/vram.py`. Tier config is `config.py`. The compose banner is `core/announce.py`.

The shared surface is the `vivijure_local.core` package. `door.py` is the per-door identity and engine seam. See [docs/architecture.md](docs/architecture.md).

`docker compose up` starts the backend, a Cloudflare tunnel, and the `ready` banner. The default tunnel is a TryCloudflare quick tunnel. Set `TUNNEL_TOKEN` for a stable named tunnel.

One credential is the shared R2 key. The tunnel is public, so `/run` rejects a request with no `LOCAL_BACKEND_TOKEN`. If that token is unset, the process generates one and the banner shows it. The backend holds no studio secret and no submitter identity.

## Conventions

`requirements.txt` pins the validated set, including diffusers and transformers. Read that file. Do not copy pin numbers into this guide. CogVideoXImageToVideoPipeline must load on that diffusers pin. torch and torchvision install from the CUDA index in `deploy/Dockerfile`. They are not pinned in `requirements.txt`.

A producer stage raises a clear error when the GPU runtime is absent. It does not fake a clip.

The public handle is `skyphusion`.

## Release

The version of record is `src/vivijure_local/__init__.py` (`__version__`). The tag shape is `v*`. See that file and [CHANGELOG.md](CHANGELOG.md).

A version moves only in a release pull request. A feature pull request does not bump. A fix pull request does not bump. There is no direct push to `main`.

Before a release pull request, check tags. If the current manifest version already has a tag, the cycle is closed, so the release pull request bumps. If it has no tag, stop. Read why before you bump again.

An org admin tags the release pull request merge commit:

```bash
sha="$(gh pr view <release-pr> --json mergeCommit -q .mergeCommit.oid)"
git tag -a vX.Y.Z -m "vX.Y.Z" "$sha"
git push origin vX.Y.Z
```

A merge to `main` runs CI only. It does not ship an image. `runtime-build.yml` refuses a commit that is not an ancestor of `origin/main`. `publish.yml` pushes the image on a `v*` tag. It does not repeat that ancestor check.

SemVer: MAJOR for a breaking API change, MINOR for a feature, PATCH for a fix. Use a Conventional Commit. The body says why.

## Docs language and safety

Write Simplified Technical English. Use short sentences. Put one idea in each sentence. Use the active present tense. Use plain words. Use the same word every time.

Do not use an em dash (U+2014). Do not use an en dash (U+2013). Use a comma, a semicolon, parentheses, or two hyphens (`--`).

State the limit next to the capability. Do not turn a measured result into a promise.

The CSAM ban includes synthetic images. Do not weaken that check. See [ACCEPTABLE-USE.md](ACCEPTABLE-USE.md).

Do not put a secret in a tracked file. Pin GHCR and compose by a versioned tag. Do not freeze an endpoint id in this file.

Verify the running artifact, not only the pipeline.
