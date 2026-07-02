"""An in-process async job registry for the local always-on server.

The datacenter backend is RunPod serverless: RunPod owns the queue and the /run + /status lifecycle.
The local box has no such platform, so this registry IS that lifecycle -- it accepts a job, runs it on
a background worker thread, and exposes RunPod-COMPATIBLE status (IN_QUEUE / IN_PROGRESS / COMPLETED /
FAILED) so the local-gpu module's poll loop is a near-clone of own-gpu's.

A consumer card runs ONE job at a time (a consumer GPU cannot fit two i2v pipelines), so the registry is a
single-worker serial queue: extra submits wait IN_QUEUE. Cancel is best-effort + cooperative: a queued
job is dropped immediately; a running job is flagged so the engine's progress callback can raise and
abort between denoise steps (a torch step is not externally interruptible, so we cancel at the next
checkpoint, then mark the job CANCELED -> reported as FAILED to the contract, which is honest: the clip
was not produced).

Dependency-light: stdlib threading only (the CLAUDE.md minimal-deps rule). The registry is pure of any
model/R2 knowledge -- it runs an injected `run_fn(req, *, should_cancel)` -- so it unit-tests with a
fake worker and no GPU.
"""
from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class JobStatus(str, Enum):
    IN_QUEUE = "IN_QUEUE"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# A cooperative-cancel signal the worker raises (via the should_cancel callback) to abort cleanly.
class Cancelled(Exception):
    pass


@dataclass
class Job:
    id: str
    payload: dict
    status: JobStatus = JobStatus.IN_QUEUE
    output: dict | None = None
    error: str | None = None
    _cancel: bool = field(default=False, repr=False)

    def status_dict(self) -> dict:
        """The RunPod-compatible status envelope the server returns for GET /status/<id>."""
        d: dict = {"id": self.id, "status": self.status.value}
        if self.status is JobStatus.COMPLETED and self.output is not None:
            d["output"] = self.output
        if self.status is JobStatus.FAILED and self.error is not None:
            d["error"] = self.error
        return d


# run_fn(payload, should_cancel) -> output dict. should_cancel() returns True once cancel is requested;
# the worker checks it between denoise steps and raises Cancelled to abort.
RunFn = Callable[[dict, Callable[[], bool]], dict]


class JobRegistry:
    """Serial single-worker job runner with RunPod-compatible status. Thread-safe."""

    def __init__(self, run_fn: RunFn, *, max_completed: int = 256) -> None:
        self._run_fn = run_fn
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._queue: deque[str] = deque()
        self._completed_order: deque[str] = deque()  # for bounded retention (a long-running box)
        self._max_completed = max_completed
        self._worker: threading.Thread | None = None
        self._wake = threading.Condition(self._lock)
        self._stop = False

    # --- submit / query / cancel ---------------------------------------------------------------

    def submit(self, payload: dict) -> str:
        job = Job(id=uuid.uuid4().hex, payload=payload)
        with self._lock:
            self._jobs[job.id] = job
            self._queue.append(job.id)
            self._ensure_worker_locked()
            self._wake.notify()
        return job.id

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        """Best-effort + idempotent. Returns True if the job is now guaranteed not to keep running
        (dropped from the queue, flagged for cooperative abort, or already terminal). A missing id is
        also True (nothing is running for it) -- the contract reads cancel as 'not running on the box'.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return True  # unknown -> not running (idempotent, like a RunPod 404 on cancel)
            if job.status is JobStatus.IN_QUEUE:
                try:
                    self._queue.remove(job_id)
                except ValueError:
                    pass
                job.status = JobStatus.FAILED
                job.error = "canceled before start"
                self._retain_locked(job_id)
                return True
            if job.status is JobStatus.IN_PROGRESS:
                job._cancel = True  # cooperative: the worker aborts at the next checkpoint
                return True
            return True  # already COMPLETED / FAILED

    # --- worker loop ---------------------------------------------------------------------------

    def _ensure_worker_locked(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._run_loop, name="vj-local-jobs", daemon=True)
            self._worker.start()

    def _run_loop(self) -> None:
        while True:
            with self._lock:
                while not self._queue and not self._stop:
                    self._wake.wait()
                if self._stop and not self._queue:
                    return
                job_id = self._queue.popleft()
                job = self._jobs.get(job_id)
                if job is None or job.status is not JobStatus.IN_QUEUE:
                    continue
                if job._cancel:
                    job.status = JobStatus.FAILED
                    job.error = "canceled before start"
                    self._retain_locked(job_id)
                    continue
                job.status = JobStatus.IN_PROGRESS

            # Run OUTSIDE the lock so /status and /cancel stay responsive during a long render.
            try:
                output = self._run_fn(job.payload, lambda: self._is_cancelled(job_id))
                with self._lock:
                    job.output = output
                    job.status = JobStatus.COMPLETED
                    self._retain_locked(job_id)
            except Cancelled:
                with self._lock:
                    job.status = JobStatus.FAILED
                    job.error = "canceled"
                    self._retain_locked(job_id)
            except Exception as e:  # noqa: BLE001 -- a render failure is DATA: record it, keep serving
                with self._lock:
                    job.status = JobStatus.FAILED
                    job.error = str(e)[:500]
                    self._retain_locked(job_id)

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            return bool(job and job._cancel)

    def _retain_locked(self, job_id: str) -> None:
        """Bound how many terminal jobs we keep so a long-running box does not grow unbounded. The
        local-gpu module's grace window then treats an evicted job's 404 as a real loss (#141)."""
        self._completed_order.append(job_id)
        while len(self._completed_order) > self._max_completed:
            old = self._completed_order.popleft()
            self._jobs.pop(old, None)

    def shutdown(self) -> None:
        with self._lock:
            self._stop = True
            self._wake.notify_all()
