"""The VRAM cap knob for a local-gpu door (pure math).

`VIVIJURE_MAX_VRAM_GB` lets a homelabber sharing one card BOUND how much VRAM this process claims. The
parse + fraction math here is PURE (no torch, no CUDA) so it unit-tests on a CPU box; the single torch
call that enforces it (`torch.cuda.set_per_process_memory_fraction`) lives at server startup
(`server.apply_vram_cap`), applied BEFORE any model load.

What the cap is: a bound on THIS process's own allocator. It stops this process claiming more than the
cap. It CANNOT stop another process from taking memory later; torch still believes this process owns the
capped slice whatever else is resident. The only check against other consumers is a one-time read of FREE
memory at boot (`server.apply_vram_cap`), so it sees memory taken BEFORE boot and nothing taken after. If
the requested cap exceeds what is free at boot, the applied cap is clamped down to the free amount (less a
small headroom, `FREE_HEADROOM_GB`) and a warning names requested, free and total. The fraction handed to
torch is of the card TOTAL, because that is what `torch.cuda.set_per_process_memory_fraction` applies.

The card FIT itself is not estimated here: the tier table in `config.py` ships only configs PROVEN on
real silicon at this door's VRAM budget (docs/proof/RESULTS.md), and every request is clamped to those
tier ceilings. (A scaffold-era pre-load VRAM estimator used to live in this module; it was never wired
into the server, so it was removed rather than keep advertising a safety check that never ran.)

Part of the byte-identical `vivijure_local.core` package shared with the sibling door.
"""
from __future__ import annotations

MAX_VRAM_ENV = "VIVIJURE_MAX_VRAM_GB"

# Slack left under the free reading when clamping a cap to it. mem_get_info is a point-in-time read taken
# after this process created its CUDA context, and the other consumer can grow a little before the model
# loads; allocating exactly to the last free byte would OOM on the first cuBLAS/cuDNN workspace. 0.5GB is
# a small margin, not a guarantee (see the module docstring).
FREE_HEADROOM_GB = 0.5


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


def vram_free_shortfall(gb: float | None, free_gb: float) -> bool:
    """True when a positive cap is larger than the free memory just read.

    free_gb <= 0 means the read is unusable, so this returns False instead of warning on a zero.
    """
    if gb is None or gb <= 0 or free_gb <= 0:
        return False
    return free_gb < gb


def clamp_cap_to_free(gb: float | None, free_gb: float, headroom_gb: float = FREE_HEADROOM_GB) -> float | None:
    """The cap to actually apply: `gb` when the free memory just read can hold it, otherwise the free
    amount less `headroom_gb`. Never larger than `gb`. None stays None (uncapped). An unusable read
    (free_gb <= 0) or a free amount no larger than the headroom returns `gb` unchanged, so a bad read
    never strands the process at a zero or negative cap (today's behaviour)."""
    if gb is None or gb <= 0:
        return None
    if free_gb <= 0 or gb <= free_gb:
        return gb
    clamped = free_gb - headroom_gb
    if clamped <= 0:
        return gb
    return min(gb, clamped)
