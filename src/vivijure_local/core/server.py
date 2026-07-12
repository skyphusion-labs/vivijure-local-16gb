"""The local always-on render server: RunPod-COMPATIBLE job API over stdlib HTTP.

The local-gpu module worker talks to this exactly as own-gpu talks to RunPod -- same endpoints, same
status envelope -- so the CF bridge is a near-clone and the control plane is unchanged:

  POST /run            { "input": { action, project, shot_id, prompt, keyframe_key?, config } } -> { "id" }
  GET  /status/<id>    -> { id, status: IN_QUEUE|IN_PROGRESS|COMPLETED|FAILED, output?, error? }
  POST /cancel/<id>    -> { ok: true }  (idempotent; an unknown id is also ok -- nothing is running)
  GET  /health         -> { ok: true, ... }   (liveness for the tunnel + the operator)
  POST /run { "selftest": true } -> a no-GPU sanity probe (the shared transport harness, like the modules)

Stdlib only (the CLAUDE.md minimal-deps rule): http.server + the JobRegistry. The routing is a PURE
`route()` function (testable without sockets); the HTTP handler is a thin shell over it. R2 + the door's
engine are wired in `build_i2v_run_fn` and injected, so the server module stays importable on a CPU box.

This module is part of the byte-identical `vivijure_local.core` package shared with the sibling door;
the per-door identity + engine binding it reads (SERVICE, ENGINE, animate) live in `vivijure_local.door`.
See docs/architecture.md (shared core).
"""
from __future__ import annotations

import hmac
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Callable

from .. import __version__
from .. import door
from ..door import ENGINE, SERVICE, animate
from .contract import I2VClipRequest, clip_key_for, keyframe_key_for
from .jobs import Cancelled, JobRegistry

_STATUS_RE = re.compile(r"^/status/([A-Za-z0-9]+)$")
_CANCEL_RE = re.compile(r"^/cancel/([A-Za-z0-9]+)$")


def token_error(headers_token: str | None, expected: str) -> tuple[int, dict] | None:
    """Guard for the protected i2v routes. Returns an (status, body) error, or None when allowed.

    HARD RULE: the backend is exposed to the public internet through the tunnel, so an i2v endpoint MUST
    NEVER serve without a valid token -- an open endpoint is an unauthenticated GPU-spend / abuse vector
    (the module-trust-boundary lesson). A request is rejected when no token is configured at all (503:
    refuse to run open) or the Bearer token is missing / wrong (401). /health and the no-GPU selftest
    stay open for liveness; everything that can touch the GPU is gated."""
    if not expected:
        return 503, {"ok": False, "error": "LOCAL_BACKEND_TOKEN not configured: refusing to serve an open i2v endpoint (the tunnel is public)"}
    if not headers_token or not hmac.compare_digest(headers_token, expected):
        return 401, {"ok": False, "error": "unauthorized"}
    return None


def route(
    method: str,
    path: str,
    body: dict | None,
    *,
    registry: JobRegistry,
    token: str | None,
    expected_token: str,
    version: str = __version__,
) -> tuple[int, dict]:
    """Pure request dispatcher: (method, path, parsed-body) -> (http_status, json_dict). No sockets, no
    I/O of its own (the registry's run_fn does the work on its worker thread), so it unit-tests
    directly. Mirrors the RunPod envelope the local-gpu module expects."""
    if method == "GET" and path == "/health":
        health = {"ok": True, "service": SERVICE, "version": version, "engine": ENGINE}
        grid = getattr(door, "DURATION_GRID", None)  # #707: door-declared duration grid; absent = no fixed grid
        if grid is not None:
            health["duration_grid"] = grid
        return 200, health

    if method == "POST" and path == "/run":
        payload = (body or {}).get("input", body or {})
        if (body or {}).get("selftest") or payload.get("selftest"):
            return 200, {"ok": True, "selftest": True, "engine": ENGINE}  # no-GPU probe stays open
        err = token_error(token, expected_token)  # i2v touches the GPU -> token required
        if err:
            return err
        action = str(payload.get("action") or "i2v_clip")
        if action != "i2v_clip":
            # This backend serves the motion.backend door only; other actions are an honest 400, not a
            # silent accept (the datacenter backend owns render/finish_clip).
            return 400, {"ok": False, "error": f"unsupported action {action!r} (this backend serves i2v_clip)"}
        req = I2VClipRequest.from_input(payload)
        reason = req.validate()
        if reason:
            return 400, {"ok": False, "error": reason}
        job_id = registry.submit(payload)
        return 200, {"id": job_id}

    m = _STATUS_RE.match(path)
    if method == "GET" and m:
        err = token_error(token, expected_token)
        if err:
            return err
        job = registry.get(m.group(1))
        if job is None:
            # Unknown / evicted id: a real RunPod 404 envelope, so the module's jobGone + grace logic
            # (the same #141 path) handles a restarted box honestly.
            return 404, {"status": 404, "title": "Not Found", "detail": "job not found"}
        return 200, job.status_dict()

    m = _CANCEL_RE.match(path)
    if method == "POST" and m:
        err = token_error(token, expected_token)
        if err:
            return err
        registry.cancel(m.group(1))  # idempotent; always ok (the contract reads ok as "not running")
        return 200, {"ok": True}

    return 404, {"status": 404, "title": "Not Found", "detail": "no such route"}


