"""Per-door identity + engine binding: the ONLY seam `vivijure_local.core` reads that differs between
the two local-gpu doors (LTX 12GB vs CogVideoX 16GB). The `core` package is byte-identical across both
repos and imports its identity from here through the stable `..door` seam, so the shared surface stays a
single diffable file. See docs/architecture.md (shared core)."""
from . import i2v_cogvideox as _engine

SERVICE = "vivijure-local-16gb"   # /health service name + operator-facing labels
ENGINE = "cogvideox"              # engine label in the /health + selftest envelopes

# First-cold-render heads-up (weight download size + wait): printed once by the ready banner (announce).
WEIGHTS_NOTE = "the CogVideoX weights (~22GB, one time), so it takes a good while longer. Later renders skip it."

def animate(*args, **kwargs):
    """Dispatch to the door's engine `animate(shot_id, keyframe_path, prompt, cfg, out_path, *,
    progress_cb) -> I2VResult` at CALL time (a live attribute lookup on the engine module, so tests can
    monkeypatch it; the engine module import is cheap, its torch/diffusers load stays deferred)."""
    return _engine.animate(*args, **kwargs)


def unload_i2v() -> None:
    """Drop resident i2v weights before a preview (keyframe) job claims the card (#153)."""
    unload = getattr(_engine, "unload_all", None)
    if callable(unload):
        unload()

# vGPU honesty (16gb#42). This door engine (CogVideoX-5B-I2V) renders pure-noise, corrupt clips on a
# mediated GRID/vGPU SLICE (e.g. an NVIDIA A16-xQ profile) while still reporting COMPLETED, with no error
# -- confirmed deterministically across cloud boxes and every door version (16gb#35). A whole-card
# PASSTHROUGH is fine; only the sliced vGPU corrupts. So this door declares itself vGPU-incompatible and
# the server (core) WARNS loudly at boot when it detects a slice (warn, never fail: the operator may know
# better). The sibling 12GB LTX door renders correctly on vGPU and does NOT set this flag, so the shared
# core (which reads it via getattr, defaulting off) stays silent there.
VGPU_UNSUPPORTED = True
VGPU_WARNING = "\n".join([
    "=" * 64,
    f"  {SERVICE}: WARNING -- a GRID/vGPU-sliced GPU was detected.",
    "",
    "  This door engine (CogVideoX-5B-I2V) is KNOWN to produce CORRUPTED,",
    "  pure-noise clips on a mediated vGPU slice (e.g. NVIDIA A16-xQ) while",
    "  still reporting the job COMPLETED -- no error, just latent-noise frames.",
    "",
    "  Use a physical / whole-card passthrough GPU for this door. If a vGPU",
    "  slice is your only option, run the 12GB LTX door instead -- it renders",
    "  correctly on the very same hardware:",
    "  https://github.com/skyphusion-labs/vivijure-local-12gb",
    "=" * 64,
])

# Duration grid (#707): this door is the DECLARING SOURCE for the clip lengths it can produce, so the
# control plane preflights a storyboard against the card real limits (read off /health) instead of
# guessing. Values are DERIVED from the SAME config the clamps use (tier_config max_frames + the pinned
# EXPORT_FPS) -- exactly one source of truth. CogVideoX-5B-I2V is a fixed 8 fps model with a per-tier
# frame ceiling; the sibling 12GB LTX door has no fixed grid and omits this block entirely (its absence
# on /health means "no declared constraint"). The shared core reads it via getattr, so the byte-identical
# server stays correct for both doors.
from .config import EXPORT_FPS, QualityTier, tier_config  # noqa: E402

DURATION_GRID = {
    "fps": EXPORT_FPS,
    "tiers": {t.value: {"max_frames": tier_config(t).max_frames} for t in QualityTier},
}
