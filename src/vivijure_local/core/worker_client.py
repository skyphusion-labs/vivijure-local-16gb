"""Parent-side IPC client for the GPU render worker subprocess (16gb#77 / 12gb#94).

WHY a subprocess. The door serves HTTP from a ThreadingHTTPServer whose /status handler thread shares
the GIL with the render. Each diffusers sampler step holds the GIL in a single ~6.4s C-level torch call,
so a /status poll landing in that window stalls ~6.4s (measured on propagandhi) and the caller times out
on a HEALTHY render. The fix is process isolation: the render runs in a persistent worker SUBPROCESS, so
the HTTP process never shares the render's GIL. This client is the parent (HTTP) side; it BLOCKS on a
queue read while the worker renders, and that blocking read RELEASES the GIL, so /status stays
sub-second at every percentile.

Protocol: newline-delimited JSON, one object per line (see core/render_worker.py for the worker side).
  parent -> worker stdin : {"t":"render","job":<id>,"input":{...payload...}} ; {"t":"shutdown"}
  worker -> parent stdout: {"t":"ready"} ; {"t":"progress","job","step","total"} ;
                           {"t":"result","job","output":{...}} ; {"t":"error","job","message"}
Worker stderr is inherited (the operator tails it): weights-load notice, offload logs, stray library
prints. The worker reserves fd 1 for the protocol and redirects everything else to stderr, so a rogue
print() cannot corrupt framing; as a second line of defense THIS side logs-and-skips any stdout line it
cannot decode, and never lets it crash the read loop.

Read path: a dedicated daemon reader thread pulls whole lines off the worker's stdout (blocking readline,
correct buffering) and pushes them onto a queue; the render call drains that queue with a short timeout
so it can re-check cancel + worker liveness without ever blocking through a multi-second sampler step.
(We deliberately do NOT select() on the pipe fd: readline buffering can hold a full line in Python's
buffer that select never reports, stalling a healthy render -- the reader-thread + queue avoids that.)

Lifecycle: one persistent worker kept warm across jobs (the model stays resident in the worker's
process-lifetime pipeline cache). Cancel = terminate + respawn (process death reclaims all CUDA/VRAM
cleanly, unlike an in-process cooperative cancel that leaves the pipeline resident). Worker death
(OOM SIGKILL, segfault, cold-start import failure) is detected via an EOF sentinel from the reader
thread, and surfaces as an honest job failure (WorkerDied) -- never a hang.

Stdlib only (the CLAUDE.md minimal-deps rule): subprocess + threading + queue + json.
"""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import uuid
from typing import Callable

# The default worker command: run the shared-core worker module in THIS interpreter. Overridable
# (the hermetic tests point it at a fake worker script so the protocol tests without a GPU).
DEFAULT_WORKER_ARGV = [sys.executable, "-m", "vivijure_local.core.render_worker"]


class WorkerDied(RuntimeError):
    """The render worker exited before delivering a terminal (result/error) event for the job. Raised
    so the job fails HONESTLY with the real reason (e.g. an OOM SIGKILL) instead of hanging pending."""


