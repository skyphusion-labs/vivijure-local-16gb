# Make films on your own GPU (CogVideoX)

Vivijure's motion engine (image-to-video), running on **your** graphics card with **CogVideoX-5B-I2V**,
the fidelity-first local engine. No cloud, no per-render bill, no account to sign up for. One command
and you're rendering.

> WORKING NAME / PRE-PROOF: the exact minimum card and per-clip speed are being measured on real
> silicon (the benchmark, `docs/live-benchmark-plan.md`); this page states the shape honestly and marks
> the not-yet-measured numbers as such. If you want the fastest local option instead of the highest
> fidelity, use the LTX door (vivijure-local-12gb).

## Quickstart (you'll be rendering in minutes)

You need: an NVIDIA CUDA GPU, **Docker**, and the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
(one install so the container can see your GPU). CogVideoX-5B needs CPU offload on any consumer card, so
a mid-to-high VRAM card is recommended; the exact floor is pinned by the benchmark. That's it.

```sh
git clone https://github.com/skyphusion-labs/vivijure-local-cogvideox
cd vivijure-local-cogvideox
docker compose up
```

That's the whole setup. The stack starts your render backend, opens its own secure tunnel, downloads
the model once, and then prints a banner like this:

```
================================================================
  Vivijure local backend is LIVE

  Backend URL:    https://quiet-meadow-1234.trycloudflare.com
  Backend token:  3f9a... (your unique token)

  -> Paste these into your Vivijure studio's "Local (your GPU)" door
================================================================
```

**Copy those two values into your Vivijure studio's "Local (your GPU)" door, pick it, and render.**
A real clip comes back from your own card. That's it -- you just made a film on your own GPU.

(No tunnel to configure, no account, no networking to understand. The tunnel is built in and invisible;
the URL + token in the banner are all you touch.)

---

## Go deeper (optional -- when you're curious)

Everything below is opt-in. You already have the thing working; this is room to grow.

### What you just did, and the honest trade-off

You ran a **local door** -- rendering on hardware you own. The studio also has a **datacenter door**
(rented top-end GPUs by the second) and a faster local door (LTX). The trade is real and we're upfront:

| | This door (CogVideoX, local) | LTX door (local) | Datacenter door |
|---|---|---|---|
| Cost | **Free after hardware** (your power) | **Free after hardware** | Pay per render second |
| Strength | **Fidelity** (best local i2v quality) | **Speed** (few-step, fast) | Max fidelity + length |
| Speed | slower (full-step; minutes/clip class) | fast (sub-minute class) | fastest (datacenter cards) |
| Ceiling | 720x480, ~6s clips (CogVideoX-5B-I2V) | ~768x512, ~5s (LTX-Video) | higher res / longer (Wan 2.2) |
| Setup | one command on your box | one command | nothing (it's hosted) |

Pick CogVideoX when you value how the clip looks over how fast it renders; pick LTX when you want quick
iteration; pick the datacenter door for maximum fidelity without owning hardware.

### Quality tiers (what your card honestly delivers)

The studio's three tiers map to CogVideoX settings. CogVideoX-5B-I2V is a fixed-grid model (720x480, up
to 49 frames @ 8 fps), so the tiers differ by inference **steps** (quality vs speed), not resolution.
`final` here is the model's honest ceiling on your card, not datacenter parity. **Per-clip speed is
measured by the benchmark (`docs/live-benchmark-plan.md`); the placeholders below are not yet proven.**

| Tier | Resolution | Frames | Steps | Speed feel |
|---|---|---|---|---|
| draft | 720x480 | 25 | 30 | fastest preview (TBD) |
| standard | 720x480 | 49 (~6s) | 40 | the everyday tier (TBD) |
| final | 720x480 | 49 (~6s) | 50 | best quality, slowest (TBD) |

### A stable address (named tunnel)

The quickstart uses a free quick tunnel: zero setup, but the URL changes each restart. When you want a
**stable hostname** (so you set it in the studio once and forget it), create a free Cloudflare named
tunnel and put its token in `.env` as `TUNNEL_TOKEN` -- the stack uses it automatically. A stable
`LOCAL_BACKEND_TOKEN` (instead of the auto-generated one) goes in `.env` the same way.

### Sharing your GPU (cap the VRAM)

If this card is also driving your display, running another model, or you just want to leave headroom
for other work, you can bound how much VRAM vivijure is allowed to take. Set `VIVIJURE_MAX_VRAM_GB` in
`.env` to the maximum in GB, and the backend pins itself to that slice of the card at startup -- it can
never grab the whole thing.

```sh
# cap vivijure at 14GB and leave the rest of the card for everything else
VIVIJURE_MAX_VRAM_GB=14
```

Leave it blank to use the whole card (the default). A value at or above your card's real size is the
same as leaving it blank. Note the cap is a ceiling, not a discount: if you set it below what a tier
actually needs, that tier will OOM -- drop to a lower tier (`final` -> `standard` -> `draft`), switch to
sequential offload, or raise the cap. The startup log prints the applied cap.

### Troubleshooting

- **"no CUDA device" / the backend never goes LIVE:** the container can't see your GPU. Install the
  NVIDIA Container Toolkit and confirm `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`
  works, then `docker compose up` again.
- **A render fails with out-of-memory:** CogVideoX-5B is heavy; drop to a lower tier (`final` ->
  `standard` -> `draft`). The backend already uses model CPU offload + VAE tiling/slicing to fit a
  consumer card; a marginal card may need sequential offload (slower) or the lighter tier.
- **First render is slow:** that's the one-time model download populating the cache; later renders reuse it.
- **Studio can't reach it:** re-check the Backend URL + token from the banner
  (`docker compose logs ready`) match what you pasted into the studio.

### What's next

This backend runs CogVideoX-5B-I2V today. Coming (a FUTURE milestone): CogVideoX1.5-5B-I2V as a
higher tier (720p, up to 81 frames), and the rest of the studio's module system to grow into.

## License

**AGPL-3.0-only.** Yours to run, learn from, and build on. Run it as a network service and the AGPL has
you share your changes back, so it stays a commons. Not for sale, not to be resold as a SaaS.
