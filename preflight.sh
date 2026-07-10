#!/usr/bin/env bash
# vivijure-local-16gb preflight: CHECK (never install) the host prerequisites this backend needs, and
# tell you exactly what to fix if one is missing. It changes NOTHING on your system -- it only looks.
# Run it before `docker compose up`:
#
#     ./preflight.sh
#
# Every failed check prints WHAT is wrong and the docs/HOMELABBER.md section that fixes it, and the
# script exits non-zero (so you, or a script, can tell it did not pass). All green means you're ready.
#
# Why it never installs anything (vivijure-local-16gb#61): auto-installing across every Linux package
# manager is a rabbit hole we don't maintain. Instead this diagnoses and points you at the ONE tested
# install path -- docs/HOMELABBER.md, "Install the prerequisites", Ubuntu 24.04 LTS.
#
# Door seam: this is the SAME script the sibling doors ship; per-door behavior is a few env floors
# (VRAM_FLOOR_MIB) plus WARN_ON_VGPU. THIS is the 16GB CogVideoX door, which renders CORRUPT (pure-noise)
# clips on a GRID/vGPU slice while reporting success (#35/#42), so it ships WARN_ON_VGPU=1: the preflight
# warns loudly on a detected slice, mirroring the backend's own startup guard (core/gpu_virt.py).
set -u

# ---- door floors / seams (a sibling door ships the same script with different defaults) -------------
DRIVER_FLOOR="${DRIVER_FLOOR:-550}"          # NVIDIA driver MAJOR version floor
VRAM_FLOOR_MIB="${VRAM_FLOOR_MIB:-16000}"    # this door needs a ~16GB card (12GB/14GB OOM the full tiers)
DISK_FLOOR_GB="${DISK_FLOOR_GB:-35}"         # container image + the ~22GB CogVideoX weights (first render)
WARN_ON_VGPU="${WARN_ON_VGPU:-1}"            # 1: CogVideoX corrupts on a vGPU slice -> warn. 0: LTX tolerates it.
GPU_TEST_IMAGE="${GPU_TEST_IMAGE:-nvidia/cuda:12.4.1-base-ubuntu22.04}"
DOC="docs/HOMELABBER.md"

fails=0
red()    { printf "  FAIL  %s\n" "$1"; fails=$((fails+1)); }
grn()    { printf "  OK    %s\n" "$1"; }
warnln() { printf "  WARN  %s\n" "$1"; }
skipln() { printf "  SKIP  %s\n" "$1"; }
fix()    { printf "        -> fix: %s\n" "$1"; }

echo "vivijure-local-16gb preflight (checks only; installs nothing)"
echo "==========================================================="

# 0. OS note (informational; the one tested path is Ubuntu 24.04 LTS)
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  if [ "${ID:-}" = "ubuntu" ] && [ "${VERSION_ID:-}" = "24.04" ]; then
    grn "OS: Ubuntu 24.04 LTS (the tested path)"
  else
    warnln "OS: ${PRETTY_NAME:-unknown} -- the tested path is Ubuntu 24.04 LTS. The same checks apply;"
    printf "        if a step differs on your distro, follow each tool's official guide (linked in %s).\n" "$DOC"
  fi
else
  warnln "OS: could not read /etc/os-release (continuing; the tested path is Ubuntu 24.04 LTS)"
fi
echo

# 1. NVIDIA driver present + >= floor
have_driver=0
if command -v nvidia-smi >/dev/null 2>&1; then
  dv="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n1 | tr -d ' ')"
  if [ -z "$dv" ]; then
    red "NVIDIA driver: nvidia-smi is installed but reports no GPU/driver."
    fix "reboot after installing the driver, then re-check 'nvidia-smi'. $DOC step 1 (NVIDIA driver)."
  else
    dvmajor="${dv%%.*}"
    if [ "${dvmajor:-0}" -ge "$DRIVER_FLOOR" ] 2>/dev/null; then
      grn "NVIDIA driver: $dv (>= $DRIVER_FLOOR)"
      have_driver=1
    else
      red "NVIDIA driver: $dv is older than the $DRIVER_FLOOR floor."
      fix "install a $DRIVER_FLOOR+ driver ('sudo ubuntu-drivers install'), then reboot. $DOC step 1."
    fi
  fi
else
  red "NVIDIA driver: 'nvidia-smi' not found -- no NVIDIA driver is installed."
  fix "install it: 'sudo ubuntu-drivers install' then 'sudo reboot'. $DOC step 1 (NVIDIA driver)."
fi

# 2. GPU VRAM >= floor
if [ "$have_driver" = "1" ]; then
  vram="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | sort -nr | head -n1 | tr -d ' ')"
  if [ -n "$vram" ] && [ "$vram" -ge "$VRAM_FLOOR_MIB" ] 2>/dev/null; then
    grn "GPU VRAM: ${vram} MiB (>= ${VRAM_FLOOR_MIB} MiB floor)"
  elif [ -n "$vram" ]; then
    red "GPU VRAM: ${vram} MiB is below the ${VRAM_FLOOR_MIB} MiB this door needs."
    fix "this backend targets a 16GB+ card (12GB/14GB OOM the full tiers); see the 'Quality tiers' table in $DOC."
  else
    warnln "GPU VRAM: could not read memory.total from nvidia-smi (skipping the size check)."
  fi
