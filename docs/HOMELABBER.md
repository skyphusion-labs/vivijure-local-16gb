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

> ### A real, dedicated GPU is required -- cloud "vGPU" slices do NOT work with this door
>
> A GRID/vGPU-sliced card (the mediated-passthrough kind many cloud "vGPU" plans rent, such as the
> NVIDIA **A16-16Q** "16Q" profile) produces **pure-noise, corrupt clips** with CogVideoX-5B, even
> though the render reports COMPLETED and the VRAM number looks right. There is no error and no
> warning -- just a valid-looking mp4 that is latent noise on every frame. This is **deterministic**,
> confirmed across multiple cloud boxes and every door version (#35). The 16GB floor above assumes
> **physical silicon** (a whole card passed through, not a slice). If your only option is a vGPU
> slice, use the **[12GB LTX door](https://github.com/skyphusion-labs/vivijure-local-12gb)** instead:
> it renders correctly on the very same vGPU hardware.

Starting from a fresh Ubuntu box with none of these installed yet? Do **Install the prerequisites**
just below first, then come back to the R2 step and the run.

### Install the prerequisites (Ubuntu 22.04 / 24.04)

You do this ONCE per box. Copy each block, run it, and check the "you should see" line before moving on.
The commands below follow each project's official install guide (cited under each step); we have not
run a from-scratch driver install on bare metal ourselves, so if a command changed upstream, the linked
guide is the source of truth. On a non-Ubuntu distro, use the same three guides for your package manager.

**1. NVIDIA driver (550 or newer).** The distro-standard route picks the recommended driver for your
card:

```sh
sudo ubuntu-drivers install
sudo reboot
```

After the reboot, check it:

```sh
nvidia-smi
```

You should see a table with your GPU and a **Driver Version of 550 or higher** (and a CUDA Version of
12.x). If `ubuntu-drivers` installed something older than 550 (can happen on 22.04), list the options
with `ubuntu-drivers devices` and install a 550+ branch explicitly, e.g. `sudo ubuntu-drivers install
nvidia:550`, then reboot again.
(Source: Ubuntu Server "Install NVIDIA drivers" -- https://documentation.ubuntu.com/server/how-to/graphics/install-nvidia-drivers/)

**2. Docker Engine + the compose plugin (Docker's official apt repo).**

```sh
# Add Docker's official GPG key
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Add the Docker repo to apt
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update

# Install the engine, CLI, and the compose plugin
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Optional but handy, so you do not need `sudo` for every docker command:

```sh
sudo usermod -aG docker $USER
newgrp docker    # or just log out and back in, so the new group takes effect
```

Check it:

```sh
docker run --rm hello-world
```

You should see Docker's "Hello from Docker!" message. If you get `permission denied` on the socket, you
skipped the `usermod`/`newgrp` step above (or need to log out and back in).
(Source: Docker "Install Docker Engine on Ubuntu" -- https://docs.docker.com/engine/install/ubuntu/)

**3. NVIDIA Container Toolkit (NVIDIA's official apt repo).** This is what lets the container see your
GPU:

```sh
# Add NVIDIA's repo
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit

# Wire the toolkit into Docker and restart the daemon
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Now the "prereqs OK" gate -- the container must be able to run `nvidia-smi` on your card:

```sh
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

You should see the SAME GPU table as step 1, but printed from INSIDE a container. If that works, every
prerequisite is in place and you are ready to run vivijure.
(Source: NVIDIA "Installing the NVIDIA Container Toolkit" -- https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)

ONE setup step before you start: your Vivijure studio's Cloudflare R2 credentials (this backend shares
that bucket -- it reads the keyframe and writes the finished clip there). Get them from the Cloudflare
dashboard -> R2 -> Manage R2 API Tokens, scoped to your bucket.

Getting `R2_ACCOUNT_ID` right matters (a wrong value returns a 403, not a clear error): it is your
**Cloudflare account ID**, a 32-character hex string, NOT part of the access key or secret. Find it in
the R2 S3 API endpoint Cloudflare shows for your bucket, `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`
(the account ID is the part before `.r2.cloudflarestorage.com`); it is also the hex ID in your dashboard
URL, `https://dash.cloudflare.com/<ACCOUNT_ID>`. The access key ID and secret are the OTHER two values,
shown once when you create the R2 API token.

```sh
git clone https://github.com/skyphusion-labs/vivijure-local-16gb
cd vivijure-local-16gb
cp .env.example .env
# edit .env: set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY (R2_BUCKET defaults to "vivijure")
docker compose up
```

`docker compose up` PULLS the prebuilt image from GHCR, so there is no long local build -- you go
straight to rendering. (Prefer to build from source? `docker compose up --build`.)

Updating: `docker compose up` PULLS the image once, then `pull_policy: missing` means it never
re-pulls on its own -- no surprise auto-updates. To move to a newer release, pull it explicitly with
`docker compose pull`, then `docker compose up -d`.

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
tunnel and switch the tunnel to it with a small `docker-compose.override.yml` next to the compose file
(Docker Compose merges an override file automatically, so you never edit the tracked `docker-compose.yml`):

```yaml
services:
  cloudflared:
    command: ["tunnel", "run"]
```

Then put the named tunnel's token in `.env` as `TUNNEL_TOKEN` (cloudflared reads it automatically for
`tunnel run`). A stable `LOCAL_BACKEND_TOKEN` (instead of the auto-generated one) goes in `.env` the
same way.

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
