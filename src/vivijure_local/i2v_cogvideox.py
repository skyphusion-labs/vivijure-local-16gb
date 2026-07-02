"""Image-to-video on a consumer card: CogVideoX-5B-I2V.

The local door's engine, sibling to the LTX door (vivijure-local-12gb). The keyframe is the still; this
turns it into motion. CogVideoX-5B-I2V takes the keyframe as the first (conditioning) frame and the
scene prompt as the motion description and produces N frames. CogVideoX was chosen as the second local
motion backend for FIDELITY: it is the quality leader for true i2v on a consumer card (strong
first-frame identity + coherent motion + real text control), the honest trade against LTX's speed (the
full comparison is docs/i2v-model-selection.md). A self-hoster points LOCAL_BACKEND_URL at whichever
container fits their card + patience.

Clean-room: built from diffusers' CogVideoXImageToVideoPipeline + export_to_video and the CogVideoX-5B
model card's own constraints (num_frames a 4k+1 count capped at 49, the model's native 720x480 grid),
not from any prior pipeline. The frame-count / dimension math and the tier->engine mapping are PURE and
CPU-tested; the generation body defers torch/diffusers and is validated on the card (mirroring the LTX
door's i2v_ltx.animate + vivijure-backend's i2v.animate). Engine knobs come from `config.I2VConfig`.

NOTE ON RESOLUTION: unlike LTX (which scales resolution per tier), CogVideoX-5B-I2V is trained at a
FIXED 720x480 grid and degrades badly off it, so the tiers here differ by inference STEPS and frame
COUNT (speed vs fidelity), NOT resolution. That is an honest property of the model, mapped honestly in
config.py -- not a bug in the scaffold.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import I2VConfig, Offload

# CogVideoX's temporal VAE compresses time by 4, so a clip's latent grid needs (num_frames - 1)
# divisible by 4; the 5B-I2V model is trained/validated at 49 frames (= 4*12+1, ~6s at 8 fps) and caps
# there. Spatial dims are the native 720x480 grid; the VAE spatial compression is 8 and the transformer
# patch is 2, so a dim must be divisible by 16.
TEMPORAL_STRIDE = 4
SPATIAL_MULTIPLE = 16
MAX_FRAMES = 49
DEFAULT_FPS = 8


# --------------------------------------------------------------------------- pure helpers

def snap_frames(n: int, max_frames: int = MAX_FRAMES) -> int:
    """Snap a frame count to the nearest valid 4k+1 the CogVideoX temporal VAE accepts (rounding UP so a
    clip never comes out shorter than asked), clamped to [1, max_frames].

    snap-then-clamp so the result is always 4k+1 even after the ceiling applies: if rounding up would
    exceed max_frames, step down to the largest 4k+1 <= max_frames."""
    n = max(1, int(n))
    rem = (n - 1) % TEMPORAL_STRIDE
    snapped = n if rem == 0 else n + (TEMPORAL_STRIDE - rem)
    if snapped <= max_frames:
        return snapped
    prev = max_frames - ((max_frames - 1) % TEMPORAL_STRIDE)
    return max(1, prev)


def snap_dim(px: int) -> int:
    """Snap a spatial dimension DOWN to a multiple of 16 (never up, so a clamped tier ceiling stays a
    ceiling), with a floor of 16."""
    return max(SPATIAL_MULTIPLE, (int(px) // SPATIAL_MULTIPLE) * SPATIAL_MULTIPLE)


def frames_for(target_seconds: float | None, fps: int = DEFAULT_FPS, *, max_frames: int = MAX_FRAMES) -> int:
    """Frame count for a target duration at `fps`: snap to 4k+1 and cap at the ceiling. Falls back to
    the ceiling when no target is given."""
    if not target_seconds or target_seconds <= 0:
        return snap_frames(max_frames, max_frames)
    return snap_frames(round(target_seconds * fps), max_frames)


def clip_seconds(num_frames: int, fps: int = DEFAULT_FPS) -> float:
    """The realized clip length. i2v fixes the first frame to the keyframe, so N frames play as N/fps
    seconds."""
    return round(num_frames / max(1, fps), 3)


def resolve_engine_dims(cfg: I2VConfig) -> tuple[int, int, int]:
    """The (width, height, num_frames) actually fed to the pipeline: tier dims snapped to /16 and the
    frame count snapped to 4k+1 under the tier ceiling. Pure, so the server can report the realized
    shape before any GPU work."""
    return snap_dim(cfg.width), snap_dim(cfg.height), snap_frames(cfg.num_frames)


# --------------------------------------------------------------------------- result

@dataclass
class I2VResult:
    """The outcome of animating one keyframe: where the clip landed, its frame count / fps / length,
    and whether the few-step distilled path produced it (always False for CogVideoX-5B-I2V, which is a
    full-step diffusion model, but the field is kept for output-shape parity with the LTX door)."""

    shot_id: str
    path: Path
    num_frames: int
    fps: int
    seconds: float
    distilled: bool


# --------------------------------------------------------------------------- pipeline cache (process-lifetime)

# One offload-configured pipeline per (model, offload, vae_tiling), built once per process and reused.
# The datacenter backend scales to zero between jobs; the local box is always-on and serial (ONE job at a
# time -- a consumer card cannot fit two i2v pipelines), so the honest optimisation is the opposite: keep
# the resident pipeline warm instead of re-reading the full weights (~30s + a full disk/VRAM reload) every
# clip. The registry is single-worker serial (jobs.py), so no lock is needed.
_PIPE_CACHE: dict = {}


def _pipe_cache_key(cfg: I2VConfig):
    """The cache identity: two jobs share a pipeline only if the model AND the offload wiring match.
    Offload hooks mutate the pipeline in place at build, so a pipe configured for one offload/tiling mode
    must not be reused under another."""
    return (cfg.model, cfg.offload.value, bool(cfg.vae_tiling))


def _get_pipe(cfg: I2VConfig, pipeline_cls, torch):
    """Return the process-cached, offload-configured pipeline for `cfg`, building it once on a miss.

    The heavy from_pretrained + `_apply_offload` runs ONCE per key; subsequent jobs reuse the fully
    configured resident pipeline. Offload is applied only at build (re-applying per job is redundant and
    can double-wrap the diffusers hooks), so the cache stores the ready-to-run pipe."""
    key = _pipe_cache_key(cfg)
    pipe = _PIPE_CACHE.get(key)
    if pipe is None:
        pipe = pipeline_cls.from_pretrained(cfg.model, torch_dtype=torch.bfloat16)
        _apply_offload(pipe, cfg)
        _PIPE_CACHE[key] = pipe
    return pipe


def _evict_pipe(cfg: I2VConfig, torch) -> None:
    """Drop the cached pipeline for `cfg` after a failed render and release its VRAM. A failed generate
    can leave the pipeline or the CUDA allocator in a bad state; the honest recovery is to rebuild fresh
    on the next job rather than reuse a poisoned pipe. The free is EXPLICIT here, not GC-timing-dependent
    -- the consumer-VRAM budget is thin, too thin to wait on the collector (docs/proof/RESULTS.md)."""
    _PIPE_CACHE.pop(_pipe_cache_key(cfg), None)
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


# --------------------------------------------------------------------------- animate (GPU, deferred)

def animate(shot_id: str, keyframe: Path, prompt: str, cfg: I2VConfig, out_path: Path, *, progress_cb=None) -> I2VResult:
    """Animate `keyframe` into a clip at `out_path` for one shot, on the local card.

    Heavy imports (torch / diffusers) are DEFERRED so this module stays CPU-importable and the pure
    helpers above test without a GPU; this body is validated on the card (the spend gate -- see
    docs/live-benchmark-plan.md). The offload-configured pipeline is process-cached (`_get_pipe`) so only
    the FIRST job on a warm box pays the weights load; VAE tiling AND slicing (CogVideoX's big
    consumer-VRAM savers on the decode) are applied once at build. `progress_cb(step, total)` is wired
    best-effort through diffusers' callback hook. On a failed generate the pipeline is evicted and its
    VRAM freed (`_evict_pipe`) so a poisoned pipe never carries into the next job.

    VALIDATED on the card (docs/proof/RESULTS.md, diffusers 0.32.2): the 16GB floor and per-tier
    speeds are measured with this exact call shape and offload wiring. It raises if torch/diffusers is
    absent rather than pretending to render (a producer stage never fakes output)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import torch  # deferred: keep this module CPU-importable
        from diffusers import CogVideoXImageToVideoPipeline
        from diffusers.utils import export_to_video, load_image
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"i2v_cogvideox.animate requires torch + diffusers (CogVideoXImageToVideoPipeline): {e}. "
            "This is the GPU body; install the card runtime (requirements.txt) or run the pure helpers."
        ) from e

    width, height, num_frames = resolve_engine_dims(cfg)
    image = load_image(str(keyframe))

    pipe = _get_pipe(cfg, CogVideoXImageToVideoPipeline, torch)

    seed = cfg.seed if cfg.seed >= 0 else 0
    generator = torch.Generator(device="cpu").manual_seed(seed)
    step_callback = _step_callback(progress_cb, cfg.steps)

    try:
        result_frames = pipe(
            image=image,
            prompt=prompt,
            negative_prompt=cfg.negative_prompt or "worst quality, blurry, jittery, distorted",
            width=width,
            height=height,
            num_frames=num_frames,
            num_inference_steps=cfg.steps,
            guidance_scale=cfg.guidance_scale,
            num_videos_per_prompt=1,
            generator=generator,
            **({"callback_on_step_end": step_callback} if step_callback else {}),
        ).frames[0]
    except Exception:
        _evict_pipe(cfg, torch)  # a cancel or an OOM must not leave a poisoned pipe cached
        raise

    export_to_video(result_frames, str(out_path), fps=cfg.fps)
    return I2VResult(
        shot_id=shot_id or "shot", path=out_path, num_frames=num_frames, fps=cfg.fps,
        seconds=clip_seconds(num_frames, cfg.fps), distilled=False,
    )


