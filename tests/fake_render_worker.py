"""A GPU-free stand-in for core/render_worker.py, used to hermetically test RenderWorkerClient's side of
the IPC protocol (16gb#77 / 12gb#94). It speaks the same newline-JSON protocol but never imports torch,
diffusers, or boto3, so the full parent-side protocol (progress relay, result, error, worker death,
cancel/terminate, graceful shutdown, and stray-line tolerance) tests in CI with no GPU.

Behavior is driven by env vars so one script covers every case the client must handle:
  FAKE_MODE=result (default) : emit N progress events, then a result
           =error            : emit progress, then an {"t":"error"} event
           =die              : emit one progress, then exit(1) mid-job (worker death after ready)
           =die_before       : exit(3) immediately, before ready (cold-start crash)
           =hang             : after ready, block forever on a render (so the client must cancel it)
  FAKE_PROGRESS=<n>          : number of progress events (default 2)
  FAKE_GARBAGE=1             : emit a NON-JSON line on stdout before the result (framing-hygiene test)
  FAKE_SHUTDOWN_MARKER=<path>: on a {"t":"shutdown"} command, write this file (proves graceful shutdown)
"""
import json
import os
import sys
import time


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


MODE = os.environ.get("FAKE_MODE", "result")
NPROG = int(os.environ.get("FAKE_PROGRESS", "2"))
GARBAGE = os.environ.get("FAKE_GARBAGE")
MARKER = os.environ.get("FAKE_SHUTDOWN_MARKER")


def handle_render(cmd):
    job = cmd.get("job")
    if MODE == "hang":
        while True:
            time.sleep(0.05)  # never terminal: the client must terminate us to cancel
    for i in range(NPROG):
        emit({"t": "progress", "job": job, "step": i + 1, "total": NPROG})
    if GARBAGE:
        sys.stdout.write("this is not protocol json at all\n")  # the client must skip this, not crash
        sys.stdout.flush()
    if MODE == "die":
        sys.exit(1)  # worker death after progress, before a terminal event
    if MODE == "error":
        emit({"t": "error", "job": job, "message": "boom: fake render failure"})
        return
    shot = (cmd.get("input") or {}).get("shot_id", "s")
    emit({"t": "result", "job": job, "output": {
        "clip_key": "renders/p/clips/%s_i2v.mp4" % shot, "shot_id": shot,
        "fps": 8, "num_frames": 49, "seconds": 6.125, "distilled": False,
    }})


def main():
    if MODE == "die_before":
        return 3  # crash before ready: the client sees the worker die before any terminal event
    emit({"t": "ready"})
    while True:
        line = sys.stdin.readline()
        if line == "":  # EOF: parent closed our stdin
            break
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except Exception:
            continue
        t = cmd.get("t")
        if t == "shutdown":
            if MARKER:
                with open(MARKER, "w") as f:
                    f.write("shutdown-received")
            break
        if t == "render":
            handle_render(cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