# --------------------------------------------------------------------------- the i2v run_fn (GPU side)

def build_i2v_run_fn(store, *, workdir: Path | None = None, on_progress: Callable[[int, int], None] | None = None) -> Callable[[dict, Callable[[], bool]], dict]:
    """Build the registry's worker function: fetch the keyframe from R2, animate it with the door's
    engine, upload the clip, return a pointer-only result (the clip_key), mirroring vivijure-backend's
    run_i2v_clip_job. `store` is an R2-like object (get_file / put_file); injected so this tests with a
    fake store.

    The returned fn takes (payload, should_cancel). should_cancel is threaded into the engine's progress
    callback so a /cancel aborts between denoise steps (a torch step is not externally interruptible)."""
    base = Path(workdir) if workdir else Path(tempfile.gettempdir())
    announced = {"weights": False}  # heads-up the operator once, on the first (cold) job

    def run(payload: dict, should_cancel: Callable[[], bool]) -> dict:
        from ..config import I2VConfig, QualityTier

        req = I2VClipRequest.from_input(payload)
        reason = req.validate()
        if reason:
            raise ValueError(reason)

        if not announced["weights"]:
            # The first job on a cold box loads the model weights before denoise (a cold box also
            # DOWNLOADS them). /status has no sub-step channel, so it just reads IN_PROGRESS the whole
            # time -- indistinguishable from a hang unless we say so. Announce it once, to stderr's
            # sibling stdout the operator tails.
            announced["weights"] = True
            print(
                "vivijure-local: first i2v job on this process -- the model weights load now (a cold "
                "box downloads them, which can take several minutes before the denoise starts). This "
                "is NOT a hang; keep polling /status (it stays IN_PROGRESS until the clip is ready).",
                flush=True,
            )

        job_dir = Path(tempfile.mkdtemp(prefix="vj-local-", dir=str(base)))
        try:
            kf_key = req.keyframe_key or keyframe_key_for(req.project, req.shot_id)
            local_kf = job_dir / "keyframe.png"
            try:
                store.get_file(kf_key, local_kf)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"i2v_clip: could not fetch keyframe {kf_key!r}: {e}") from e

            tier = QualityTier.parse(req.config.get("quality"))
            cfg = I2VConfig.from_request(req.config, tier=tier)

            def progress_cb(step: int, total: int) -> None:
                if on_progress is not None:
                    try:
                        on_progress(step, total)
                    except Exception:
                        pass  # progress relay is best-effort; never break the render
                if should_cancel():
                    raise Cancelled()

            out_path = job_dir / "out.mp4"
            result = animate(req.shot_id, local_kf, req.prompt, cfg, out_path, progress_cb=progress_cb)

            clip_key = clip_key_for(req.project, req.shot_id)
            store.put_file(result.path, clip_key, content_type="video/mp4")
            # Pointer-only return (small payload; R2 holds state), the exact shape readOutput expects.
            return {
                "clip_key": clip_key,
                "shot_id": req.shot_id,
                "fps": result.fps,
                "num_frames": result.num_frames,
                "seconds": result.seconds,
                "distilled": result.distilled,
            }
        finally:
            import shutil
            shutil.rmtree(job_dir, ignore_errors=True)

    return run


# --------------------------------------------------------------------------- VRAM cap (startup)

