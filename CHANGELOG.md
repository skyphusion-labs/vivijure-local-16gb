# Changelog

All notable changes to vivijure-local-16gb are recorded here. This project follows SemVer-style
`0.MINOR.PATCH` while pre-1.0 (PATCH for fixes and backend tweaks, MINOR for features).

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
