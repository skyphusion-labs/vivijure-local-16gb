# Changelog

All notable changes to vivijure-local-16gb are recorded here. This project follows SemVer-style
`0.MINOR.PATCH` while pre-1.0 (PATCH for fixes and backend tweaks, MINOR for features).

## v0.3.0 -- 2026-07-12

Feature: `VIVIJURE_OFFLOAD` operator knob to pick the diffusers offload mode (#74).

- A big-VRAM operator can now run CogVideoX-5B RESIDENT on the card instead of being forced through
  per-step model-CPU-offload. Set `VIVIJURE_OFFLOAD=none` (whole model resident, fastest, needs a big
  card, roughly 20GB+), `=model` (page whole pieces to CPU: the consumer-card default), or
  `=sequential` (per-layer paging: the low-VRAM fallback). When set it applies to EVERY tier.
- UNSET (the default) keeps each tier hardcoded offload byte-for-byte, so an existing install is
  UNCHANGED -- no behavior change unless the operator opts in.
- An invalid value FAILS LOUD at startup with the valid list, never a silent default (the honesty rule).
- The engine already had the resident path (`pipe.to("cuda")`); this wires the env override into
  `config.from_request` and validates it at boot (`server.validate_offload_or_exit`). Hermetic CPU
  tests cover the parse, the per-tier override, the unset==default guarantee, and the startup guard.
- The fastest offload mode that fits a given card is card-dependent (docs/live-benchmark-plan.md); the
  16GB tiers still fit only under `model`, `none` is for the 20GB+ operator.

## v0.2.2 -- 2026-07-11

Fix: `/cancel` now actually aborts a running render (#70, PR #71).

- The engine step callback swallowed ALL exceptions (`except Exception: pass`), including the
  `core.jobs.Cancelled` signal the job registry raises to abort a render between denoise steps. So
  `POST /cancel` returned `{ok: true}` while the denoise ran to completion and shipped a full clip --
  a silent no-op (and a silent-degrade violation). The callback now re-raises `Cancelled` and swallows
  only genuine progress-reporting errors, so a cancel aborts the denoise at the next step. Hermetic
  tests assert a `Cancelled` raised in the step callback aborts the denoise loop (RED->GREEN). Same
  defect + fix as the sibling 12gb door v0.3.1.

## v0.2.1 -- 2026-07-10

Publish build fix; the render engine is unchanged.

- **Re-pin `av` to 17.1.0 (GHCR build fix).** Dependabot's 17.1.0 -> 18.0.0 bump broke the image
  build: av 18 ships no Python 3.10 wheels and the door image builds on py3.10. CI never builds the
  Docker image, so the bump passed green and the v0.2.0 publish was the first build to hit it.
  Dependabot now ignores `av` (bump only together with a base-image Python move).
- **`__version__` bumped to 0.2.1.**

## v0.2.0 -- 2026-07-10

From-scratch homelabber onboarding: a dependency preflight and one tested bare-OS install path (the
sibling of vivijure-local-12gb v0.2.0). The render engine is unchanged.

- **`preflight.sh`: a dependency preflight that checks, never installs (#61).** Run it before
  `docker compose up`. It checks the NVIDIA driver (present and >= the 550 floor, card visible), GPU
  VRAM against this door's 16GB floor, Docker (installed and daemon up), the compose plugin, that a
  `--gpus all` container can ACTUALLY see the GPU (the real NVIDIA Container Toolkit test, not just
  "package installed"), and free disk. Each failed check names the exact HOMELABBER.md step that fixes
  it and the script exits non-zero; it installs nothing (Conrad ruling: no auto-installers across
  every package manager). **Door-specific:** it ships `WARN_ON_VGPU=1`, so it warns loudly on a
  detected GRID/vGPU slice (which CogVideoX renders as pure noise while reporting success, #35/#42),
  mirroring the runtime boot-warn in `core/gpu_virt.py`. Same shared preflight shape as the 12GB door,
  differing only in the VRAM floor and the vGPU seam default (portable via `DRIVER_FLOOR` /
  `VRAM_FLOOR_MIB` / `DISK_FLOOR_GB` / `WARN_ON_VGPU`).
- **HOMELABBER.md is now one tested Ubuntu 24.04 LTS path (#61).** Added an "already have `nvidia-smi`
  working? skip ahead" branch, a "Confirm your box is ready (preflight)" section, retired the stale
  "we have not run a from-scratch driver install ourselves" caveat, and scoped the docs to the one
  tested distro (other distros point at each project's official guide). README gains a from-scratch +
  preflight pointer.
- **`__version__` bumped to 0.2.0.**

## v0.1.4 -- 2026-07-05

vGPU honesty for the CogVideoX door (16gb#42, splitting the doc + runtime half out of #35). The render
engine is unchanged.

- **Boot-time GRID/vGPU-slice detection (#42).** CogVideoX-5B-I2V renders pure-noise, corrupt clips on a
  mediated GRID/vGPU SLICE (e.g. an NVIDIA A16-xQ profile) while still reporting the job COMPLETED, with
  no error -- confirmed deterministically across cloud boxes and every door version (#35). A whole-card
  passthrough is fine; only the slice corrupts. The server now reads `nvidia-smi -q` at startup and, when
  it detects a sliced vGPU, prints a loud warning naming the 12GB LTX door as the vGPU-tolerant option. It
  WARNS, it does not fail: the operator may know better, and any ambiguous read (nvidia-smi absent,
  non-zero exit, missing field) stays silent -- never a false positive. Detection lives in the
  byte-identical shared core (`core/gpu_virt.py`, pure parse + a best-effort subprocess probe) and is
  gated on a per-door seam (`door.VGPU_UNSUPPORTED`, read via getattr) so the vGPU-tolerant LTX (12GB)
  door stays silent. Parse is unit-tested (GRID, bare-metal, passthrough, and missing-field shapes).
- **README + HOMELABBER: the supported-hardware note now points at the boot warning.** The existing
  "a real, dedicated GPU is required" note (v0.1.3) gains a line noting the backend also detects a slice
  at startup and warns in `docker compose logs`.
- **`__version__` bumped to 0.1.4.**

## v0.1.3 -- 2026-07-05

Homelabber-facing hardening and docs honesty; the render engine is unchanged.

- **Compose no longer collides with another stack on the same host (#39).** The three services carried
  fixed `container_name`s (`vivijure-cloudflared`, `vivijure-ready`, `vivijure-local-16gb`), which are
  global per host: running this door next to a media stack that also names a container
  `vivijure-cloudflared`, or next to the sibling 12GB door, aborted one stack. Dropped the explicit
  names so Compose scopes them per project (`<project>-<service>-1`); inter-service DNS and
  `docker compose logs <service>` are unchanged.
- **Dockerfile no longer claims "NOT on RunPod" (#40).** A stale homelab-era comment; the pinned
  cu124 / torch 2.5.1 stack is portable to any CUDA 12.x card, homelab consumer OR datacenter
  secure-cloud pod.
- **HOMELABBER troubleshooting: moving a door to a new R2 account (#41).** Added the self-diagnosis for
  a `could not fetch keyframe ... (404)` after a door move: the door reads and writes against ITS OWN
  `.env` R2, so re-wire `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_BUCKET` to
  the new studio's bucket.
- **INTEGRATION.md and README rewritten for an outside homelabber (#43).** The studio-wiring recipe
  dropped the internal-CI framing (ci.yml EXCLUDE, PR #382, stale studio versions) for the real path
  (set `INSTALL_LOCAL_GPU=1` plus `LOCAL_BACKEND_URL` and `LOCAL_BACKEND_TOKEN` in `deploy.env`, run
  `./deploy.sh`), and README quickstart step 3 no longer references a "paste into the studio door" UI
  that does not exist.
- **`__version__` bumped to 0.1.3.**

## v0.1.2 -- 2026-07-04

Ready-banner correctness after a restart, plus a bare-OS prerequisite-install guide. The render
engine is unchanged.

- **The ready banner now shows the CURRENT quick-tunnel URL after a restart (#32).**
  `announce` parsed the FIRST match in `/shared/cf.log`, but that log is on the persistent volume and
  cloudflared APPENDS a fresh URL on every (re)start, so after any restart (the documented
  `docker compose up -d` update path, a reboot, or a crash) the banner advertised the oldest, dead URL
  and the studio could not reach the box. It now takes the LAST match, and `init-shared` clears
  `cf.log` on start (`rm -f`, so the nonroot cloudflared keeps ownership under the sticky `/shared`).
  Verified live on a real box with a full restart cycle.
- **Bare-OS prerequisite-install guide in the quickstart (#30).** The docs previously only
  stated the requirements (NVIDIA driver 550+, Docker, NVIDIA Container Toolkit) and linked out; a
  novice on a fresh OS now gets the actual install commands.
- **`__version__` bumped to 0.1.2** so `/health` reports the shipped version (it was left at 0.1.0
  through v0.1.1).

## v0.1.1 -- 2026-07-04

Fix the default quick-tunnel bring-up (compose + ready-banner fix; the render engine is unchanged).

- **cloudflared no longer crash-loops on `docker compose up`.** The `cloudflared` service wrapped its
  tunnel startup in an inline `sh -c` script, but the `cloudflare/cloudflared` image is distroless (no
  shell) and its entrypoint is `cloudflared --no-autoupdate`, so the script was passed to cloudflared as
  arguments and never ran; the default quick tunnel never started and the `ready` banner never printed a
  Backend URL. The service now invokes cloudflared natively:
  `tunnel --url http://vivijure-local-16gb:8000 --logfile /shared/cf.log`. No shell, no entrypoint override, no
  dependence on the distroless image's contents.
- **A one-shot `init-shared` service makes the shared volume writable by cloudflared's nonroot UID.**
  `cloudflare/cloudflared` runs as nonroot (65532) and cannot create `/shared/cf.log` in the root-owned
  shared volume, so `--logfile` failed with permission-denied and the banner got no URL. `init-shared`
  (the app image, root) now runs to completion first (compose `service_completed_successfully`) and sets
  `/shared` to sticky-world-writable (`chmod 1777`, the `/tmp` model), so nonroot cloudflared can create
  its logfile while the sticky bit still protects root-owned files like the token. cloudflared itself
  stays nonroot.
- **The named tunnel is now a documented `docker-compose.override.yml`** (see HOMELABBER "A stable
  address") instead of an automatic `.env` switch, because a shell-free static command cannot branch on
  whether `TUNNEL_TOKEN` is set. The novice quick-tunnel path stays the tracked default.
- **The ready banner reports the real tunnel state.** It shows the actual quick-tunnel URL whenever one
  is live (regardless of whether `TUNNEL_TOKEN` is set), and prints the named-hostname line only when no
  quick URL appears and `TUNNEL_TOKEN` is set, with a one-line hint so a partial config self-diagnoses.
  Keeps "a degrade is never silent": the banner never claims a named hostname while a quick URL is live.

Honest history: the 2026-06-18 switch of `cloudflare/cloudflared:latest` to a distroless nonroot base
broke this compose tunnel service two ways at once -- it removed the shell the inline `sh -c` script
needed, AND it dropped cloudflared to a nonroot UID (65532) that cannot write the root-owned `/shared`.
Combined with the pre-existing entrypoint mismatch, the service was never functional via
`docker compose up`; any tunnel URL in earlier proofs came from a hand-run cloudflared out-of-band.
v0.1.1 addresses all three.

## v0.1.0 -- 2026-07-04

First public release of the CogVideoX local render door: the studio's image-to-video motion engine
running on the operator's own 16GB+ NVIDIA GPU with CogVideoX-5B-I2V, no cloud GPU and no per-render bill.

### What this image ships

- **CogVideoX-5B-I2V backend** rendering on your own card (16GB VRAM floor, proven on real silicon),
  served over a token-gated HTTP API with a `/health` check.
- **One-command bring-up** (`docker compose up`): the render server, its own Cloudflare tunnel, and a
  copy-paste "ready" banner that prints the Backend URL + token for the studio's "Local (your GPU)" door.
- **Prebuilt image pulled from GHCR** (`ghcr.io/skyphusion-labs/vivijure-local-16gb`), so a novice
  pulls instead of cold-building torch layers. Source builders can still `docker compose up --build`.
- **Secure by default:** the tunnel is public, so the i2v endpoint hard-rejects any request without the
  token; the token auto-generates if the operator leaves it blank and is shown in the banner.
- **Built-in tunnel:** a zero-config TryCloudflare quick tunnel by default, or a stable named tunnel
  when `TUNNEL_TOKEN` is set.
- **Three honest quality tiers** (draft / standard / final). CogVideoX-5B-I2V is a fixed 720x480 grid,
  so the tiers differ by inference steps and frame count (quality vs speed), not resolution; an optional
  `VIVIJURE_MAX_VRAM_GB` cap shares the card with other work.
- **Shared R2 bucket** for the render contract: reads the keyframe, writes the finished clip.
- **No SSH in the image:** the released image contains no `sshd` (the Dockerfile installs no openssh).

### Notes

- CogVideoX weights (~22GB) download on the FIRST render into a persistent volume and are reused after,
  so the first render takes a good while longer.
- Compared with the LTX 12gb door: CogVideoX trades speed for how the clip looks (measured ~1.6-5
  min/clip). Pick this door when fidelity matters more than render time.
- `docker-compose.yml` pins `pull_policy: missing`, so the image never auto-updates. To move to a newer
  release, pull explicitly: `docker compose pull` then `docker compose up -d`.
