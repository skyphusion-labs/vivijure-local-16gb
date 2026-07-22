"""Local-door SDXL keyframe preview (vivijure-local#153).

Draws start keyframes on the homelab card so `motion_backend: local-gpu` never depends on
RunPod vivijure-backend for the keyframe phase. v1 scope:

  - RealVisXL + Hyper-SD few-step path (draft/standard); fuller steps on final
  - Per-shot prompts from the project bundle (style + scene + cast name triggers)
  - Optional IP-Adapter identity from the first cast ref image (no DreamBooth train in v1)
  - Optional pretrained LoRA adapters staged from R2 (`pretrained_loras`)
  - Multi-character InstantID / regional anti-bleed deferred (prompt + first-slot IP-Adapter)

Heavy imports (torch / diffusers / PIL) are deferred so CPU tests cover planning + bundle extract.
The process caches one SDXL pipe; call `unload_preview()` before i2v (and unload i2v before preview)
so a single consumer card can host both stages serially.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .core.bundle import Bundle, build_prompt, extract_bundle
from .core.contract import PreviewRequest, keyframe_key_for

DEFAULT_MODEL = os.environ.get("VIVIJURE_KEYFRAME_MODEL", "SG161222/RealVisXL_V5.0")
DISTILL_REPO = os.environ.get("VIVIJURE_KEYFRAME_DISTILL_REPO", "ByteDance/Hyper-SD")
DISTILL_WEIGHT = os.environ.get(
    "VIVIJURE_KEYFRAME_DISTILL_WEIGHT", "Hyper-SDXL-8steps-CFG-lora.safetensors"
)
IP_ADAPTER_REPO = "h94/IP-Adapter"
IP_ADAPTER_SUBFOLDER = "sdxl_models"
IP_ADAPTER_WEIGHT = "ip-adapter_sdxl.bin"
DEFAULT_NEGATIVE = (
    "lowres, bad anatomy, extra limbs, fused faces, two heads, deformed, blurry, watermark, text"
)

_PIPE = None  # process-cached SDXL pipe (or None after unload)


@dataclass(frozen=True)
class PreviewTier:
    steps: int
    guidance: float
    width: int
    height: int
    few_step: bool


def tier_params(quality_tier: str, overrides: dict | None = None) -> PreviewTier:
    """Map quality_tier (+ optional render_overrides.keyframe) onto engine knobs."""
    t = (quality_tier or "final").strip().lower()
    if t == "draft":
        base = PreviewTier(steps=4, guidance=1.0, width=1024, height=576, few_step=True)
    elif t == "standard":
        base = PreviewTier(steps=8, guidance=1.0, width=1344, height=768, few_step=True)
    else:
        base = PreviewTier(steps=30, guidance=6.5, width=1344, height=768, few_step=False)

    kf = (overrides or {}).get("keyframe") if isinstance(overrides, dict) else None
    if not isinstance(kf, dict):
        return base

    def _int(key: str, default: int) -> int:
        v = kf.get(key)
        return int(v) if isinstance(v, (int, float)) and v > 0 else default

    def _float(key: str, default: float) -> float:
        v = kf.get(key)
        return float(v) if isinstance(v, (int, float)) else default

    return PreviewTier(
        steps=_int("steps", base.steps),
        guidance=_float("guidance_scale", base.guidance),
        width=_int("width", base.width),
        height=_int("height", base.height),
        few_step=base.few_step,
    )


def plan_shots(bundle: Bundle, process_shot_ids: list[str] | None) -> list[str]:
    """Ordered shot ids to render; honors process_shot_ids scope when provided."""
    all_ids = [s.id for s in bundle.storyboard.scenes]
    if not process_shot_ids:
        return all_ids
    wanted = set(process_shot_ids)
    return [s for s in all_ids if s in wanted]


def unload_preview() -> None:
    """Drop the cached SDXL pipe and free CUDA so i2v can claim the card."""
    global _PIPE
    _PIPE = None
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _unload_i2v() -> None:
    """Best-effort: ask the door engine to drop its resident i2v weights."""
    try:
        from . import door

        unload = getattr(door, "unload_i2v", None)
        if callable(unload):
            unload()
    except Exception:
        pass


def _get_pipe(few_step: bool):
    """Return a process-cached SDXL pipeline configured for the card."""
    global _PIPE
    import torch
    from diffusers import StableDiffusionXLPipeline

    if _PIPE is not None:
        return _PIPE

    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
    pipe = StableDiffusionXLPipeline.from_pretrained(DEFAULT_MODEL, torch_dtype=dtype)
    if few_step:
        try:
            pipe.load_lora_weights(DISTILL_REPO, weight_name=DISTILL_WEIGHT, adapter_name="distill")
            pipe.set_adapters(["distill"], adapter_weights=[1.0])
        except Exception as e:  # noqa: BLE001
            print(f"preview_sdxl: Hyper-SD load failed ({e}); full-step only", flush=True)
    try:
        pipe.load_ip_adapter(
            IP_ADAPTER_REPO,
            subfolder=IP_ADAPTER_SUBFOLDER,
            weight_name=IP_ADAPTER_WEIGHT,
        )
        pipe.set_ip_adapter_scale(0.7)
    except Exception as e:  # noqa: BLE001
        print(f"preview_sdxl: IP-Adapter load failed ({e}); prompt-only identity", flush=True)

    if torch.cuda.is_available():
        # sequential offload keeps RealVisXL under a 12GB ceiling alongside residual allocator state
        try:
            pipe.enable_model_cpu_offload()
        except Exception:
            pipe.to("cuda")
    _PIPE = pipe
    return pipe


def _bind_pretrained_loras(pipe, staged: dict[str, Path]) -> list[str]:
    """Attach staged LoRA adapters; returns adapter names to deactivate after the shot."""
    names: list[str] = []
    for slot, path in staged.items():
        name = f"char_{slot}"
        try:
            pipe.load_lora_weights(str(path.parent), weight_name=path.name, adapter_name=name)
            names.append(name)
        except Exception as e:  # noqa: BLE001
            print(f"preview_sdxl: LoRA {slot} load failed ({e})", flush=True)
    if names:
        try:
            # keep distill if present
            active = list(getattr(pipe, "get_active_adapters", lambda: [])() or [])
            weights = [1.0] * len(active)
            for n in names:
                if n not in active:
                    active.append(n)
                    weights.append(0.6)
            pipe.set_adapters(active, adapter_weights=weights)
        except Exception as e:  # noqa: BLE001
            print(f"preview_sdxl: set_adapters failed ({e})", flush=True)
    return names


def _stage_pretrained_loras(req: PreviewRequest, store, workdir: Path) -> dict[str, Path]:
    staged: dict[str, Path] = {}
    for slot, ref in req.pretrained_loras.items():
        if Path(ref).is_file():
            staged[slot] = Path(ref)
            continue
        dest = workdir / "pretrained" / slot / (Path(ref).name or "pytorch_lora_weights.safetensors")
        dest.parent.mkdir(parents=True, exist_ok=True)
        store.get_file(ref, dest)
        staged[slot] = dest
    return staged


def _first_ref_image(bundle: Bundle, scene) -> Path | None:
    for slot in scene.character_slots:
        char = bundle.cast.characters.get(slot)
        if char and char.ref_paths:
            return char.ref_paths[0]
    # fall back to any cast ref so a no-slot scene still gets identity when possible
    for char in bundle.cast.characters.values():
        if char.ref_paths:
            return char.ref_paths[0]
    return None


def render_preview(
    req: PreviewRequest,
    store,
    workdir: Path,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Fetch the bundle, draw keyframes, upload PNGs, return pointer-only result.

    Result shape matches vivijure-backend preview / the module's parseKeyframes:
      { project, keyframes: [{shot_id, key}, ...], lora?: {slot: {lora_id}} }
    """
    from PIL import Image

    _unload_i2v()
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    tar = store.get_file(req.bundle_key, workdir / "bundle.tar.gz")
    bundle = extract_bundle(Path(tar), workdir / "project")
    shot_ids = plan_shots(bundle, req.process_shot_ids)
    if not shot_ids:
        raise ValueError("preview: no scenes in scope to keyframe")

    params = tier_params(req.quality_tier, req.render_overrides)
    staged = _stage_pretrained_loras(req, store, workdir)
    scenes_by_id = {s.id: s for s in bundle.storyboard.scenes}

    print(
        "vivijure-local: preview job -- loading SDXL keyframe weights (cold box may download "
        f"{DEFAULT_MODEL}; this is NOT a hang).",
        flush=True,
    )
    pipe = _get_pipe(params.few_step)
    lora_names = _bind_pretrained_loras(pipe, staged)

    seed = 0
    kf_overrides = req.render_overrides.get("keyframe") if isinstance(req.render_overrides, dict) else None
    if isinstance(kf_overrides, dict) and isinstance(kf_overrides.get("seed"), (int, float)) and kf_overrides["seed"] >= 0:
        seed = int(kf_overrides["seed"])

    import torch

    keyframes: list[dict[str, str]] = []
    total = len(shot_ids)
    for i, shot_id in enumerate(shot_ids):
        if should_cancel and should_cancel():
            raise RuntimeError("preview cancelled")
        scene = scenes_by_id[shot_id]
        # Injected / authored start image: copy through without SDXL spend.
        if scene.start_image:
            src = bundle.root / scene.start_image
            if src.is_file():
                out_path = workdir / "keyframes" / f"{shot_id}.png"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                Image.open(src).convert("RGB").save(out_path)
                key = keyframe_key_for(req.project, shot_id)
                store.put_file(out_path, key, content_type="image/png")
                keyframes.append({"shot_id": shot_id, "key": key})
                if on_progress:
                    on_progress(i + 1, total)
                continue

        prompt = build_prompt(scene, bundle.cast, bundle.storyboard)
        kwargs: dict = {
            "prompt": prompt,
            "negative_prompt": DEFAULT_NEGATIVE,
            "num_inference_steps": params.steps,
            "guidance_scale": params.guidance,
            "width": params.width,
            "height": params.height,
            "generator": torch.Generator(device="cpu").manual_seed(seed + i),
        }
        ref = _first_ref_image(bundle, scene)
        if ref is not None and hasattr(pipe, "load_ip_adapter"):
            try:
                kwargs["ip_adapter_image"] = Image.open(ref).convert("RGB")
            except Exception:
                pass

        image = pipe(**kwargs).images[0]
        out_path = workdir / "keyframes" / f"{shot_id}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(out_path)
        key = keyframe_key_for(req.project, shot_id)
        store.put_file(out_path, key, content_type="image/png")
        keyframes.append({"shot_id": shot_id, "key": key})
        if on_progress:
            on_progress(i + 1, total)

    # drop character adapters so the next job starts clean; keep distill if any
    if lora_names:
        try:
            pipe.delete_adapters(lora_names)
        except Exception:
            pass

    result: dict = {"project": req.project, "keyframes": keyframes}
    if req.pretrained_loras:
        result["lora"] = {slot: {"lora_id": ref} for slot, ref in req.pretrained_loras.items()}
    return result
