"""The job contract between the local-gpu module worker and this backend.

Deliberately IDENTICAL to vivijure-backend's i2v_clip action shape, because that sameness is the whole
point: the same control plane and the same `buildI2vBody` drive either door. The module POSTs
`{ "input": { action, project, shot_id, prompt, keyframe_key?, config } }` to /run; this backend
writes the clip to the shared R2 bucket and returns a pointer-only result (the clip_key), exactly as
the datacenter handler does. Parsing is forgiving (unknown keys ignored), so the control plane can add
authored fields without breaking an older backend.

HARD INVARIANT (#129): the request/result shape + the two R2 key templates below are the single
`i2v_clip` wire contract, locked against drift by `tests/fixtures/i2v_clip_contract.json`
(byte-identical across this door, the sibling door, and vivijure-backend) and asserted by
`tests/test_i2v_clip_conformance.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field


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
