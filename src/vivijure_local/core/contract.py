"""The job contract between the local-gpu module worker and this backend.

Deliberately IDENTICAL to vivijure-backend's i2v_clip / preview action shapes, because that sameness
is the whole point: the same control plane and the same module bodies drive either door. The module
POSTs `{ "input": { action, ... } }` to /run; this backend writes artifacts to the shared R2 bucket
and returns a pointer-only result, exactly as the datacenter handler does. Parsing is forgiving
(unknown keys ignored), so the control plane can add authored fields without breaking an older backend.

HARD INVARIANT (#129): the i2v_clip request/result shape + the two R2 key templates below are the
single `i2v_clip` wire contract, locked against drift by `tests/fixtures/i2v_clip_contract.json`
(byte-identical across this door, the sibling door, and vivijure-backend) and asserted by
`tests/test_i2v_clip_conformance.py`.

`preview` (vivijure-local#153): the local door also serves keyframe generation so a studio with
`motion_backend: local-gpu` never depends on RunPod vivijure-backend for SDXL stills.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_TIERS = frozenset({"draft", "standard", "final"})


def _str(v: object, default: str = "") -> str:
    return v if isinstance(v, str) else default


@dataclass
class I2VClipRequest:
    """One per-shot image-to-video job. `keyframe_key` is optional; when absent the backend applies its
    own `renders/<project>/keyframes/<shot>.png` convention (the single source of truth for where the
    keyframe stage wrote), mirroring the datacenter handler."""

    project: str
    shot_id: str
    prompt: str
    keyframe_key: str | None
    config: dict = field(default_factory=dict)

    @classmethod
    def from_input(cls, payload: dict) -> "I2VClipRequest":
        payload = payload or {}
        return cls(
            project=_str(payload.get("project")) or "untitled",
            shot_id=_str(payload.get("shot_id")) or "shot",
            prompt=_str(payload.get("prompt")),
            keyframe_key=_str(payload.get("keyframe_key")) or None,
            config=payload.get("config") if isinstance(payload.get("config"), dict) else {},
        )

    def validate(self) -> str | None:
        """A malformed job is DATA: return a reason string, never raise. The producer stage needs a
        prompt (the motion description); without it the render is meaningless."""
        if not self.prompt:
            return "i2v_clip: prompt is required (the motion description)"
        return None


@dataclass
class PreviewRequest:
    """One project-level keyframe preview job (action=preview). Mirrors vivijure-backend's preview
    input so the local-gpu module's buildPreviewBody can target this door unchanged."""

    project: str
    bundle_key: str
    quality_tier: str = "final"
    process_shot_ids: list[str] | None = None
    pretrained_loras: dict[str, str] = field(default_factory=dict)
    render_overrides: dict = field(default_factory=dict)

    @classmethod
    def from_input(cls, payload: dict) -> "PreviewRequest":
        payload = payload or {}
        tier = _str(payload.get("quality_tier"), "final").strip().lower() or "final"
        if tier not in _TIERS:
            tier = "final"
        shot_ids = payload.get("process_shot_ids")
        if not isinstance(shot_ids, list):
            shot_ids = None
        else:
            shot_ids = [s for s in shot_ids if isinstance(s, str) and s.strip()]
            if not shot_ids:
                shot_ids = None
        loras = payload.get("pretrained_loras")
        pretrained = (
            {str(k): str(v) for k, v in loras.items() if isinstance(k, str) and isinstance(v, str) and v}
            if isinstance(loras, dict)
            else {}
        )
        overrides = payload.get("render_overrides")
        return cls(
            project=_str(payload.get("project")) or "untitled",
            bundle_key=_str(payload.get("bundle_key")),
            quality_tier=tier,
            process_shot_ids=shot_ids,
            pretrained_loras=pretrained,
            render_overrides=overrides if isinstance(overrides, dict) else {},
        )

    def validate(self) -> str | None:
        if not self.bundle_key:
            return "preview: bundle_key is required (no project bundle to fetch)"
        if not self.bundle_key.startswith("bundles/"):
            return "preview: bundle_key must start with bundles/"
        return None


def keyframe_key_for(project: str, shot_id: str) -> str:
    """The keyframe key convention, shared with the datacenter backend's `keys.keyframe_key`. A safe
    slug (no slashes / spaces) so the key is well-formed."""
    return f"renders/{_safe(project)}/keyframes/{_safe(shot_id)}.png"


def clip_key_for(project: str, shot_id: str) -> str:
    """Where this backend writes the finished clip. Matches the datacenter `_i2v.mp4` suffix so the
    control plane's R2-presence completion check (#141) treats either door's output identically."""
    return f"renders/{_safe(project)}/clips/{_safe(shot_id)}_i2v.mp4"


def _safe(s: str) -> str:
    """Reduce a name to the SAME R2-safe path segment as the datacenter backend keys._slug
    (vivijure-backend), so a project never scatters across two slug spellings of its own name. A
    divergence here is a keyframe 404: the studio wrote 'My_Film' but this door looked under
    'My__Film'. Mirror _slug exactly: strip, collapse ANY whitespace run to one '_', then '/' -> '_'."""
    return "_".join(str(s).strip().split()).replace("/", "_") or "untitled"
