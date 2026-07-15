"""Consumer-scoped render config for the local backend (the CogVideoX door).

This is the honest counterpart to vivijure-backend's `config.py`, and the fidelity sibling of the LTX
door's config. The datacenter backend maps the quality tiers (draft / standard / final) onto Wan 2.2
A14B step counts and datacenter GPU classes (RTX PRO 6000 / H200 / B200). THIS backend maps the SAME
tier vocabulary onto CogVideoX-5B-I2V engine configs a single consumer card can ACTUALLY run -- so
"final" here is the card's honest ceiling, NOT datacenter parity.

Why the tiers keep the same names: the control plane owns the tier set (QUALITY_TIERS) and INJECTS
the chosen tier into every motion.backend module as `quality`. `validateConfig` silently DROPS an
injected value not in the module's enum, so the local-gpu module's enum stays draft/standard/final
(see vivijure/tests/quality-tier-drift.test.ts, #124). The HONESTY is in the engine mapping below and
in `docs/i2v-model-selection.md`, not in renaming the tiers.

CogVideoX-5B-I2V is FIXED-GRID: it is trained/validated at 720x480 x 49 frames @ 8 fps and degrades
badly off that grid, so -- unlike the LTX door, where tiers scale resolution -- these tiers differ
only by inference STEPS. Frame count and resolution stay on the model's native grid.

These numbers are MEASURED defaults (docs/proof/RESULTS.md). The offload mode + VRAM floor that fit a
consumer card were pinned by the July 2026 card benchmark. Nothing here trains or generates; this module
is pure + CPU-importable (no torch), exactly like vivijure-backend's config.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class QualityTier(str, Enum):
    """The control-plane tier vocabulary. Parsed leniently; unknown -> STANDARD (the safe middle)."""

    DRAFT = "draft"
    STANDARD = "standard"
    FINAL = "final"

    @classmethod
    def parse(cls, v: object) -> "QualityTier":
        try:
            return cls(str(v).strip().lower())
        except Exception:
            return cls.STANDARD


class Offload(str, Enum):
    """How aggressively the diffusers pipeline trades speed for VRAM headroom. Ordered weakest ->
    strongest. The stronger the offload, the more the card fits but the slower the run (sequential
    shuttles each layer on/off the GPU per step). CogVideoX-5B carries a large T5 text encoder plus a
    5B transformer, so offload is not optional on a consumer card -- the only question is which mode."""

    NONE = "none"                      # everything resident on the GPU (only a big card; CogVideoX-5B OOMs 16GB here)
    MODEL_CPU_OFFLOAD = "model"        # whole submodules paged to CPU between uses (diffusers enable_model_cpu_offload)
    SEQUENTIAL_CPU_OFFLOAD = "sequential"  # per-layer paging (slowest, smallest footprint; the low-VRAM fallback)


# The CogVideoX i2v variant this door targets. Phase A = the 5B-I2V model (the fidelity leader, custom
# CogVideoX license, register + 1M-visits/mo cap -- see docs/i2v-model-selection.md). Phase B (a FUTURE
# milestone, not wired here) adds CogVideoX1.5-5B-I2V as a higher tier (720p, up to 81 frames). The
# offload mode + real VRAM floor are pinned by the card benchmark (docs/proof/RESULTS.md, Milestone 2).
COGVIDEOX_5B_I2V = "THUDM/CogVideoX-5b-I2V"

# The FIXED export cadence for CogVideoX-5B-I2V: its frames ARE 8 fps frames (the model is trained
# at 8 fps and pinned there), so this is the single source both from_request and the /health
# duration_grid (#707) read -- no second copy of the number.
EXPORT_FPS = 8


@dataclass(frozen=True)
class TierConfig:
    """The engine knobs one quality tier maps to on a consumer card. The animate() body reads these."""

    model: str
    steps: int             # CogVideoX-5B-I2V: ~50 denoise steps is the model-card default; draft trims for speed
    guidance_scale: float  # CogVideoX-5B-I2V sampling ~6.0 (the model card default)
    width: int             # native grid 720x480; divisible by 16 (CogVideoX constraint), enforced in snap_dim
    height: int
    max_frames: int        # native frame count (49 for CogVideoX-5B-I2V; exposed as the duration-grid cap)
    offload: Offload
    vae_tiling: bool       # decode the VAE in tiles + slices to bound peak decode VRAM (the big consumer saver)


# The honest CogVideoX ladder. Resolution and frame count are held at the model's native 720x480x49
# grid across ALL tiers: live diagnostics on physical Ada silicon proved that 25/41-frame renders can
# complete successfully but decode as latent tile noise. Tiers differ only by STEPS (fidelity/speed).
# Offload is model-cpu-offload + VAE tiling/slicing, PROVEN to fit a 16GB card across all three tiers
# (docs/proof/RESULTS.md; 12GB/14GB cards OOM on the 49-frame tiers). NOT datacenter parity; CogVideoX1.5
# is the future higher tier.
_TIERS: dict[QualityTier, TierConfig] = {
    # Fastest native-grid tier: fewer steps, same 49-frame clip. The old 25-frame timing is historical;
    # this corrected shape needs a fresh benchmark.
    QualityTier.DRAFT: TierConfig(
        model=COGVIDEOX_5B_I2V, steps=30, guidance_scale=6.0,
        width=720, height=480, max_frames=49, offload=Offload.MODEL_CPU_OFFLOAD, vae_tiling=True,
    ),
    # The comfortable middle: the full 49-frame clip at a moderate step count. Measured 243.0s/clip
    # on an RTX 4090 (docs/proof).
    QualityTier.STANDARD: TierConfig(
        model=COGVIDEOX_5B_I2V, steps=40, guidance_scale=6.0,
        width=720, height=480, max_frames=49, offload=Offload.MODEL_CPU_OFFLOAD, vae_tiling=True,
    ),
    # The card's HONEST ceiling: the model-card default 50 steps at the full 49-frame native grid.
    # Measured 299.2s/clip on an RTX 4090 (docs/proof).
    QualityTier.FINAL: TierConfig(
        model=COGVIDEOX_5B_I2V, steps=50, guidance_scale=6.0,
        width=720, height=480, max_frames=49, offload=Offload.MODEL_CPU_OFFLOAD, vae_tiling=True,
    ),
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


# Operator override for the diffusers offload mode (16gb#74). UNSET (the default) keeps each tier
# hardcoded, consumer-card-safe strategy byte-for-byte -- no behavior change. A big-VRAM operator can set
# VIVIJURE_OFFLOAD=none to run the model RESIDENT (no per-step CPU paging, faster) or =sequential for the
# low-VRAM fallback; when set it applies to EVERY tier. An INVALID value is a LOUD startup failure
# (server.validate_offload_or_exit), never a silent default -- a fat-fingered knob must surface at boot,
# not as a slow or OOM run later.
OFFLOAD_ENV = "VIVIJURE_OFFLOAD"


def parse_offload_override(raw: object) -> "Offload | None":
    """Parse a VIVIJURE_OFFLOAD value to an Offload override, or None when unset/blank (keep the tier
    default). Pure + CPU-only. Raises ValueError on a non-empty value that is not a valid mode, so the
    operator learns at startup instead of silently getting the per-tier default."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    try:
        return Offload(s)
    except ValueError:
        valid = ", ".join(o.value for o in Offload)
        raise ValueError(
            f"{OFFLOAD_ENV}={raw!r} is not a valid offload mode; use one of: {valid} "
            "(or leave it unset to keep each tier default)"
        ) from None


