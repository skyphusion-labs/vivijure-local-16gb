"""The honest 16GB tier->engine mapping (no GPU)."""
import pytest

from vivijure_local.config import (
    OFFLOAD_ENV,
    I2VConfig,
    Offload,
    QualityTier,
    offload_override,
    parse_offload_override,
    tier_config,
)


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


def test_fps_pinned_to_8_and_seed_and_negative_pass_through():
    # CogVideoX-5B-I2V is fixed 8 fps: the backend pins the export cadence to 8 regardless of the
    # requested fps (a shared module may send the LTX door's 24). The frames ARE 8fps frames.
    assert I2VConfig.from_request({"fps": 999}).fps == 8
    assert I2VConfig.from_request({"fps": 24}).fps == 8
    assert I2VConfig.from_request({"fps": 1}).fps == 8
    assert I2VConfig.from_request({}).fps == 8
    assert I2VConfig.from_request({"seed": 42}).seed == 42
    assert I2VConfig.from_request({"seed": -1}).seed == -1
    assert I2VConfig.from_request({"negative_prompt": "blurry"}).negative_prompt == "blurry"


def test_bad_numeric_values_fall_back_to_defaults_not_crash():
    cfg = I2VConfig.from_request({"fps": "abc", "seed": None, "num_frames": "x", "flow_shift": True})
    assert cfg.fps == 8 and cfg.seed == -1 and cfg.flow_shift == 5.0


# --- VIVIJURE_OFFLOAD operator override (16gb#74) ---------------------------------------------------

def test_parse_offload_override_unset_or_blank_is_none():
    # Unset / blank / whitespace all mean "no override" -> each tier keeps its hardcoded default.
    assert parse_offload_override(None) is None
    assert parse_offload_override("") is None
    assert parse_offload_override("   ") is None


def test_parse_offload_override_maps_each_valid_mode():
    assert parse_offload_override("none") is Offload.NONE
    assert parse_offload_override("model") is Offload.MODEL_CPU_OFFLOAD
    assert parse_offload_override("sequential") is Offload.SEQUENTIAL_CPU_OFFLOAD
    # case-insensitive + surrounding whitespace tolerated (operator ergonomics)
    assert parse_offload_override("  NONE  ") is Offload.NONE
    assert parse_offload_override("Model") is Offload.MODEL_CPU_OFFLOAD


def test_parse_offload_override_invalid_raises_loud():
    # A fat-fingered value must FAIL LOUD, never silently default (the honesty rule).
    with pytest.raises(ValueError) as ei:
        parse_offload_override("resident")
    msg = str(ei.value)
    assert "VIVIJURE_OFFLOAD" in msg
    assert "none" in msg and "model" in msg and "sequential" in msg  # lists the valid modes


def test_offload_override_reads_the_env(monkeypatch):
    monkeypatch.delenv(OFFLOAD_ENV, raising=False)
    assert offload_override() is None
    monkeypatch.setenv(OFFLOAD_ENV, "none")
    assert offload_override() is Offload.NONE


def test_from_request_keeps_tier_default_when_unset(monkeypatch):
    # Byte-for-byte: with no override, every tier resolves to its hardcoded offload.
    monkeypatch.delenv(OFFLOAD_ENV, raising=False)
    for t in (QualityTier.DRAFT, QualityTier.STANDARD, QualityTier.FINAL):
        cfg = I2VConfig.from_request({"quality": t.value})
        assert cfg.offload is tier_config(t).offload


def test_from_request_applies_override_to_every_tier(monkeypatch):
    # A set override replaces the per-tier default for ALL tiers (the big-VRAM operator opt-in).
    monkeypatch.setenv(OFFLOAD_ENV, "none")
    for t in (QualityTier.DRAFT, QualityTier.STANDARD, QualityTier.FINAL):
        assert I2VConfig.from_request({"quality": t.value}).offload is Offload.NONE
    monkeypatch.setenv(OFFLOAD_ENV, "sequential")
    assert I2VConfig.from_request({"quality": "draft"}).offload is Offload.SEQUENTIAL_CPU_OFFLOAD