def _apply_offload(pipe, cfg: I2VConfig) -> None:
    """Apply the config's VRAM strategy to the pipeline so the run fits a consumer card. CogVideoX's
    decode is the peak-VRAM spike, so VAE tiling AND slicing are both enabled (best-effort per hook: a
    diffusers build lacking one runs without it rather than failing the render)."""
    if cfg.vae_tiling:
        vae = getattr(pipe, "vae", None)
        for target, fn in ((pipe, "enable_vae_tiling"), (vae, "enable_tiling"),
                            (pipe, "enable_vae_slicing"), (vae, "enable_slicing")):
            hook = getattr(target, fn, None) if target is not None else None
            if callable(hook):
                try:
                    hook()
                except Exception:
                    pass
    if cfg.offload is Offload.SEQUENTIAL_CPU_OFFLOAD:
        _try(pipe, "enable_sequential_cpu_offload")
    elif cfg.offload is Offload.MODEL_CPU_OFFLOAD:
        _try(pipe, "enable_model_cpu_offload")
    else:
        _try(pipe, "to", "cuda")


def _try(obj, name: str, *args) -> None:
    hook = getattr(obj, name, None)
    if callable(hook):
        try:
            hook(*args)
        except Exception:
            pass


def _step_callback(progress_cb, total: int):
    """Wrap a `(step, total)` callback in diffusers' callback_on_step_end signature. Returns None when
    there is no callback (zero overhead). Best-effort: a progress failure never breaks the denoise."""
    if progress_cb is None:
        return None

    def on_step_end(pipe, step_index, timestep, callback_kwargs):
        try:
            progress_cb(step_index + 1, total)
        except Exception:
            pass
        return callback_kwargs

    return on_step_end
