"""The VRAM cap knob (pure math; no torch, no CUDA)."""
from vivijure_local import vram


def test_parse_max_vram_gb_reads_a_positive_number():
    assert vram.parse_max_vram_gb("14") == 14.0
    assert vram.parse_max_vram_gb(" 8.5 ") == 8.5


def test_parse_max_vram_gb_unset_or_blank_is_no_cap():
    assert vram.parse_max_vram_gb(None) is None
    assert vram.parse_max_vram_gb("") is None
    assert vram.parse_max_vram_gb("   ") is None


def test_parse_max_vram_gb_junk_or_nonpositive_is_no_cap():
    assert vram.parse_max_vram_gb("banana") is None
    assert vram.parse_max_vram_gb("0") is None
    assert vram.parse_max_vram_gb("-4") is None


def test_vram_fraction_is_gb_over_total():
    assert vram.vram_fraction(15.0, 24.0) == 15.0 / 24.0


def test_vram_fraction_clamps_a_cap_above_the_card_to_full():
    # asking for more VRAM than the card physically has just means "use all of it" (fraction 1.0),
    # never a >1.0 value that torch.cuda.set_per_process_memory_fraction would reject.
    assert vram.vram_fraction(24.0, 16.0) == 1.0
    assert vram.vram_fraction(16.0, 16.0) == 1.0


def test_vram_fraction_no_cap_or_unknown_total_is_none():
    assert vram.vram_fraction(None, 16.0) is None
    assert vram.vram_fraction(15.0, 0.0) is None


def test_env_to_fraction_end_to_end_torch_free():
    # the whole knob, CPU-only: env string -> gb -> fraction, injecting total_gb (no torch, no CUDA).
    assert vram.vram_fraction(vram.parse_max_vram_gb("15"), 24.0) == 15.0 / 24.0
    assert vram.vram_fraction(vram.parse_max_vram_gb("32"), 16.0) == 1.0   # clamp >card to full
    assert vram.vram_fraction(vram.parse_max_vram_gb(""), 16.0) is None    # unset = no-op
