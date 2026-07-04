# Changelog

All notable changes to vivijure-local-16gb are recorded here. This project follows SemVer-style
`0.MINOR.PATCH` while pre-1.0 (PATCH for fixes and backend tweaks, MINOR for features).

## v0.1.1 -- 2026-07-04

Fix the default quick-tunnel bring-up (compose + ready-banner fix; the render engine is unchanged).

- **cloudflared no longer crash-loops on `docker compose up`.** The `cloudflared` service wrapped its
  tunnel startup in an inline `sh -c` script, but the `cloudflare/cloudflared` image is distroless (no
  shell) and its entrypoint is `cloudflared --no-autoupdate`, so the script was passed to cloudflared as
  arguments and never ran; the default quick tunnel never started and the `ready` banner never printed a
  Backend URL. The service now invokes cloudflared natively:
  `tunnel --url http://vivijure-local-16gb:8000 --logfile /shared/cf.log`. No shell, no entrypoint override, no
  dependence on the distroless image's contents.
- **The backend pre-creates the tunnel logfile for cloudflared's nonroot UID.** `cloudflare/cloudflared`
  runs as nonroot (65532) and cannot create `/shared/cf.log` in the root-owned shared volume, so
  `--logfile` failed with permission-denied and the banner got no URL. The backend entrypoint (root) now
  pre-creates `/shared/cf.log` owned by 65532; `/shared` stays root-owned and the token stays root-only.
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
