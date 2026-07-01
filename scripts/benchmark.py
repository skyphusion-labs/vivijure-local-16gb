#!/usr/bin/env python3
"""The PROOF GATE: a live CogVideoX-5B-I2V benchmark on real hardware.

Scripted + ready to FIRE the instant Conrad picks the card (a SECURE RunPod pod -- never community,
a hard rule). DO NOT run it until Conrad says go -- it needs a GPU + downloads weights
(spend/time). On a CPU box it imports + the pure parts run; the generate step raises without torch
(a producer stage never fakes a clip).

What it captures, per quality tier (draft / standard / final):
  - FIT:   peak VRAM (torch.cuda.max_memory_allocated) and whether it OOMs at the provisional consumer-card ceiling.
  - SPEED: wall-clock seconds per clip at the tier's resolution/frame ceiling.
  - QUALITY: a real sample .mp4 written to the output dir to eyeball.
Writes results/benchmark-<host>.md + results/benchmark-<host>.json + the sample clips.

Usage (on the GPU box, after `pip install -r requirements.txt`):
  python scripts/benchmark.py --keyframe path/to/keyframe.png --out results/
  python scripts/benchmark.py --tiers draft,standard --out results/   # subset
  # no --keyframe: a synthetic test keyframe is generated so the run is self-contained.

This intentionally does NOT touch R2 or the job server -- it drives the engine (i2v_cogvideox.animate)
directly so the benchmark measures the MODEL on the CARD, nothing else.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

# Make `vivijure_local` importable when run from the repo root (src/ layout).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vivijure_local.config import I2VConfig, QualityTier  # noqa: E402
from vivijure_local import i2v_cogvideox, vram  # noqa: E402


def synth_keyframe(path: Path, width: int = 720, height: int = 480) -> Path:
    """Write a synthetic keyframe so the benchmark is self-contained when no real one is given."""
    from PIL import Image, ImageDraw  # Pillow ships in the runtime image

    img = Image.new("RGB", (width, height), (18, 22, 30))
    d = ImageDraw.Draw(img)
    for y in range(height):  # a vertical gradient so i2v has real structure to move
        d.line([(0, y), (width, y)], fill=(20 + y % 60, 30 + (y * 2) % 80, 60 + (y * 3) % 120))
    d.ellipse([width * 0.35, height * 0.3, width * 0.65, height * 0.7], fill=(220, 180, 90))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def gpu_name() -> str:
    try:
        import torch

        return torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu (no CUDA)"
    except Exception:
        return "unknown (torch unavailable)"


def run_tier(tier: QualityTier, keyframe: Path, out_dir: Path, prompt: str) -> dict:
    """Benchmark one tier: reset the CUDA peak counter, animate one clip, capture peak VRAM + time."""
    import torch

    cfg = I2VConfig.from_request({"quality": tier.value}, tier=tier)
    w, h, n = i2v_cogvideox.resolve_engine_dims(cfg)
    est = vram.estimate(cfg)
    record: dict = {
        "tier": tier.value, "model": cfg.model, "width": w, "height": h, "frames": n,
        "steps": cfg.steps, "offload": cfg.offload.value, "vae_tiling": cfg.vae_tiling,
        "estimated_peak_gb": est.peak_gb, "estimated_fits_16gb": est.fits,
    }
    out_clip = out_dir / f"sample_{tier.value}.mp4"
    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    try:
        result = i2v_cogvideox.animate(f"bench_{tier.value}", keyframe, prompt, cfg, out_clip)
        elapsed = round(time.time() - t0, 1)
        peak_gb = round(torch.cuda.max_memory_allocated() / 1e9, 2)
        record.update({
            "ok": True, "oom": False, "seconds_per_clip": elapsed, "measured_peak_gb": peak_gb,
            "fits_16gb": peak_gb <= (16.0 - 0.5), "clip_seconds": result.seconds,
            "sample": str(out_clip),
        })
    except RuntimeError as e:
        elapsed = round(time.time() - t0, 1)
        oom = "out of memory" in str(e).lower() or "CUDA out of memory" in str(e)
        record.update({"ok": False, "oom": oom, "seconds_per_clip": elapsed, "error": str(e)[:300]})
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description="CogVideoX-5B-I2V benchmark (the proof gate)")
    ap.add_argument("--keyframe", type=Path, default=None, help="real keyframe PNG (else synthesized)")
    ap.add_argument("--out", type=Path, default=Path("results"), help="output dir for clips + report")
    ap.add_argument("--tiers", default="draft,standard,final", help="comma list of tiers to run")
    ap.add_argument("--prompt", default="a slow, smooth cinematic dolly-in, gentle parallax", help="motion prompt")
    args = ap.parse_args()

    try:
        import torch  # noqa: F401
    except Exception:
        print("ERROR: torch/diffusers not installed -- this is the GPU proof gate, run it on the card "
              "(pip install -r requirements.txt). Pure helpers are covered by the CPU test suite.",
              file=sys.stderr)
        return 2

    import torch
    if not torch.cuda.is_available():
        print("ERROR: no CUDA device visible. Run on the GPU box (check the NVIDIA Container Toolkit "
              "/ --gpus all if in Docker).", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    keyframe = args.keyframe or synth_keyframe(args.out / "keyframe.png")
    tiers = [QualityTier.parse(t) for t in args.tiers.split(",") if t.strip()]

    host = platform.node() or "host"
    meta = {"host": host, "gpu": gpu_name(), "keyframe": str(keyframe), "prompt": args.prompt}
    print(f"Benchmarking on {meta['gpu']} (host {host}); keyframe={keyframe}", flush=True)

    results = []
    for tier in tiers:
        print(f"\n--- tier {tier.value} ---", flush=True)
        rec = run_tier(tier, keyframe, args.out, args.prompt)
        results.append(rec)
        if rec.get("ok"):
            print(f"  OK  peak={rec['measured_peak_gb']}GB  {rec['seconds_per_clip']}s/clip  "
                  f"fits16GB={rec['fits_16gb']}  -> {rec['sample']}", flush=True)
        else:
            print(f"  FAIL  oom={rec['oom']}  {rec.get('error','')[:120]}", flush=True)

    report = {"meta": meta, "results": results}
    (args.out / f"benchmark-{host}.json").write_text(json.dumps(report, indent=2))
    _write_markdown(args.out / f"benchmark-{host}.md", report)
    print(f"\nWrote {args.out}/benchmark-{host}.md + .json + sample clips.", flush=True)
    return 0


def _write_markdown(path: Path, report: dict) -> None:
    m = report["meta"]
    lines = [
        f"# CogVideoX-5B-I2V benchmark -- {m['host']}",
        "",
        f"- GPU: **{m['gpu']}**",
        f"- keyframe: `{m['keyframe']}`  prompt: _{m['prompt']}_",
        "",
        "| tier | model | res | frames | steps | offload | est peak | measured peak | fits 16GB | sec/clip | result |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        res = f"{r['width']}x{r['height']}"
        meas = f"{r.get('measured_peak_gb','-')}GB" if r.get("ok") else "-"
        fits = ("yes" if r.get("fits_16gb") else "NO") if r.get("ok") else "-"
        spc = r.get("seconds_per_clip", "-")
        outcome = "OK" if r.get("ok") else ("OOM" if r.get("oom") else "FAIL")
        lines.append(
            f"| {r['tier']} | `{Path(r['model']).name}` | {res} | {r['frames']} | {r['steps']} | "
            f"{r['offload']} | {r['estimated_peak_gb']}GB | {meas} | {fits} | {spc} | {outcome} |"
        )
    lines += ["", "Sample clips are written alongside this report; eyeball them for motion quality.",
              "Update `src/vivijure_local/config.py` (tier ceilings) + `vram.py` (coefficients) from the",
              "measured peaks, then tag the validated version."]
    path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
