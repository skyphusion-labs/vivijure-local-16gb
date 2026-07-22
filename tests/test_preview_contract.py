"""CPU tests for the local-door preview (keyframe) contract (#153)."""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from vivijure_local.core.bundle import build_prompt, extract_bundle, storyboard_from_dict
from vivijure_local.core.contract import PreviewRequest, keyframe_key_for
from vivijure_local.preview_sdxl import plan_shots, tier_params


def test_preview_request_requires_bundle_key():
    assert PreviewRequest.from_input({"project": "p"}).validate()
    ok = PreviewRequest.from_input({"project": "p", "bundle_key": "bundles/p.tar.gz"})
    assert ok.validate() is None
    assert ok.quality_tier == "final"


def test_preview_request_clamps_tier_and_shots():
    req = PreviewRequest.from_input(
        {
            "project": "My Film",
            "bundle_key": "bundles/x.tar.gz",
            "quality_tier": "DRAFT",
            "process_shot_ids": ["shot_01", "", 3],
            "pretrained_loras": {"A": "loras/a.safetensors"},
        }
    )
    assert req.quality_tier == "draft"
    assert req.process_shot_ids == ["shot_01"]
    assert req.pretrained_loras == {"A": "loras/a.safetensors"}


def test_tier_params_defaults_and_overrides():
    d = tier_params("draft")
    assert d.few_step and d.steps == 4
    f = tier_params("final", {"keyframe": {"width": 1024, "height": 1024, "steps": 20}})
    assert f.width == 1024 and f.height == 1024 and f.steps == 20 and not f.few_step


def test_plan_shots_scopes():
    sb = storyboard_from_dict(
        {"title": "t", "scenes": [{"id": "a", "prompt": "p1"}, {"id": "b", "prompt": "p2"}]}
    )

    class B:
        storyboard = sb

    assert plan_shots(B(), None) == ["a", "b"]
    assert plan_shots(B(), ["b"]) == ["b"]


def test_keyframe_key_slug_matches_i2v_convention():
    assert keyframe_key_for("My Film", "shot 01") == "renders/My_Film/keyframes/shot_01.png"


def _bundle_tar(tmp: Path) -> Path:
    root = tmp / "proj"
    (root / "characters" / "refs" / "A").mkdir(parents=True)
    (root / "storyboard.yaml").write_text(
        "title: demo\nstyle_prefix: cinematic,\nscenes:\n"
        "  - id: shot_01\n    prompt: hero walks\n    character_slots: [A]\n",
        encoding="utf-8",
    )
    (root / "characters" / "registry.json").write_text(
        json.dumps({"characters": {"A": {"name": "Ada", "prompt": "woman"}}}),
        encoding="utf-8",
    )
    # minimal PNG header-ish bytes are fine for extract; no image decode here
    (root / "characters" / "refs" / "A" / "ref.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    tar_path = tmp / "bundle.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for p in root.rglob("*"):
            if p.is_file():
                tf.add(p, arcname=str(p.relative_to(root)))
    return tar_path


def test_extract_bundle_and_prompt(tmp_path: Path):
    tar = _bundle_tar(tmp_path)
    dest = tmp_path / "out"
    bundle = extract_bundle(tar, dest)
    assert len(bundle.storyboard.scenes) == 1
    assert bundle.cast.characters["A"].name == "Ada"
    assert bundle.cast.characters["A"].ref_paths
    prompt = build_prompt(bundle.storyboard.scenes[0], bundle.cast, bundle.storyboard)
    assert "hero walks" in prompt and "Ada" in prompt and "cinematic" in prompt
