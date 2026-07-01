"""The honest 16GB tier->engine mapping (no GPU)."""
from vivijure_local.config import I2VConfig, Offload, QualityTier, tier_config


def test_tier_parse_is_lenient_and_defaults_to_standard():
    assert QualityTier.parse("draft") is QualityTier.DRAFT
    assert QualityTier.parse("FINAL") is QualityTier.FINAL
    assert QualityTier.parse("nonsense") is QualityTier.STANDARD
    assert QualityTier.parse(None) is QualityTier.STANDARD


def test_the_three_tiers_map_to_distinct_honest_configs():
    draft, std, final = (tier_config(t) for t in (QualityTier.DRAFT, QualityTier.STANDARD, QualityTier.FINAL))
    # draft is the lightest/fastest; final is the card's honest ceiling (more steps; res is fixed at the
    # CogVideoX native grid).
    assert draft.width <= std.width <= final.width
    assert final.steps > std.steps > draft.steps           # CogVideoX tiers differ by steps (fidelity)
    assert all(t.offload is Offload.MODEL_CPU_OFFLOAD for t in (draft, std, final))  # CogVideoX-5B needs model-offload on a consumer card
    assert all(t.vae_tiling for t in (draft, std, final))   # tiling everywhere (the big 16GB saver)


def test_from_request_uses_the_tier_baseline():
    cfg = I2VConfig.from_request({"quality": "standard"})
    base = tier_config(QualityTier.STANDARD)
    assert cfg.tier is QualityTier.STANDARD
    assert cfg.model == base.model
    assert cfg.steps == base.steps
    assert cfg.width == base.width and cfg.height == base.height


def test_caller_can_narrow_but_never_widen_past_the_honest_ceiling():
    base = tier_config(QualityTier.STANDARD)
    # Ask for a huge resolution + frame count: clamped DOWN to the tier ceiling (the card's honest fit).
    cfg = I2VConfig.from_request(
        {"quality": "standard", "width": 4096, "height": 4096, "num_frames": 9999}
    )
    assert cfg.width == base.width and cfg.height == base.height
    assert cfg.num_frames == base.max_frames
    # A smaller request is honored (narrowing is fine).
    smaller = I2VConfig.from_request({"quality": "standard", "num_frames": 49})
    assert smaller.num_frames == 49


def test_fps_clamped_to_8_30_and_seed_and_negative_pass_through():
    assert I2VConfig.from_request({"fps": 999}).fps == 30
    assert I2VConfig.from_request({"fps": 1}).fps == 8
    assert I2VConfig.from_request({"seed": 42}).seed == 42
    assert I2VConfig.from_request({"seed": -1}).seed == -1
    assert I2VConfig.from_request({"negative_prompt": "blurry"}).negative_prompt == "blurry"


def test_bad_numeric_values_fall_back_to_defaults_not_crash():
    cfg = I2VConfig.from_request({"fps": "abc", "seed": None, "num_frames": "x", "flow_shift": True})
    assert cfg.fps == 8 and cfg.seed == -1 and cfg.flow_shift == 5.0