def offload_override() -> "Offload | None":
    """The active VIVIJURE_OFFLOAD override read from the environment (None when unset). Raises
    ValueError on an invalid value; the server validates it loudly at startup."""
    return parse_offload_override(os.environ.get(OFFLOAD_ENV))


@dataclass(frozen=True)
class I2VConfig:
    """The per-shot i2v config the server hands the engine: a tier baseline with the caller's clamped
    overrides layered on. Mirrors the wire body the local-gpu module sends (quality / num_frames / fps
    / seed / flow_shift / negative_prompt), so the field names match end to end (no remap layer).
    `flow_shift` is carried for wire parity with the LTX door but is inert for CogVideoX."""

    tier: QualityTier
    model: str
    steps: int
    guidance_scale: float
    width: int
    height: int
    num_frames: int
    fps: int
    seed: int
    flow_shift: float
    offload: Offload
    vae_tiling: bool
    negative_prompt: str

    @classmethod
    def from_request(cls, cfg: dict, *, tier: QualityTier | None = None) -> "I2VConfig":
        """Build from the i2v_clip job's `config` dict. The tier baseline is the source of truth; the
        caller may narrow width/height (never widen it). CogVideoX-5B-I2V is frame-count fixed: every
        tier uses the native 49-frame grid, so a caller's num_frames is ignored rather than allowing a
        valid-looking COMPLETED job whose VAE decode is latent tile noise.
        """
        cfg = cfg or {}
        t = tier or QualityTier.parse(cfg.get("quality"))
        base = _TIERS[t]
        # Frame count is not a duration knob for this model. It was trained at 49 frames; physical-card
        # diagnostics reproduced corrupt tile-noise clips at 25 and 41 frames with no runtime error.
        num_frames = base.max_frames
        # Resolution: clamp to the tier ceiling on each axis (never widen past the native grid).
        width = min(base.width, _coerce_int(cfg.get("width"), base.width) or base.width)
        height = min(base.height, _coerce_int(cfg.get("height"), base.height) or base.height)
        seed = _coerce_int(cfg.get("seed"), -1)
        # CogVideoX-5B-I2V is a FIXED 8 fps model: its frames ARE 8fps frames, so pin the export cadence
        # to 8 and ignore a higher requested fps. A shared local-gpu module may default fps=24 (the LTX
        # door cadence); exporting CogVideoX's 49 frames at 24fps would play about 3x too fast. Honest
        # to the model: the knob cannot change what cadence the frames were generated for.
        fps = EXPORT_FPS
        flow_shift = _coerce_float(cfg.get("flow_shift"), 5.0)
        # Operator offload override (VIVIJURE_OFFLOAD): when set it replaces this tier default for
        # EVERY tier; unset keeps the per-tier default byte-for-byte (16gb#74).
        override = offload_override()
        offload = override if override is not None else base.offload
        return cls(
            tier=t, model=base.model, steps=base.steps, guidance_scale=base.guidance_scale,
            width=width, height=height, num_frames=num_frames, fps=fps, seed=seed,
            flow_shift=flow_shift, offload=offload, vae_tiling=base.vae_tiling,
            negative_prompt=str(cfg.get("negative_prompt") or ""),
        )


def tier_config(tier: QualityTier) -> TierConfig:
    """The engine baseline for a tier (a copy-safe frozen dataclass)."""
    return _TIERS[tier]


def _coerce_int(v: object, default: int) -> int:
    try:
        if v is None or isinstance(v, bool):
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _coerce_float(v: object, default: float) -> float:
    try:
        if v is None or isinstance(v, bool):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default
