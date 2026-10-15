"""Local-door `action: train_lora` job body: bundle -> SDXL cast adapters -> R2.

Unloads preview + i2v first so a single consumer card can host train exclusively, then fits one
adapter per cast slot that has reference images. Result shape matches the cast harvest path:
  { project, lora: { slot: { lora_id } } }
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .core.bundle import extract_bundle
from .core.contract import TrainLoraRequest, lora_key_for
from .lora_train import config_from_overrides, train_slot


def _unload_card() -> None:
    """Drop resident preview + i2v weights so train owns the VRAM budget."""
    try:
        from . import preview_sdxl
        preview_sdxl.unload_preview()
    except Exception:
        pass
    try:
        from . import door
        unload = getattr(door, "unload_i2v", None)
        if callable(unload):
            unload()
    except Exception:
        pass


def run_train_lora(
    req: TrainLoraRequest,
    store,
    workdir: Path,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Fetch the cast-train bundle, fit SDXL LoRAs, upload, return pointer-only result."""
    _unload_card()
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    tar = store.get_file(req.bundle_key, workdir / "bundle.tar.gz")
    bundle = extract_bundle(Path(tar), workdir / "project")

    characters = [
        c for c in bundle.cast.characters.values() if c.ref_paths
    ]
    if not characters:
        raise ValueError("train_lora: no cast slots with reference images in the bundle")

    lora_section = req.render_overrides.get("lora") if isinstance(req.render_overrides, dict) else None
    cfg = config_from_overrides(lora_section if isinstance(lora_section, dict) else None)

    result_lora: dict[str, dict[str, str]] = {}
    total = len(characters)
    for i, char in enumerate(characters):
        if should_cancel and should_cancel():
            raise RuntimeError("train_lora cancelled")
        out_dir = workdir / "loras" / char.slot
        out_dir.mkdir(parents=True, exist_ok=True)

        def progress_cb(step: int, total_steps: int, _loss: float = 0.0) -> None:
            if on_progress is not None:
                # Map per-slot step into a coarse overall bar: slot i of total + in-slot fraction.
                try:
                    overall = int(((i + (step / max(total_steps, 1))) / total) * 1000)
                    on_progress(overall, 1000)
                except Exception:
                    pass
            if should_cancel and should_cancel():
                raise RuntimeError("train_lora cancelled")

        print(
            f"vivijure-local: train_lora slot={char.slot!r} refs={len(char.ref_paths)} "
            f"rank={cfg.rank} steps_cap={cfg.max_steps} (SDXL on this card).",
            flush=True,
        )
        trained = train_slot(char, out_dir, config=cfg, progress_cb=progress_cb)
        key = lora_key_for(req.project, char.slot)
        store.put_file(trained.path, key, content_type="application/octet-stream")
        result_lora[char.slot] = {"lora_id": key}
        if on_progress is not None:
            on_progress(int(((i + 1) / total) * 1000), 1000)

    return {"project": req.project, "lora": result_lora}