class RenderWorkerClient:
    """Owns one persistent render-worker subprocess and speaks the newline-JSON protocol to it.

    A single worker is kept warm and reused; it is (re)spawned lazily at the start of a render if none is
    alive (first job, or after a cancel/death respawn). One render is in flight at a time -- enforced by
    a lock, matching the serial single-worker JobRegistry that drives this -- because two interleaved
    render commands on one stdin would corrupt the framing.
    """

    def __init__(
        self,
        argv: list[str] | None = None,
        *,
        env: dict | None = None,
        poll_interval: float = 0.5,
        term_grace: float = 5.0,
    ) -> None:
        self._argv = list(argv) if argv else list(DEFAULT_WORKER_ARGV)
        self._env = env
        self._poll_interval = poll_interval
        self._term_grace = term_grace
        self._proc: subprocess.Popen | None = None
        self._queue: queue.Queue | None = None  # (kind, value): ("line", str) or ("eof", None)
        self._render_lock = threading.Lock()  # one render in flight per worker (framing integrity)

    # --- lifecycle ------------------------------------------------------------------------------

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _ensure_worker(self) -> subprocess.Popen:
        """Return a live worker, spawning a fresh one (and its reader thread) if none is alive. Reaps a
        dead handle first so a respawn after a cancel/crash starts clean."""
        if self._alive():
            return self._proc  # type: ignore[return-value]
        self._reap(self._proc)
        proc = subprocess.Popen(
            self._argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            # stderr inherits the parent's: the operator tails the worker log stream directly.
            env=self._env,
            text=True,
            bufsize=1,  # line-buffered
        )
        q: queue.Queue = queue.Queue()
        reader = threading.Thread(target=self._pump_stdout, args=(proc, q), daemon=True)
        reader.start()
        self._proc = proc
        self._queue = q
        return proc

    @staticmethod
    def _pump_stdout(proc: subprocess.Popen, q: "queue.Queue") -> None:
        """Read whole lines off the worker's stdout until EOF, pushing each onto the queue. Runs on a
        daemon thread so the blocking readline never blocks the render loop's cancel/liveness checks; an
        EOF (the worker closed stdout / died) is signalled with a sentinel so the render loop can react."""
        try:
            for line in proc.stdout:  # type: ignore[union-attr]  # correct line buffering, one at a time
                q.put(("line", line))
        except Exception:
            pass
        finally:
            q.put(("eof", None))

    def _reap(self, proc: subprocess.Popen | None) -> None:
        """Close a handle's pipes and drop it (and its queue) if it is still the current one."""
        if proc is None:
            return
        for stream in (proc.stdin, proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        if self._proc is proc:
            self._proc = None
            self._queue = None

    def _terminate_proc(self, proc: subprocess.Popen) -> None:
        """SIGTERM, wait a bounded grace, then SIGKILL. Process death reclaims the GPU. Idempotent."""
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=self._term_grace)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=self._term_grace)
        except Exception:
            pass

    # --- render ---------------------------------------------------------------------------------

    def render(
        self,
        payload: dict,
        should_cancel: Callable[[], bool] | None = None,
        *,
        on_progress: Callable[[int, int], None] | None = None,
        job_id: str | None = None,
    ) -> dict:
        """Run one i2v job on the worker and return its pointer-only output dict. Blocks (releasing the
        GIL) until the worker delivers a result. Raises Cancelled if should_cancel() flips (the worker is
        terminated and respawned on the next call), RuntimeError on a worker-reported render error, and
        WorkerDied if the worker exits before delivering a terminal event."""
        should_cancel = should_cancel or (lambda: False)
        corr = job_id or uuid.uuid4().hex
        with self._render_lock:  # single render in flight: two would interleave on one stdin
            proc = self._ensure_worker()
            q = self._queue
            cmd = json.dumps({"t": "render", "job": corr, "input": payload}) + "\n"
            try:
                proc.stdin.write(cmd)  # type: ignore[union-attr]
                proc.stdin.flush()  # type: ignore[union-attr]
            except (BrokenPipeError, OSError) as e:
                rc = proc.poll()
                self._reap(proc)
                raise WorkerDied(
                    f"render worker unavailable (exit code={rc}) before job {corr} started: {e}"
                ) from None
            return self._read_until_terminal(proc, q, corr, should_cancel, on_progress)

    def _read_until_terminal(
        self,
        proc: subprocess.Popen,
        q: "queue.Queue",
        corr: str,
        should_cancel: Callable[[], bool],
        on_progress: Callable[[int, int], None] | None,
    ) -> dict:
        while True:
            if should_cancel():
                self._terminate_proc(proc)  # cancel = terminate + respawn (VRAM reclaimed on exit)
                self._reap(proc)
                from .jobs import Cancelled
                raise Cancelled()
            try:
                # short timeout so we re-check cancel promptly even mid-step (no data flows during the
                # ~6.4s GIL-holding sampler step, so we must not block indefinitely on the queue).
                kind, val = q.get(timeout=self._poll_interval)
            except queue.Empty:
                continue
            if kind == "eof":  # the reader hit EOF: the worker closed stdout / died
                rc = proc.poll()
                self._reap(proc)
                raise WorkerDied(
                    f"render worker exited (code={rc}) before completing job {corr}"
                )
            line = (val or "").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if not isinstance(ev, dict):
                    raise ValueError("protocol event is not an object")
            except (ValueError, TypeError):
                # stdout hygiene, second line of defense: a stray non-protocol line (a library print that
                # slipped past the worker fd redirect). Log to OUR stderr and SKIP -- never crash framing.
                print(
                    f"vivijure-local: ignoring non-protocol worker stdout line: {line[:200]!r}",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            t = ev.get("t")
            if t == "progress":
                if on_progress is not None:
                    try:
                        on_progress(int(ev.get("step", 0)), int(ev.get("total", 0)))
                    except Exception:
                        pass  # progress is best-effort; never let it break the render
                continue
            if t == "result":
                output = ev.get("output")
                return output if isinstance(output, dict) else {}
            if t == "error":
                raise RuntimeError(str(ev.get("message") or "render worker reported an error"))
            # "ready" and any unknown/forward-compat verb: ignore and keep reading.
            continue

    # --- shutdown -------------------------------------------------------------------------------

    def shutdown(self, *, graceful: bool = True) -> None:
        """Stop the worker so a `docker stop` / watchdog restart never orphans a process holding VRAM.
        Sends {"t":"shutdown"} when idle (best-effort), then terminates within a bounded grace regardless
        of whether a render is in flight -- a still-running render observes the death and fails honestly.
        Safe to call more than once and when no worker exists."""
        proc = self._proc
        if proc is None:
            return
        got = self._render_lock.acquire(timeout=0.5)  # send the graceful line only when idle (lock held)
        try:
            if got and graceful and proc.poll() is None and proc.stdin is not None:
                try:
                    proc.stdin.write(json.dumps({"t": "shutdown"}) + "\n")
                    proc.stdin.flush()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=self._term_grace)  # let it process shutdown and exit on its own
                except subprocess.TimeoutExpired:
                    pass
            # If a render was in flight (lock not acquired), or the worker did not exit on its own,
            # escalate: terminate + hard-kill. Idempotent -- a no-op if it already exited cleanly.
            self._terminate_proc(proc)
        finally:
            if got:
                self._render_lock.release()
        self._reap(proc)
