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
        return 200, {"ok": True, "service": SERVICE, "version": version, "engine": ENGINE}

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

def build_i2v_run_fn(store, *, workdir: Path | None = None) -> Callable[[dict, Callable[[], bool]], dict]:
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


def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Wire R2 + the door's engine + the registry and serve. The store + registry build BEFORE the
    socket binds so a misconfig fails loud at startup, not mid-render."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from .r2 import R2, R2Config  # deferred: boto3 lives only in the GPU runtime image

    preflight_r2_or_exit()  # novice-first: a plain, actionable message if R2 is unset (not a traceback)
    apply_vram_cap()  # honor VIVIJURE_MAX_VRAM_GB before anything can touch the GPU
    expected_token = os.environ.get("LOCAL_BACKEND_TOKEN", "") or ""
    store = R2(R2Config.from_env())
    registry = JobRegistry(build_i2v_run_fn(store))

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
    print(f"{SERVICE} serving on {host}:{port} (engine={ENGINE})", flush=True)
    try:
        httpd.serve_forever()
    finally:
        registry.shutdown()


if __name__ == "__main__":
    serve(os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "8000") or "8000"))
