"""CPU tests for the local-door preview (keyframe) contract (#153)."""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from vivijure_local.core.bundle import _safe_extract, build_prompt, extract_bundle, storyboard_from_dict
from vivijure_local.core.contract import PreviewRequest, is_safe_bundle_key, is_safe_lora_key, keyframe_key_for
from vivijure_local.preview_sdxl import (
    _ALLOWED_KEYFRAME_MODELS,
    _ensure_ip_adapter,
    _env_allowlisted,
    _stage_pretrained_loras,
    plan_shots,
    render_preview,
    tier_params,
)


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


def test_tier_params_clamps_keyframe_overrides():
    p = tier_params(
        "final",
        {"keyframe": {"width": 999999, "height": 1, "steps": 999, "guidance_scale": 99}},
    )
    assert p.width == 1344
    assert p.height == 256
    assert p.steps == 50
    assert p.guidance == 15.0


def test_preview_request_rejects_invalid_render_overrides():
    req = PreviewRequest.from_input(
        {
            "project": "p",
            "bundle_key": "bundles/p.tar.gz",
            "render_overrides": {"keyframe": {"width": "huge"}},
        }
    )
    assert "finite number" in req.validate()


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


def _bundle_tar(tmp: Path, *, start_image: str | None = None) -> Path:
    root = tmp / "proj"
    (root / "characters" / "refs" / "A").mkdir(parents=True)
    start = f"    start_image: {start_image}\n" if start_image else ""
    (root / "storyboard.yaml").write_text(
        "title: demo\nstyle_prefix: cinematic,\nscenes:\n"
        "  - id: shot_01\n    prompt: hero walks\n    character_slots: [A]\n"
        f"{start}",
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


def test_extract_bundle_clears_existing_destination(tmp_path: Path):
    tar = _bundle_tar(tmp_path)
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "stale.txt").write_text("old", encoding="utf-8")
    extract_bundle(tar, dest)
    assert not (dest / "stale.txt").exists()
    assert (dest / "storyboard.yaml").is_file()


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "bundles",
        "/bundles/x.tar.gz",
        "bundles/../etc/passwd",
        "bundles//x.tar.gz",
        "bundles\\x.tar.gz",
        "renders/x.tar.gz",
        " bundles/x.tar.gz",
    ],
)
def test_preview_rejects_unsafe_bundle_keys(bad: str):
    req = PreviewRequest.from_input({"project": "p", "bundle_key": bad})
    assert req.validate() is not None
    assert not is_safe_bundle_key(bad)


@pytest.mark.parametrize(
    "good",
    [
        "bundles/p.tar.gz",
        "bundles/my-project/abc123.tar.gz",
    ],
)
def test_preview_accepts_safe_bundle_keys(good: str):
    req = PreviewRequest.from_input({"project": "p", "bundle_key": good})
    assert req.validate() is None
    assert is_safe_bundle_key(good)


@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd",
        "bundles/evil.safetensors",
        "../loras/x.safetensors",
        "loras/../secret.safetensors",
    ],
)
def test_preview_rejects_unsafe_pretrained_lora_keys(bad: str):
    req = PreviewRequest.from_input(
        {"project": "p", "bundle_key": "bundles/p.tar.gz", "pretrained_loras": {"A": bad}}
    )
    assert req.validate() is not None
    assert not is_safe_lora_key(bad)


@pytest.mark.parametrize("bad_slot", ["../A", "A/B", "A B", "", "x" * 65])
def test_preview_rejects_unsafe_pretrained_lora_slots(bad_slot: str):
    req = PreviewRequest.from_input(
        {
            "project": "p",
            "bundle_key": "bundles/p.tar.gz",
            "pretrained_loras": {bad_slot: "loras/a.safetensors"},
        }
    )
    assert req.validate() is not None


def test_preview_rejects_too_many_pretrained_loras():
    req = PreviewRequest.from_input(
        {
            "project": "p",
            "bundle_key": "bundles/p.tar.gz",
            "pretrained_loras": {f"S{i}": f"loras/{i}.safetensors" for i in range(5)},
        }
    )
    assert "at most 4" in req.validate()