def apply_vram_cap(logger=None) -> float | None:
    """Bound this process's VRAM to VIVIJURE_MAX_VRAM_GB, if set, BEFORE any model loads.

    A homelabber sharing one GPU between vivijure and other workloads can cap how much VRAM this process
    is allowed to claim. When the env is set AND CUDA is present, translate the GB cap into torch's
    per-process memory fraction on the active device and pin it, so an over-eager pipeline can never grab
    the whole card. Unset / blank / non-numeric / CPU-only => no-op (the full card, the honest default).

    Returns the applied fraction, or None when it was a no-op, so the caller (and the proof) can assert
    on it. The parse + fraction math is the PURE vram.* helpers (CPU-tested); only the enforcement here
    touches torch, and torch is deferred so this module stays CPU-importable."""
    log = logger or (lambda m: print(m, flush=True))
    from . import vram

    gb = vram.parse_max_vram_gb(os.environ.get(vram.MAX_VRAM_ENV))
    if gb is None:
        return None  # unset / blank / junk: full card
    try:
        import torch  # deferred: only present in the GPU runtime image
    except Exception:
        return None
    if not torch.cuda.is_available():
        return None  # CPU-only box: nothing to cap
    device_index = torch.cuda.current_device()
    total_gb = torch.cuda.get_device_properties(device_index).total_memory / (1024 ** 3)
    fraction = vram.vram_fraction(gb, total_gb)
    if fraction is None:
        return None
    torch.cuda.set_per_process_memory_fraction(fraction, device_index)
    log(f"VRAM capped to {gb}GB ({fraction:.3f} of {total_gb:.1f}GB)")
    return fraction


# --------------------------------------------------------------------------- vGPU honesty (startup)

def warn_if_sliced_vgpu(logger=None, detector=None) -> bool:
    """Boot-time honesty guard (16gb#42): some door engines (CogVideoX) render pure-noise, corrupt clips
    on a mediated GRID/vGPU SLICE while still reporting COMPLETED, with no error. If THIS door declares
    itself vGPU-incompatible (`door.VGPU_UNSUPPORTED`) AND a sliced vGPU is detected, WARN LOUDLY -- but
    NEVER fail: the operator may know better, and any ambiguous read stays silent (no false-positive).

    Door-gated via getattr so the byte-identical core stays correct for the sibling door: the LTX (12GB)
    door renders fine on vGPU and does not set the flag, so getattr(door, "VGPU_UNSUPPORTED", False) is
    False there and this is a no-op. Whole-card passthrough is never flagged (only the slice corrupts).
    Returns True iff it warned (so the proof + tests can assert on it)."""
    from .. import door
    if not getattr(door, "VGPU_UNSUPPORTED", False):
        return False
    from . import gpu_virt

    detect = detector or gpu_virt.detect_virtualization_mode
    if not gpu_virt.is_sliced_vgpu(detect()):
        return False
    log = logger or (lambda m: print(m, flush=True))
    log(getattr(door, "VGPU_WARNING", None) or (
        f"{SERVICE}: WARNING -- a GRID/vGPU-sliced GPU was detected; this door's engine is KNOWN to "
        "produce corrupted (pure-noise) output on a vGPU slice while reporting success. Use a "
        "physical / passthrough GPU."
    ))
    return True


# --------------------------------------------------------------------------- HTTP shell

def preflight_r2_or_exit(logger=None, *, sleep_s: float = 30.0) -> None:
    """Novice-first startup guard: R2 is the ONE thing the operator must supply (this backend shares
    the studio's Cloudflare R2 bucket -- it reads the keyframe and writes the finished clip there). If
    the creds are missing, print a PLAIN, actionable message (never a stack trace) and exit slowly so a
    `restart: unless-stopped` container does not spew a tight crash-loop. Everything else (tunnel, token)
    is zero-config. This does NOT echo any value; it only names which vars are unset."""
    import time

    log = logger or (lambda m: print(m, flush=True))
    required = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
    missing = [k for k in required if not os.environ.get(k)]
    if not missing:
        return
    line = "=" * 68
    log("\n".join([
        "", line,
        f"  {SERVICE}: your R2 credentials are not set yet.",
        "",
        "  This backend shares your Vivijure studio's Cloudflare R2 bucket -- it reads",
        "  the keyframe and writes the finished clip there. It is the ONE thing you must",
        "  set up; the tunnel and access token are automatic.",
        "",
        "  FIX (about a minute):",
        "    1. cp .env.example .env",
        "    2. put your R2 credentials in .env -- currently missing:",
        "         " + ", ".join(missing),
        "    3. docker compose up            (run it again)",
        "",
        "  Get the values: Cloudflare dashboard -> R2 -> Manage R2 API Tokens",
        "  (scope the token to your bucket). Full details in README.md -> Configuration.",
        line, "",
    ]))
    time.sleep(sleep_s)  # keep the message readable under restart: unless-stopped (no traceback flood)
    raise SystemExit(1)


# --------------------------------------------------------------------------- offload override (startup)

