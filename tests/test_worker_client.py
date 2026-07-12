"""Hermetic tests for RenderWorkerClient, the parent side of the render-worker IPC (16gb#77 / 12gb#94).

No GPU, no torch: the client is pointed at tests/fake_render_worker.py (a protocol-faithful stand-in
driven by env vars), so every path the client must handle is exercised in CI -- progress relay, result,
worker-reported error, worker death, cancel (terminate + respawn), single-render-in-flight, stray-line
tolerance, and graceful shutdown.
"""
import os
import sys
import threading
import time

import pytest

from vivijure_local.core.jobs import Cancelled
from vivijure_local.core.worker_client import RenderWorkerClient, WorkerDied

FAKE = os.path.join(os.path.dirname(__file__), "fake_render_worker.py")
PAYLOAD = {"action": "i2v_clip", "project": "p", "shot_id": "s", "prompt": "dolly in"}


def _client(*, env_extra=None, **kw):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    kw.setdefault("poll_interval", 0.05)
    kw.setdefault("term_grace", 2.0)
    return RenderWorkerClient([sys.executable, FAKE], env=env, **kw)


def test_happy_path_relays_progress_and_returns_result():
    c = _client(env_extra={"FAKE_PROGRESS": "3"})
    seen = []
    out = c.render(PAYLOAD, on_progress=lambda s, t: seen.append((s, t)))
    assert out["clip_key"] == "renders/p/clips/s_i2v.mp4"
    assert out["num_frames"] == 49 and out["fps"] == 8
    assert seen == [(1, 3), (2, 3), (3, 3)]
    c.shutdown()


def test_warm_worker_is_reused_across_jobs():
    c = _client()
    c.render(PAYLOAD)
    pid1 = c._proc.pid
    c.render(PAYLOAD)
    pid2 = c._proc.pid
    assert pid1 == pid2  # the persistent worker stays warm (no per-job respawn)
    c.shutdown()


def test_worker_reported_error_raises_runtimeerror():
    c = _client(env_extra={"FAKE_MODE": "error"})
    with pytest.raises(RuntimeError) as ei:
        c.render(PAYLOAD)
    assert "fake render failure" in str(ei.value)
    assert not isinstance(ei.value, WorkerDied)  # a reported error is not a death
    c.shutdown()


def test_worker_death_midjob_raises_workerdied():
    c = _client(env_extra={"FAKE_MODE": "die"})
    with pytest.raises(WorkerDied):
        c.render(PAYLOAD)
    assert c._proc is None  # the dead handle was reaped
    c.shutdown()


def test_cold_start_death_raises_workerdied():
    c = _client(env_extra={"FAKE_MODE": "die_before"})
    with pytest.raises(WorkerDied):
        c.render(PAYLOAD)
    c.shutdown()


def test_respawn_after_death():
    c = _client(env_extra={"FAKE_MODE": "die"})
    with pytest.raises(WorkerDied):
        c.render(PAYLOAD)
    # A second render must spawn a FRESH worker (not reuse the reaped, dead one) and fail the same way.
    with pytest.raises(WorkerDied):
        c.render(PAYLOAD)
    c.shutdown()


def test_stray_non_protocol_stdout_line_is_skipped():
    # The fake emits a garbage line before the result; the client must skip it and still return.
    c = _client(env_extra={"FAKE_GARBAGE": "1"})
    out = c.render(PAYLOAD)
    assert out["clip_key"] == "renders/p/clips/s_i2v.mp4"
    c.shutdown()


def test_cancel_terminates_worker_and_raises_cancelled():
    c = _client(env_extra={"FAKE_MODE": "hang"})
    flag = {"cancel": False}
    # Cancel shortly after the render starts hanging.
    t = threading.Timer(0.3, lambda: flag.update(cancel=True))
    t.start()
    with pytest.raises(Cancelled):
        c.render(PAYLOAD, should_cancel=lambda: flag["cancel"])
    t.join()
    assert c._proc is None  # terminated + reaped; the next submit will respawn
    c.shutdown()


def test_single_render_in_flight_is_serialized():
    # Two concurrent render() calls on one client must not interleave on the worker's stdin; the lock
    # serializes them. Both should return a valid result.
    c = _client(env_extra={"FAKE_PROGRESS": "2"})
    results = []
    errors = []

    def go():
        try:
            results.append(c.render(PAYLOAD))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(10)
    assert not errors, errors
    assert len(results) == 2 and all(r["clip_key"].endswith("_i2v.mp4") for r in results)
    c.shutdown()


def test_graceful_shutdown_sends_command_and_reaps(tmp_path):
    marker = tmp_path / "shutdown.marker"
    c = _client(env_extra={"FAKE_SHUTDOWN_MARKER": str(marker)})
    c.render(PAYLOAD)  # spawn + warm the worker
    proc = c._proc
    c.shutdown(graceful=True)
    assert c._proc is None
    assert proc.poll() is not None  # the worker exited
    # Give the worker a beat to flush the marker file it writes on receiving {"t":"shutdown"}.
    for _ in range(20):
        if marker.exists():
            break
        time.sleep(0.05)
    assert marker.exists() and marker.read_text() == "shutdown-received"


def test_shutdown_is_safe_with_no_worker():
    c = _client()
    c.shutdown()  # never spawned: must be a no-op, not raise
