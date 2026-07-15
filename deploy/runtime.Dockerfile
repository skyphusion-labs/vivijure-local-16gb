# vivijure-local-16gb RUNTIME BASE image (the heavy, slow-changing layer set).
#
# This is the CUDA + torch + render-deps base that almost never changes. It is built by
# .github/workflows/runtime-build.yml on the larger runner, RARELY (a toolchain/deps/CUDA bump, or the
# monthly CVE-refresh floor). The consuming release image (deploy/Dockerfile) is then just
# `FROM this@digest` + COPY src, so a SRC-ONLY release re-pushes only the tiny app layer and the heavy
# torch/deps layers dedup on GHCR ("layer already exists"). That is the publish-time win: the ~10 min
# torch + diffusers/transformers install happens once per toolchain bump, not on every release.
#
#   ghcr.io/skyphusion-labs/vivijure-local-16gb:runtime-t<N>   (a TAG in the same package)
#
# Same package as the release tags on purpose: FROM-inheritance dedups within one GHCR package. NO model
# weights are baked here (CogVideoX pulls ~22GB on the first render into a volume), so unlike
# vivijure-backend there is no seed image and no snapshot runner -- this base is a plain ~8GB pull.
#
# Bump: change torch/torchvision below or requirements.txt, then bump runtime-t<N> (workflow input) and
# repin RUNTIME_REF in deploy/Dockerfile to the digest runtime-build.yml prints.
#
# torch/torchvision from the CUDA wheel index (cu124), PINNED to the set validated on real silicon in the
# S8 re-pin proof (torch 2.5.1 + torchvision 0.20.1, paired with diffusers 0.38.0 / transformers 4.57.6
# in requirements.txt; see SECURITY.md). Portable to any CUDA 12.x card (Ada / Ampere / Hopper,
# sm_80/86/89/90): a homelab consumer card OR a datacenter secure-cloud pod.
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/hf

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# torch first, from the CUDA wheel index (Ada / cu124). Pinned to the proof-validated pair.
RUN pip3 install --index-url https://download.pytorch.org/whl/cu124 torch==2.5.1 torchvision==0.20.1

# The render deps (diffusers/transformers/accelerate/... + R2 I/O). Pinned in requirements.txt.
COPY requirements.txt /app/requirements.txt
RUN pip3 install -r /app/requirements.txt

# NOTE: no `COPY src` here. The app code lives in the thin consumer image (deploy/Dockerfile) so a
# src-only release never rebuilds this base. A CPU import smoke (torch/diffusers/transformers) runs in
# runtime-build.yml before this base is pushed.
