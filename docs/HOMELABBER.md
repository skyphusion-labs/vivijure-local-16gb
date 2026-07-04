# Make films on your own GPU (CogVideoX)

Vivijure's motion engine (image-to-video), running on **your** graphics card with **CogVideoX-5B-I2V**,
the fidelity-first local engine. No cloud GPU, no per-render bill. One setup step (your studio's R2
storage credentials), one command, and you're rendering.

> PROVEN on real silicon: the honest floor is a **16GB card**, and the per-clip speeds below are
> measured (`docs/proof/RESULTS.md`). If you want the fastest local option instead of the highest
> fidelity, use the LTX door (vivijure-local-12gb).

## Quickstart (you'll be rendering in minutes)

You need: an NVIDIA GPU with **16GB+ VRAM** (the measured floor; 12GB and 14GB cards OOM on the full
49-frame tiers), an NVIDIA driver **550 or newer**, **Docker**, the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
(one install so the container can see your GPU), and about **35GB of free disk** (container image +
the ~22GB weights). That's it.

ONE setup step before you start: your Vivijure studio's Cloudflare R2 credentials (this backend shares
that bucket -- it reads the keyframe and writes the finished clip there). Get them from the Cloudflare
dashboard -> R2 -> Manage R2 API Tokens, scoped to your bucket.

```sh
git clone https://github.com/skyphusion-labs/vivijure-local-16gb
cd vivijure-local-16gb
cp .env.example .env
# edit .env: set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY (R2_BUCKET defaults to "vivijure")
docker compose up
```

`docker compose up` PULLS the prebuilt image from GHCR, so there is no long local build -- you go
straight to rendering. (Prefer to build from source? `docker compose up --build`.)

(Forgot the R2 creds? The backend prints a plain message telling you exactly what to set -- not a stack
trace -- and you just run `docker compose up` again.)

That's the whole setup. The stack starts your render backend, opens its own secure tunnel, and
prints a banner like this:

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

One honest heads-up: your **first render** also downloads the CogVideoX weights (~22GB, one time), so
it takes a good while longer. Later renders skip the download.

(No tunnel to configure, no networking to understand. The tunnel is built in and invisible;
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
| Speed | slower (measured ~1.6-5 min/clip) | fast (sub-minute draft; ~2-3 min top tiers) | fastest (datacenter cards) |
| Ceiling | 720x480, ~6s clips (CogVideoX-5B-I2V) | ~768x512, ~5s (LTX-Video) | higher res / longer (Wan 2.2) |
| Setup | one command on your box | one command | nothing (it's hosted) |

Pick CogVideoX when you value how the clip looks over how fast it renders; pick LTX when you want quick
iteration; pick the datacenter door for maximum fidelity without owning hardware.

### Quality tiers (what your card honestly delivers)

The studio's three tiers map to CogVideoX settings. CogVideoX-5B-I2V is a fixed-grid model (720x480, up
to 49 frames @ 8 fps), so the tiers differ by inference **steps** (quality vs speed), not resolution.
`final` here is the model's honest ceiling on your card, not datacenter parity. Speeds are measured
on an RTX 4090 24GB (`docs/proof/RESULTS.md`); a 16GB card runs slower.

| Tier | Resolution | Frames | Steps | Speed feel |
|---|---|---|---|---|
| draft | 720x480 | 25 (~3.1s) | 30 | fastest preview (~1.6min/clip) |
| standard | 720x480 | 49 (~6.1s) | 40 | the everyday tier (~4min/clip) |
| final | 720x480 | 49 (~6.1s) | 50 | best quality, slowest (~5min/clip) |

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
- **First render is slow:** that's the one-time model download (~22GB) populating the cache; expect
  it to take a good while longer on the first render only. Later renders reuse it.
- **Studio can't reach it:** re-check the Backend URL + token from the banner
  (`docker compose logs ready`) match what you pasted into the studio.

### What's next

This backend runs CogVideoX-5B-I2V today. Coming (a FUTURE milestone): CogVideoX1.5-5B-I2V as a
higher tier (720p, up to 81 frames), and the rest of the studio's module system to grow into.

## License

**AGPL-3.0-only.** Yours to run, learn from, and build on. Run it as a network service and the AGPL has
you share your changes back, so it stays a commons. Not for sale, not to be resold as a SaaS.
