"""Minimal project-bundle extract for the local door's `preview` action (vivijure-local#153).

Mirrors the load-bearing shapes of vivijure-backend's Bundle/Storyboard/Cast enough to draw
keyframes from `storyboard.yaml` + cast refs. Kept in `core` so both doors share one extract path.
Pure enough for CPU tests (no torch); PyYAML is a small runtime dep (see requirements.txt).
"""
from __future__ import annotations

import json
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SLOT_IDS = ("A", "B", "C", "D")


def _str(v: Any, default: str = "") -> str:
    return v if isinstance(v, str) else default


@dataclass
class Scene:
    prompt: str
    id: str
    character_slots: list[str] = field(default_factory=list)
    start_image: str | None = None


@dataclass
class Storyboard:
    title: str
    scenes: list[Scene]
    style_prefix: str = ""
    style_preset: str = "None"
    refs_dir: str | None = None


@dataclass
class Character:
    slot: str
    name: str
    prompt: str
    ref_paths: list[Path] = field(default_factory=list)


@dataclass
class Cast:
    characters: dict[str, Character]


@dataclass
class Bundle:
    root: Path
    storyboard: Storyboard
    cast: Cast


def _scene_from_dict(d: dict[str, Any], index: int) -> Scene:
    slots = [s for s in (d.get("character_slots") or []) if s in SLOT_IDS]
    return Scene(
        prompt=_str(d.get("prompt")),
        id=_str(d.get("id")) or f"shot_{index + 1:02d}",
        character_slots=slots,
        start_image=(_str(d["start_image"]) or None) if "start_image" in d else None,
    )


def storyboard_from_dict(d: dict[str, Any]) -> Storyboard:
    scenes = [_scene_from_dict(s, i) for i, s in enumerate(d.get("scenes") or []) if isinstance(s, dict)]
    if not scenes:
        raise ValueError("storyboard has no scenes")
    preset = _str(d.get("style_preset")).strip() or "None"
    return Storyboard(
        title=_str(d.get("title"), "untitled"),
        scenes=scenes,
        style_prefix=_str(d.get("style_prefix")),
        style_preset=preset,
        refs_dir=(_str(d["refs_dir"]) or None) if "refs_dir" in d else None,
    )


def cast_from_registry(registry: dict[str, Any]) -> Cast:
    raw = registry.get("characters") or {}
    out: dict[str, Character] = {}
    for slot, c in raw.items():
        if slot not in SLOT_IDS or not isinstance(c, dict):
            continue
        out[slot] = Character(slot=slot, name=_str(c.get("name"), slot), prompt=_str(c.get("prompt")))
    return Cast(characters=out)


def _safe_tar_member_name(name: str) -> None:
    """Reject absolute paths, traversal, backslashes, and empty segments in tar member names."""
    if not name or name != name.strip():
        raise ValueError(f"unsafe path in bundle: {name!r}")
    if name.startswith(("/", "\\")) or (len(name) >= 2 and name[1] == ":"):
        raise ValueError(f"unsafe path in bundle: {name}")
    if "\\" in name:
        raise ValueError(f"unsafe path in bundle: {name}")
    parts = name.split("/")
    if any(p in ("", "..") for p in parts):
        raise ValueError(f"unsafe path in bundle: {name}")


def _safe_extract(tf: tarfile.TarFile, dest: Path) -> None:
    dest = dest.resolve()
    for member in tf.getmembers():
        _safe_tar_member_name(member.name)
        if member.issym() or member.islnk():
            raise ValueError(f"unsafe link in bundle: {member.name}")
        target = (dest / member.name).resolve()
        if not target.is_relative_to(dest):
            raise ValueError(f"unsafe path in bundle: {member.name}")
    tf.extractall(dest, filter="data")


def extract_bundle(tar_path: Path, dest: Path) -> Bundle:
    """Extract a control-plane project bundle and resolve cast reference images."""
    import yaml  # deferred: keep import light for modules that only need types

    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:gz") as tf:
        _safe_extract(tf, dest)

    sb_path = dest / "storyboard.yaml"
    if not sb_path.is_file():
        raise FileNotFoundError(f"bundle is missing storyboard.yaml at {sb_path}")
    storyboard = storyboard_from_dict(yaml.safe_load(sb_path.read_text(encoding="utf-8")) or {})

    reg_path = dest / "characters" / "registry.json"
    cast = cast_from_registry(
        json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.is_file() else {}
    )

    refs_root = dest / (storyboard.refs_dir or "characters/refs")
    for slot, char in cast.characters.items():
        slot_dir = refs_root / slot
        if slot_dir.is_dir():
            char.ref_paths = sorted(
                p for p in slot_dir.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
            )

    return Bundle(root=dest, storyboard=storyboard, cast=cast)


def build_prompt(scene: Scene, cast: Cast, storyboard: Storyboard) -> str:
    """Compose an SDXL prompt: style prefix + scene prompt + character name triggers."""
    triggers = ", ".join(
        (cast.characters[s].name if s in cast.characters and cast.characters[s].name else s)
        for s in scene.character_slots
    )
    parts = [storyboard.style_prefix, scene.prompt, triggers]
    if storyboard.style_preset and storyboard.style_preset != "None":
        parts.append(storyboard.style_preset)
    cleaned = [c for c in (p.strip().strip(",").strip() for p in parts if p) if c]
    return ", ".join(cleaned)
