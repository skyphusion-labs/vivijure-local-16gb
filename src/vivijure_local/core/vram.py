"""The VRAM cap knob for a local-gpu door (pure math).

`VIVIJURE_MAX_VRAM_GB` lets a homelabber sharing one card BOUND how much VRAM this process claims. The
parse + fraction math here is PURE (no torch, no CUDA) so it unit-tests on a CPU box; the single torch
call that enforces it (`torch.cuda.set_per_process_memory_fraction`) lives at server startup
(`server.apply_vram_cap`), applied BEFORE any model load.

The card FIT itself is not estimated here: the tier table in `config.py` ships only configs PROVEN on
real silicon at this door's VRAM budget (docs/proof/RESULTS.md), and every request is clamped to those
tier ceilings. (A scaffold-era pre-load VRAM estimator used to live in this module; it was never wired
into the server, so it was removed rather than keep advertising a safety check that never ran.)

Part of the byte-identical `vivijure_local.core` package shared with the sibling door.
"""
from __future__ import annotations

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
