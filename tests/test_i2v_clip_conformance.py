"""Conformance guard (#129): this local-gpu door's `i2v_clip` contract MUST match the shared golden
that the datacenter backend (vivijure-backend) and the sibling door also assert against. The doors are
hand-kept parallel copies with no shared source; the golden is the single reference so none can silently
drift (a drift = the control plane builds a body one door rejects, or a keyframe 404 under a mis-slugged
key). Engine-agnostic + CPU-only: `animate` is faked, so this runs on either door (LTX / CogVideoX)."""
import json
from pathlib import Path
from types import SimpleNamespace

from vivijure_local.core import contract
from vivijure_local.core.contract import I2VClipRequest
from vivijure_local.core.server import build_i2v_run_fn

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "i2v_clip_contract.json").read_text())


class _FakeStore:
    def get_file(self, key, dest):
        Path(dest).write_bytes(b"png")
        return dest

    def put_file(self, src, key, content_type=None):
        return key


def test_slug_rule_matches_golden():
    # Assert the project slug via the PUBLIC key path (not reaching into _safe), for each golden case.
    for name, expected in GOLDEN["slug_examples"].items():
        assert contract.keyframe_key_for(name, "x") == f"renders/{expected}/keyframes/x.png", f"slug({name!r})"


def test_key_templates_match_golden():
    s = GOLDEN["sample"]
    assert contract.keyframe_key_for(s["project"], s["shot_id"]) == s["keyframe_key"]
    assert contract.clip_key_for(s["project"], s["shot_id"]) == s["clip_key"]


def test_request_prompt_required_and_optionals_per_golden():
    assert GOLDEN["request"]["required"] == ["prompt"]
    # prompt required: a missing prompt is a validation reason (DATA, never a raise)
    assert I2VClipRequest.from_input({"project": "p", "shot_id": "s"}).validate() is not None
    # a valid request: keyframe_key optional (None) and config defaults to {}
    req = I2VClipRequest.from_input({"project": "p", "shot_id": "s", "prompt": "move"})
    assert req.validate() is None
    assert req.keyframe_key is None
    assert req.config == {}


def test_result_pointer_fields_match_golden(tmp_path, monkeypatch):
    def fake_animate(shot_id, keyframe, prompt, cfg, out_path, *, progress_cb=None):
        Path(out_path).write_bytes(b"mp4")
        return SimpleNamespace(path=Path(out_path), num_frames=121, fps=24, seconds=5.04, distilled=False)

    # Patch the name the run_fn resolves (core.server.animate), so this is engine-agnostic.
    monkeypatch.setattr("vivijure_local.core.server.animate", fake_animate)
    run = build_i2v_run_fn(_FakeStore(), workdir=tmp_path)
    out = run({"action": "i2v_clip", "project": "neon city", "shot_id": "shot 01",
               "prompt": "camera pushes in", "config": {"quality": "draft"}}, lambda: False)
    assert set(out.keys()) == set(GOLDEN["result_pointer_fields"])
    assert out["clip_key"] == contract.clip_key_for("neon city", "shot 01")
