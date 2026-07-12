"""The GPU render worker subprocess: the render runs HERE, off the HTTP process's GIL (16gb#77 / 12gb#94).

This is the child of core/server.py's HTTP process. It runs the SAME render body the door always ran
(build_i2v_run_fn: fetch the keyframe from R2, animate it, upload the clip, return the pointer), but in
its own process, so the ThreadingHTTPServer's /status handler thread never shares the render's GIL. It
speaks the newline-JSON protocol on stdin/stdout (the parent side is core/worker_client.py):

  stdin  (parent -> here): {"t":"render","job":<id>,"input":{...payload...}} ; {"t":"shutdown"}
  stdout (here -> parent): {"t":"ready"} once at startup ; {"t":"progress","job","step","total"} ;
                           {"t":"result","job","output":{...}} ; {"t":"error","job","message"}

STDOUT HYGIENE (critical): stdout is the protocol channel, but torch / diffusers / tqdm / any stray
print() will write to stdout and corrupt the framing. So BEFORE any heavy import we dup the real stdout
fd to a private protocol writer and point fd 1 at stderr; everything that thinks it is writing to stdout
then lands on stderr (which the operator tails anyway). Protocol events go ONLY through the private
writer.

The worker keeps ONE model warm for its whole lifetime (the pipeline cache in i2v_*.animate is
process-global), so only its first job pays the load. It never self-cancels: cancel is the parent
terminating this process (which reclaims all CUDA/VRAM), so should_cancel is a constant False here.

Stdlib only on the control path (os / sys / json); torch/diffusers/boto3 stay deferred inside the
render body, exactly as before, so an import failure surfaces as an honest job error, not a boot crash.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Callable


def _open_protocol_writer():
    """Reserve fd 1 for the protocol and redirect all other stdout to stderr.

    Returns a text writer bound to the ORIGINAL stdout fd (the private protocol channel). After this,
    fd 1 points at what was fd 2 (stderr), so a library print() / tqdm bar / torch banner cannot corrupt
    the JSON framing -- it just joins the operator's stderr log. Must run before any heavy import."""
    proto = os.fdopen(os.dup(1), "w", buffering=1)  # line-buffered private copy of the real stdout
    os.dup2(2, 1)  # fd 1 -> stderr: everyone else's "stdout" now lands on the operator's log stream
    # sys.stdout still wraps fd 1 (now stderr); a stray print() therefore goes to stderr. Leave it.
    return proto


def serve_loop(emit: Callable[[dict], None], make_run_fn, readline: Callable[[], str]) -> None:
    """The protocol loop, factored out of main() so it tests without a GPU or a real subprocess.

    `emit(obj)` writes one protocol event; `make_run_fn(on_progress)` builds the render function bound to
    a progress sink; `readline()` yields one command line at a time ("" on EOF). A render failure is
    DATA: it becomes an {"t":"error"} event and the loop keeps serving. EOF or {"t":"shutdown"} ends it."""
    current = {"job": None}

    def on_progress(step: int, total: int) -> None:
        job = current["job"]
        if job is not None:
            emit({"t": "progress", "job": job, "step": int(step), "total": int(total)})

    run_fn = make_run_fn(on_progress)
    emit({"t": "ready"})

    while True:
        line = readline()
        if line == "":  # EOF: the parent closed our stdin (it exited / restarted) -- exit, don't orphan.
            break
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
            if not isinstance(cmd, dict):
                continue
        except (ValueError, TypeError):
            continue  # ignore a malformed command line rather than dying
        t = cmd.get("t")
        if t == "shutdown":
            break
        if t != "render":
            continue
        job = cmd.get("job")
        payload = cmd.get("input") if isinstance(cmd.get("input"), dict) else {}
        current["job"] = job
        try:
            # should_cancel is a constant False: cancel is the parent terminating this process, never a
            # cooperative in-process abort (terminate + respawn reclaims the GPU cleanly).
            output = run_fn(payload, lambda: False)
            emit({"t": "result", "job": job, "output": output})
        except Exception as e:  # noqa: BLE001 -- a render failure is DATA: report it and keep serving.
            emit({"t": "error", "job": job, "message": str(e)[:500]})
        finally:
            current["job"] = None


def main() -> int:
    proto = _open_protocol_writer()

    def emit(obj: dict) -> None:
        proto.write(json.dumps(obj) + "\n")
        proto.flush()

    # apply_vram_cap MUST run in THIS process -- the one that loads the model -- before any torch alloc.
    from .server import apply_vram_cap, build_i2v_run_fn

    apply_vram_cap()

    from .r2 import R2, R2Config

    store = R2(R2Config.from_env())

    serve_loop(
        emit,
        lambda on_progress: build_i2v_run_fn(store, on_progress=on_progress),
        sys.stdin.readline,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