else
  skipln "GPU VRAM: skipped (no working driver yet -- fix the driver check first)."
fi

# 2b. vGPU-slice warning (door-gated). A mediated GRID/vGPU SLICE can render corrupt (pure-noise) clips
# on some door engines while REPORTING success; a whole-card passthrough is fine. WARN, never fail --
# the operator may know better, and only a "vgpu" mode is flagged (passthrough / none / unknown stay
# silent). Mirrors the backend's own startup guard (core/gpu_virt.py).
if [ "$WARN_ON_VGPU" = "1" ] && [ "$have_driver" = "1" ]; then
  vmode="$(nvidia-smi -q 2>/dev/null | awk -F: '/^[[:space:]]*Virtualization Mode/{gsub(/^[ \t]+|[ \t]+$/,"",$2); print tolower($2); exit}')"
  if [ "$vmode" = "vgpu" ]; then
    warnln "GPU is a GRID/vGPU SLICE (Virtualization Mode: vgpu). CogVideoX-5B renders corrupted"
    printf "        (pure-noise) clips on a vGPU slice while REPORTING success (#35). Use a physical /\n"
    printf "        whole-card passthrough GPU, or the 12GB LTX door (which tolerates vGPU). See the vGPU\n"
    printf "        note in %s. (Warning only; not a hard failure -- the door also warns at startup.)\n" "$DOC"
  fi
fi

# 3. Docker present + daemon up
have_docker=0
if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    grn "Docker: installed and the daemon is running"
    have_docker=1
  else
    red "Docker: installed, but the daemon is not reachable (or you lack permission to talk to it)."
    fix "start it ('sudo systemctl start docker'), or add yourself to the docker group ('sudo usermod -aG docker \$USER', then log out and back in). $DOC step 2 (Docker)."
  fi
else
  red "Docker: 'docker' not found -- Docker Engine is not installed."
  fix "install Docker Engine + the compose plugin. $DOC step 2 (Docker Engine + the compose plugin)."
fi

# 4. Compose v2
if [ "$have_docker" = "1" ]; then
  if docker compose version >/dev/null 2>&1; then
    cv="$(docker compose version --short 2>/dev/null | tr -d ' ')"
    grn "Docker Compose v2: present${cv:+ (v$cv)}"
  else
    red "Docker Compose v2: the 'docker compose' plugin is missing."
    fix "install the compose plugin (docker-compose-plugin). $DOC step 2 (Docker Engine + the compose plugin)."
  fi
else
  skipln "Docker Compose v2: skipped (Docker not usable yet -- fix the Docker check first)."
fi

# 5. NVIDIA runtime actually wired -- the REAL test: a --gpus all container that runs nvidia-smi
if [ "$have_docker" = "1" ] && [ "$have_driver" = "1" ]; then
  printf "  ..    checking GPU-in-container (may pull a small test image once)...\n"
  if docker run --rm --gpus all "$GPU_TEST_IMAGE" nvidia-smi >/dev/null 2>&1; then
    grn "NVIDIA Container Toolkit: a --gpus all container can see your GPU"
  else
    red "NVIDIA Container Toolkit: Docker cannot hand a container your GPU (the container test failed)."
    fix "install the NVIDIA Container Toolkit and wire it: 'sudo nvidia-ctk runtime configure --runtime=docker', then 'sudo systemctl restart docker'. $DOC step 3 (NVIDIA Container Toolkit)."
    printf "        (test run: docker run --rm --gpus all %s nvidia-smi)\n" "$GPU_TEST_IMAGE"
  fi
else
  skipln "NVIDIA Container Toolkit: skipped (need a working driver AND Docker first)."
fi

# 6. Disk headroom for the image + weights
avail_kb="$(df -Pk /var/lib/docker 2>/dev/null | awk 'NR==2{print $4}')"
[ -z "$avail_kb" ] && avail_kb="$(df -Pk / 2>/dev/null | awk 'NR==2{print $4}')"
if [ -n "$avail_kb" ]; then
  avail_gb=$((avail_kb/1024/1024))
  if [ "$avail_gb" -ge "$DISK_FLOOR_GB" ]; then
    grn "Disk: ${avail_gb} GB free (>= ${DISK_FLOOR_GB} GB)"
  else
    red "Disk: only ${avail_gb} GB free where Docker stores data; this door needs about ${DISK_FLOOR_GB} GB (image + model weights)."
    fix "free up space, or point Docker's data-root at a bigger disk. See the disk note in $DOC (Quickstart)."
  fi
else
  warnln "Disk: could not measure free space (skipping)."
fi

echo
echo "==========================================================="
if [ "$fails" -eq 0 ]; then
  echo "All checks passed. You're ready: cp .env.example .env, set your R2 creds, then 'docker compose up'."
  exit 0
else
  echo "$fails check(s) failed. Fix each FAIL above (every one points at the exact $DOC section), then re-run ./preflight.sh."
  exit 1
fi
