"""CPU-testable surface of local SDXL LoRA train (no GPU)."""
from pathlib import Path

import pytest

from vivijure_local.core.bundle import Character
from vivijure_local.core.contract import TrainLoraRequest, lora_key_for
from vivijure_local.lora_train import (
    LoraTrainConfig,
    caption_for,
    config_from_overrides,
    default_base_repo,
    train_slot,
)


def _char(slot="A", name="Vesper", prompt="teal-haired netrunner", refs=()):
    return Character(slot=slot, name=name, prompt=prompt, ref_paths=[Path(p) for p in refs])


def test_default_base_repo_matches_preview_sdxl():
    assert default_base_repo() == "SG161222/RealVisXL_V5.0"


def test_caption_uses_name_as_trigger():
    assert caption_for(_char(), LoraTrainConfig().caption_template) == "Vesper, teal-haired netrunner"


def test_caption_rejects_unknown_placeholder():
    with pytest.raises(ValueError, match="unsupported placeholders"):
        caption_for(_char(), "{name}, {evil}")


def test_train_slot_rejects_no_refs():
    with pytest.raises(ValueError, match="no reference images"):
        train_slot(_char(refs=()), Path("/tmp/never"))


def test_config_from_overrides_clamps():
    cfg = config_from_overrides({"rank": 999, "max_steps": 10, "resolution": 768})
    assert cfg.rank == 128
    assert cfg.max_steps == 10
    assert cfg.resolution == 768


def test_lora_key_for_matches_backend_layout():
    assert lora_key_for("My Film", "A") == "loras/My_Film/A/pytorch_lora_weights.safetensors"


def test_train_lora_request_requires_bundle():
    req = TrainLoraRequest.from_input({"project": "p"})
    assert req.validate() and "bundle_key is required" in req.validate()


def test_train_lora_request_rejects_wan():
    req = TrainLoraRequest.from_input({
        "project": "p",
        "bundle_key": "bundles/p.tar.gz",
        "model_family": "wan",
    })
    assert req.validate() and "SDXL only" in req.validate()


def test_train_lora_request_accepts_sdxl():
    req = TrainLoraRequest.from_input({
        "project": "p",
        "bundle_key": "bundles/p.tar.gz",
        "model_family": "sdxl",
    })
    assert req.validate() is None
