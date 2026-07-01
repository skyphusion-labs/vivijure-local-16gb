"""The pure VRAM budgeter (no torch, no CUDA)."""
from vivijure_local.config import I2VConfig, Offload, QualityTier
from vivijure_local import vram


def _cfg(tier: QualityTier) -> I2VConfig:
    return I2VConfig.from_request({"quality": tier.value}, tier=tier)


def test_all_three_tiers_are_estimated_to_fit_the_16gb_floor():
    for tier in (QualityTier.DRAFT, QualityTier.STANDARD, QualityTier.FINAL):
        est = vram.estimate(_cfg(tier))
        assert est.fits, f"{tier} predicted not to fit 16GB: {est}"
        assert est.peak_gb <= est.budget_gb


def test_stronger_offload_lowers_the_resident_weight_cost():
    from dataclasses import replace

    base = _cfg(QualityTier.FINAL)
    none = vram.estimate(replace(base, offload=Offload.NONE))
    model = vram.estimate(replace(base, offload=Offload.MODEL_CPU_OFFLOAD))
    seq = vram.estimate(replace(base, offload=Offload.SEQUENTIAL_CPU_OFFLOAD))
    assert none.weights_gb > model.weights_gb > seq.weights_gb


def test_vae_tiling_discounts_the_activation_peak():
    from dataclasses import replace

    cfg = _cfg(QualityTier.STANDARD)
    tiled = vram.activations_gb(cfg)
    untiled = vram.activations_gb(replace(cfg, vae_tiling=False))
    assert tiled < untiled


def test_strongest_offload_picks_the_weakest_that_fits():
    # CogVideoX-5B-I2V is far too big to sit fully resident on a 16GB card, so the budgeter recommends
    # model-cpu-offload at every tier; the real floor + peak are pinned by the card benchmark (Milestone 2).
    for tier in (QualityTier.DRAFT, QualityTier.STANDARD, QualityTier.FINAL):
        assert vram.strongest_offload(_cfg(tier)) is Offload.MODEL_CPU_OFFLOAD


def test_a_24gb_card_has_more_headroom_than_the_16gb_floor():
    cfg = _cfg(QualityTier.FINAL)
    floor = vram.estimate(cfg, card_gb=16.0)
    big = vram.estimate(cfg, card_gb=24.0)
    assert big.headroom_gb > floor.headroom_gb


# --------------------------------------------------------------------------- VRAM cap knob (pure math)

def test_parse_max_vram_gb_reads_a_positive_number():
    assert vram.parse_max_vram_gb("11") == 11.0
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
    assert vram.vram_fraction(11.0, 16.0) == 11.0 / 16.0


def test_vram_fraction_clamps_a_cap_above_the_card_to_full():
    # asking for more VRAM than the card physically has just means "use all of it" (fraction 1.0),
    # never a >1.0 value that torch.cuda.set_per_process_memory_fraction would reject.
    assert vram.vram_fraction(24.0, 16.0) == 1.0
    assert vram.vram_fraction(16.0, 16.0) == 1.0


def test_vram_fraction_no_cap_or_unknown_total_is_none():
    assert vram.vram_fraction(None, 16.0) is None
    assert vram.vram_fraction(11.0, 0.0) is None


def test_env_to_fraction_end_to_end_torch_free():
    # the whole knob, CPU-only: env string -> gb -> fraction, injecting total_gb (no torch, no CUDA).
    assert vram.vram_fraction(vram.parse_max_vram_gb("11"), 16.0) == 11.0 / 16.0
    assert vram.vram_fraction(vram.parse_max_vram_gb("32"), 16.0) == 1.0   # clamp >card to full
    assert vram.vram_fraction(vram.parse_max_vram_gb(""), 16.0) is None    # unset = no-op