def test_stage_pretrained_loras_rejects_local_paths(tmp_path: Path):
    local = tmp_path / "evil.safetensors"
    local.write_bytes(b"x")
    req = PreviewRequest.from_input(
        {
            "project": "p",
            "bundle_key": "bundles/p.tar.gz",
            "pretrained_loras": {"A": str(local)},
        }
    )

    class Store:
        def get_file(self, key, dest):
            raise AssertionError("store should not be called for local paths")

    with pytest.raises(ValueError, match="unsafe LoRA key"):
        _stage_pretrained_loras(req, Store(), tmp_path / "work")


def test_stage_pretrained_loras_caps_store_reads(tmp_path: Path):
    req = PreviewRequest.from_input(
        {
            "project": "p",
            "bundle_key": "bundles/p.tar.gz",
            "pretrained_loras": {f"S{i}": f"loras/{i}.safetensors" for i in range(5)},
        }
    )

    class Store:
        def get_file(self, key, dest):
            raise AssertionError("store should not be called after cap failure")

    with pytest.raises(ValueError, match="at most 4"):
        _stage_pretrained_loras(req, Store(), tmp_path / "work")


def test_extract_bundle_rejects_symlink(tmp_path: Path):
    tar_path = tmp_path / "evil.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        info = tarfile.TarInfo("storyboard.yaml")
        data = b"title: t\nscenes:\n  - id: a\n    prompt: p\n"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        sym = tarfile.TarInfo("link.txt")
        sym.type = tarfile.SYMTYPE
        sym.linkname = "/etc/passwd"
        tf.addfile(sym)
    with pytest.raises(ValueError, match="unsafe link"):
        extract_bundle(tar_path, tmp_path / "out")


def test_extract_bundle_rejects_special_members(tmp_path: Path):
    tar_path = tmp_path / "evil.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        dev = tarfile.TarInfo("dev/zero")
        dev.type = tarfile.CHRTYPE
        tf.addfile(dev)
    with tarfile.open(tar_path, "r:gz") as tf:
        with pytest.raises(ValueError, match="unsafe special file"):
            _safe_extract(tf, tmp_path / "out")


def test_extract_bundle_rejects_traversal_member(tmp_path: Path):
    tar_path = tmp_path / "evil.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        info = tarfile.TarInfo("../escape.txt")
        data = b"pwnd"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    with tarfile.open(tar_path, "r:gz") as tf:
        with pytest.raises(ValueError, match="unsafe path"):
            _safe_extract(tf, tmp_path / "out")


def test_render_preview_rejects_unsafe_start_image_before_model_load(tmp_path: Path):
    tar = _bundle_tar(tmp_path, start_image="../outside.png")
    calls = []

    class Store:
        def get_file(self, key, dest):
            calls.append(key)
            return tar

        def put_file(self, src, key, content_type=None):
            raise AssertionError("unsafe start_image should fail before upload")

    req = PreviewRequest.from_input({"project": "p", "bundle_key": "bundles/p.tar.gz"})
    with pytest.raises(ValueError, match="unsafe bundle path"):
        render_preview(req, Store(), tmp_path / "work")
    assert calls == ["bundles/p.tar.gz"]


def test_env_allowlist_rejects_unknown_keyframe_model_override(monkeypatch):
    monkeypatch.setenv("VIVIJURE_KEYFRAME_MODEL", "evil/repo")
    with pytest.raises(ValueError, match="not allowlisted"):
        _env_allowlisted(
            "VIVIJURE_KEYFRAME_MODEL", "SG161222/RealVisXL_V5.0", _ALLOWED_KEYFRAME_MODELS
        )


class _FakePipe:
    def __init__(self):
        self._vj_ip_loaded = 0
        self.loads = 0
        self.unloads = 0
        self.scales: list[float] = []

    def load_ip_adapter(self, *args, **kwargs):
        self.loads += 1
        self._vj_ip_loaded = 1

    def unload_ip_adapter(self):
        self.unloads += 1
        self._vj_ip_loaded = 0

    def set_ip_adapter_scale(self, scale):
        self.scales.append(scale)


def test_ensure_ip_adapter_unloads_for_castless_shot():
    pipe = _FakePipe()
    pipe._vj_ip_loaded = 1
    _ensure_ip_adapter(pipe, 0)
    assert pipe.unloads == 1
    assert pipe._vj_ip_loaded == 0
    assert pipe.loads == 0


def test_ensure_ip_adapter_loads_when_ref_present():
    pipe = _FakePipe()
    _ensure_ip_adapter(pipe, 1)
    assert pipe.loads == 1
    assert pipe._vj_ip_loaded == 1
    assert pipe.scales == [0.7]
