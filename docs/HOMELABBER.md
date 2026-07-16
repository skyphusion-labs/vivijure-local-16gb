# Make films on your own GPU (CogVideoX)

Vivijure's motion engine (image-to-video), running on **your** graphics card with **CogVideoX-5B-I2V**,
the fidelity-first local engine. **Production-ready** (v1.0.0): safe to wire into a production Vivijure
Studio today. No cloud GPU, no per-render bill. One setup step (your studio's R2 storage credentials),
one command, and you're rendering.

> **PROVEN on real silicon:** the honest floor is a **16GB card**, speeds are measured, and real-content
> renders are clean on the native 49-frame grid (`docs/proof/RESULTS.md`). Want the fastest local option
> instead of the highest fidelity? Use the **[12GB LTX door](https://github.com/skyphusion-labs/vivijure-local-12gb)**.

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
>
> The backend also DETECTS a vGPU slice at startup (from `nvidia-smi`) and prints a loud warning
> in `docker compose logs` -- it warns, it does not refuse (if you know your setup differs, proceed).

Starting from a fresh Ubuntu box with none of these installed yet? Do **Install the prerequisites**
just below first, then come back to the R2 step and the run. **Already have `nvidia-smi` working and
Docker running?** Skip the install and jump to the
[preflight check](#confirm-your-box-is-ready-preflight) to confirm the rest, then do the R2 step.

### Install the prerequisites (Ubuntu 24.04 LTS)

One tested path, start to finish: **Ubuntu 24.04 LTS.** You do this ONCE per box. Copy each block, run
it, and check the "you should see" line before moving on. These follow each project's official install
guide (linked under each step, and the source of truth if a command changes upstream) and are the same
shape we use to bring up a fresh NVIDIA GPU box on bare-metal Ubuntu 24.04. On Ubuntu 22.04 the same
commands generally work (the driver step may pick an older branch; see the note in step 1). On a
non-Ubuntu distro, use each project's official guide for your package manager -- we test one path, not a
matrix. When you're done, run `./preflight.sh` (below) to confirm every piece is in place -- on this
door it also flags a vGPU slice, which CogVideoX cannot render on.

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

### Confirm your box is ready (preflight)

Not sure every piece is in place? From the repo, run the preflight. It **checks** every prerequisite and
tells you exactly what to fix; it installs nothing and changes nothing on your system:

```sh
git clone https://github.com/skyphusion-labs/vivijure-local-16gb
cd vivijure-local-16gb
./preflight.sh
```

It checks: your NVIDIA driver (version, and that the card is visible), that Docker is installed and its
daemon is running, the compose plugin, that a container can actually see your GPU (the real toolkit
test, not just "is the package installed"), your GPU's VRAM against this door's 16GB floor, and free
disk. **On this door it also warns if your GPU is a GRID/vGPU slice**, which CogVideoX renders as
pure noise while reporting success (see the vGPU callout above); use a physical / passthrough card, or
the 12GB LTX door. Every failed check names the step above that fixes it, and the script exits
non-zero; **all green (a vGPU warning is not a hard failure) means you're ready** for the R2 step and
`docker compose up`.

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
git checkout v1.0.1    # or stay on main once it pins the same release tag in docker-compose.yml
./preflight.sh    # recommended: checks every prerequisite (installs nothing); all green -> go
cp .env.example .env
# edit .env: set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY (R2_BUCKET defaults to "vivijure")
docker compose pull   # ghcr.io/skyphusion-labs/vivijure-local-16gb:1.0.1
docker compose up
```

`docker compose` pulls the version pinned in `docker-compose.yml` (currently **1.0.1**). See
**[GitHub Releases](https://github.com/skyphusion-labs/vivijure-local-16gb/releases)** for the current
stable tag. (Prefer to build from source? `docker compose up --build`.)

Updating: bump the `x-door-image` pin in `docker-compose.yml` to the new release tag (or `git checkout`
that tag), then `docker compose pull && docker compose up -d`. The file uses `pull_policy: missing`, so
it will not re-pull on its own between restarts.

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

**Wire those two values into your Vivijure studio, then pick this door in the planner and render.**
Wiring is one step on the studio side: set `LOCAL_BACKEND_URL` and `LOCAL_BACKEND_TOKEN` (plus
`INSTALL_LOCAL_GPU=1`) in the studio's `deploy.env` and run `./deploy.sh`, which deploys the `local-gpu`
module and binds it to the core (full steps in [docs/INTEGRATION.md](INTEGRATION.md)). Then open the
planner, choose the "Local (your GPU)" door in the motion-backend picker, and render -- a real clip
comes back from your own card. That's it, you just made a film on your own GPU.

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

The studio's three tiers map to CogVideoX settings. CogVideoX-5B-I2V is a fixed-grid model (720x480,
49 frames @ 8 fps), so the tiers differ by inference **steps** (quality vs speed), not frame count or
resolution. Off-grid frame counts can complete but decode as latent tile noise.
`final` here is the model's honest ceiling on your card, not datacenter parity.

| Tier | Resolution | Frames | Steps | Speed on a 16GB Ada-class card | Speed on RTX 4090 (reference) |
|---|---|---|---|---|---|
| draft | 720x480 | 49 (~6.1s) | 30 | ~8.5 min/clip | ~1.6 min/clip |
| standard | 720x480 | 49 (~6.1s) | 40 | ~11 min/clip | ~4 min/clip |
| final | 720x480 | 49 (~6.1s) | 50 | ~14 min/clip (estimated) | ~5 min/clip |

Numbers from `docs/proof/RESULTS.md` (July 2026). Your card may be faster or slower; the 16GB floor
means **fit**, not a speed guarantee.

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

The shipped stack defaults `VIVIJURE_MAX_VRAM_GB=15.5` so a **16GB card does not OOM** from PyTorch
reserving opportunistically (the honest floor for 49-frame CogVideoX with model CPU offload). You do not
need to set this on a first run.

If you have a bigger card and want the full VRAM budget, raise it in `.env` (e.g. `24` on a 24GB card).
If you share the card with a display, another model, or a game, you can lower it further:

```sh
# example: leave 4GB for desktop + another workload on a 20GB card
VIVIJURE_MAX_VRAM_GB=16
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
  (`docker compose logs ready`) match what you set in the studio's `deploy.env`
  (`LOCAL_BACKEND_URL` / `LOCAL_BACKEND_TOKEN`).
- **Renders fail with "could not fetch keyframe ... (404) ... Not Found":** you moved this door to a
  studio on a different Cloudflare account or bucket, but its `.env` still points at the old bucket.
  The door reads keyframes and writes clips against ITS OWN R2, not the studio's, so a 404 here means
  it is looking in the wrong account. Update `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
  `R2_SECRET_ACCESS_KEY`, and `R2_BUCKET` in `.env` to the new studio's R2, then recreate the
  container. (If the door and studio share the same R2 account and bucket, this step is not needed.)

### What's next

This backend runs CogVideoX-5B-I2V today. Coming (a FUTURE milestone): CogVideoX1.5-5B-I2V as a
higher tier (720p, up to 81 frames), and the rest of the studio's module system to grow into.

## License

**AGPL-3.0-only.** Yours to run, learn from, and build on. Run it as a network service and the AGPL has
you share your changes back, so it stays a commons. Not for sale, not to be resold as a SaaS.