def validate_offload_or_exit(logger=None, *, sleep_s: float = 30.0):
    """Startup guard for VIVIJURE_OFFLOAD (16gb#74 / 12gb#91): validate the operator offload override
    BEFORE the socket binds, so a fat-fingered value fails loud HERE instead of silently falling back to
    the per-tier default (or surfacing later as a slow / OOM run). Unset => None (every tier keeps its
    hardcoded, consumer-card-safe offload). A valid value is logged so the operator can see which mode is
    active for every tier. An INVALID value prints a plain, actionable message and exits (never a
    traceback), matching preflight_r2_or_exit. Returns the resolved override (or None) on success."""
    import time

    from .. import config

    log = logger or (lambda m: print(m, flush=True))
    try:
        override = config.offload_override()
    except ValueError as e:
        line = "=" * 68
        log("\n".join([
            "", line,
            f"  {SERVICE}: your {config.OFFLOAD_ENV} setting is invalid; refusing to start.",
            "",
            f"    {e}",
            "",
            "  FIX: unset it to keep each tier default, or set one of the listed modes.",
            line, "",
        ]))
        time.sleep(sleep_s)  # readable under restart: unless-stopped (no tight crash-loop)
        raise SystemExit(1) from None
    if override is not None:
        log(f"{SERVICE}: offload override active -- {config.OFFLOAD_ENV}={override.value} (all tiers)")
    return override


def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Wire the render-worker client + the registry and serve. The boot guards + the worker client
    build BEFORE the socket binds so a misconfig fails loud at startup, not mid-render.

    The render runs in a persistent SUBPROCESS (core/render_worker.py via core/worker_client.py), not on
    this process's job thread: the door serves HTTP from a ThreadingHTTPServer whose /status handler
    thread would otherwise share the GIL with the render, and each diffusers sampler step holds the GIL
    ~6.4s in a single C call (16gb#77 / 12gb#94). Isolating the render in its own process keeps /status
    sub-second at every percentile. This process stays pure stdlib on the request path -- torch, boto3,
    and the VRAM cap all live in the worker (the process that actually loads the model)."""
    import signal
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from .worker_client import RenderWorkerClient

    preflight_r2_or_exit()  # novice-first: a plain, actionable message if R2 is unset (not a traceback)
    # NOTE: apply_vram_cap() now runs at render-worker startup (render_worker.main), the process that
    # loads the model; the per-process VRAM cap is meaningless in this HTTP process, which never allocates.
    validate_offload_or_exit()  # 16gb#74/12gb#91: fail loud on a bad VIVIJURE_OFFLOAD, never silently default
    warn_if_sliced_vgpu()  # 16gb#42: warn (never fail) if this door engine is on a corrupting vGPU slice
    expected_token = os.environ.get("LOCAL_BACKEND_TOKEN", "") or ""
    client = RenderWorkerClient()

    def run_fn(payload: dict, should_cancel: Callable[[], bool]) -> dict:
        # Delegate the render to the persistent worker subprocess; this call BLOCKS waiting on the
        # worker (releasing the GIL), so /status stays responsive throughout the render. Cancel = the client
        # terminating + respawning the worker; a worker crash surfaces as an honest job failure.
        return client.render(payload, should_cancel)

    registry = JobRegistry(run_fn)

    class Handler(BaseHTTPRequestHandler):
        def _bearer(self) -> str | None:
            h = self.headers.get("authorization") or ""
            return h[7:] if h.lower().startswith("bearer ") else None

        def _body(self) -> dict | None:
            length = int(self.headers.get("content-length") or 0)
            if not length:
                return None
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                return None

        def _dispatch(self, method: str) -> None:
            status, payload = route(
                method, self.path, self._body() if method == "POST" else None,
                registry=registry, token=self._bearer(), expected_token=expected_token,
            )
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def log_message(self, *args) -> None:  # keep stdout clean; the operator tails their own logs
            pass

    httpd = ThreadingHTTPServer((host, port), Handler)

    def _graceful(signum, _frame):
        # SIGTERM is what `docker stop` and the watchdog send. Break serve_forever so the finally below
        # runs and the worker is shut down cleanly (no orphaned process holding VRAM).
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _graceful)

    print(f"{SERVICE} serving on {host}:{port} (engine={ENGINE})", flush=True)
    try:
        httpd.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        httpd.server_close()
        registry.shutdown()
        client.shutdown(graceful=True)  # send {"t":"shutdown"} + bounded terminate; never orphan a worker


if __name__ == "__main__":
    serve(os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "8000") or "8000"))
