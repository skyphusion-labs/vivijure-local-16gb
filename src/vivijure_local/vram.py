"""Pure VRAM budgeting for the CogVideoX door.

A coarse, HONEST first-order estimator of whether an i2v config fits a card, and which offload mode it
needs. It is deliberately conservative (it would rather recommend more offload than OOM a user's only
GPU), and it is PURE -- no torch, no CUDA -- so it unit-tests on a CPU box and so the server can refuse
or down-shift a job BEFORE loading a model and discovering the OOM the hard way.

These coefficients are rough published/community figures (see docs/i2v-model-selection.md), not
measured on the card. The live benchmark (docs/live-benchmark-plan.md) replaces them with real peak
numbers; until then the estimator's job is to keep the card from OOMing, not to be exact.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import I2VConfig, Offload

# The design floor. PROVISIONAL for CogVideoX: unlike the LTX door (proven at a 12GB budget), the real
# consumer floor for CogVideoX-5B-I2V is pinned by the card benchmark (Milestone 2). CogVideoX-5B is
# heavier than LTX (a 5B transformer + a large T5-XXL text encoder), so the honest floor may sit higher,
# or need sequential offload / quantization to reach a smaller card. 16GB is the working target until
# real silicon says otherwise; we prove-then-name the tier, exactly like LTX.
FLOOR_VRAM_GB = 16.0

# A slice of VRAM the driver / CUDA context / cuDNN workspaces / allocator fragmentation always hold.
# Never available to the model. Set conservatively (PyTorch fragmentation alone routinely strands 1-2GB
# on a card this size), because the cost of being wrong here is OOMing a user's only GPU.
RESERVED_GB = 2.0

# Rough resident footprint per CogVideoX variant, by the precision it loads at. Community/published
# order-of-magnitude figures, not measured here, rounded UP. CogVideoX-5B-I2V is a 5B DiT transformer
# PLUS a large T5-XXL text encoder and a 3D VAE, so the fully-resident cost is far too big for a
# consumer card (that is why offload is not optional here). The offload factor below models paging most
# of that off the GPU; the live benchmark replaces this with the measured peak.
_WEIGHTS_GB = {
    # CogVideoX-5B-I2V: ~5B transformer (bf16) + T5-XXL encoder + 3D VAE. Fully resident is ~22-24GB, so
    # this NEVER picks NONE-offload on a consumer card. With model-cpu-offload community reports fit a
    # 16GB card; sequential offload pushes far lower (~5GB) but is very slow. Milestone 2 measures it.
    "THUDM/CogVideoX-5b-I2V": 24.0,
}
_DEFAULT_WEIGHTS_GB = 12.0  # an unknown CogVideoX variant: assume large so we err toward more offload

# How much each offload mode shaves off the RESIDENT weight cost (the activations cost is separate).
# model-cpu-offload pages whole submodules; sequential pages per layer (far smaller resident set).
_OFFLOAD_RESIDENT_FACTOR = {
    Offload.NONE: 1.0,
    Offload.MODEL_CPU_OFFLOAD: 0.45,
    Offload.SEQUENTIAL_CPU_OFFLOAD: 0.18,
}


@dataclass(frozen=True)
class VramEstimate:
    """The verdict for one config on one card budget."""

    weights_gb: float
    activations_gb: float
    peak_gb: float
    budget_gb: float       # usable VRAM after the reserved slice
    fits: bool
    headroom_gb: float     # budget - peak (negative => predicted OOM)


def latent_pixels(cfg: I2VConfig) -> int:
    """The latent volume that drives activation cost: (W/16) * (H/16) * frames. CogVideoX's VAE spatial
    compression is 8x and the transformer patch is 2 (so the effective spatial stride is 16x), and its
    temporal compression is 4x; the latent grid is the real cost driver, not raw pixels."""
    return max(1, (cfg.width // 16) * (cfg.height // 16) * max(1, cfg.num_frames))


def activations_gb(cfg: I2VConfig) -> float:
    """A coarse activation/attention working-set estimate. Scales with the latent volume; VAE tiling
    bounds the (otherwise spiky) decode peak, so it earns a discount. ~0.9 GB per 100k latent units is
    a conservative placeholder until the benchmark measures it."""
    raw = latent_pixels(cfg) / 100_000.0 * 0.9
    return raw * (0.6 if cfg.vae_tiling else 1.0)


def estimate(cfg: I2VConfig, *, card_gb: float = FLOOR_VRAM_GB) -> VramEstimate:
    """Estimate peak VRAM for `cfg` on a `card_gb` card and decide whether it fits."""
    resident = _WEIGHTS_GB.get(cfg.model, _DEFAULT_WEIGHTS_GB)
    weights = resident * _OFFLOAD_RESIDENT_FACTOR.get(cfg.offload, 1.0)
    acts = activations_gb(cfg)
    peak = weights + acts
    budget = max(0.0, card_gb - RESERVED_GB)
    return VramEstimate(
        weights_gb=round(weights, 2),
        activations_gb=round(acts, 2),
        peak_gb=round(peak, 2),
        budget_gb=round(budget, 2),
        fits=peak <= budget,
        headroom_gb=round(budget - peak, 2),
    )


# --------------------------------------------------------------------------- VRAM cap (env-driven)
# A REAL homelabber feature (not just a test hook): someone sharing one card between vivijure and other
# workloads can BOUND how much VRAM this process claims, via VIVIJURE_MAX_VRAM_GB. The parse + fraction
# math below is PURE (torch-free) so it unit-tests on a CPU box; the single torch call that enforces it
# (torch.cuda.set_per_process_memory_fraction) lives at server startup (server.apply_vram_cap), applied
# BEFORE any model load.

MAX_VRAM_ENV = "VIVIJURE_MAX_VRAM_GB"


def parse_max_vram_gb(raw: str | None) -> float | None:
    """Parse the VIVIJURE_MAX_VRAM_GB env value to a GB cap. Unset / blank / non-numeric / <= 0 ALL mean
    NO CAP (return None => use the whole card), so a mistyped value never silently strands the GPU at a
    surprise fraction; it just falls back to the honest default of the full card."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        gb = float(s)
    except (TypeError, ValueError):
        return None
    return gb if gb > 0 else None


def vram_fraction(gb: float | None, total_gb: float) -> float | None:
    """The per-process memory fraction a `gb` cap maps to on a `total_gb` device. None when uncapped
    (gb is None) or the device total is unknown (<= 0). Clamped to (0, 1]: a cap at or above the card's
    real size collapses to the full-card fraction 1.0 (asking for more than exists just means "all of
    it"), never a value > 1.0 that torch would reject."""
    if gb is None or gb <= 0 or total_gb <= 0:
        return None
    return min(1.0, gb / total_gb)


def strongest_offload(cfg: I2VConfig, *, card_gb: float = FLOOR_VRAM_GB) -> Offload:
    """Pick the WEAKEST offload that still fits `cfg` on the card (weakest = fastest). Walk from NONE
    toward SEQUENTIAL and return the first that fits; if none fits, return SEQUENTIAL (the smallest
    footprint) so the caller can still try, and let the estimate's `fits=False` warn honestly."""
    from dataclasses import replace

    order = [Offload.NONE, Offload.MODEL_CPU_OFFLOAD, Offload.SEQUENTIAL_CPU_OFFLOAD]
    for mode in order:
        if estimate(replace(cfg, offload=mode), card_gb=card_gb).fits:
            return mode
    return Offload.SEQUENTIAL_CPU_OFFLOAD
