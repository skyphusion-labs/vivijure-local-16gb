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
