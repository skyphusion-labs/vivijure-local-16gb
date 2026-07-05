# Changelog

All notable changes to vivijure-local-16gb are recorded here. This project follows SemVer-style
`0.MINOR.PATCH` while pre-1.0 (PATCH for fixes and backend tweaks, MINOR for features).

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
