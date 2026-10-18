"""The in-process async job registry: lifecycle, failure-as-data, and cancel (no GPU)."""
import threading
import time

from vivijure_local.core.jobs import Cancelled, JobRegistry, JobStatus


def _wait_for(registry, job_id, status, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = registry.get(job_id)
        if job and job.status is status:
            return job
        time.sleep(0.01)
    job = registry.get(job_id)
    raise AssertionError(f"job {job_id} did not reach {status} (last={job.status if job else None})")


def test_a_job_runs_to_completion_and_reports_its_output():
    reg = JobRegistry(lambda payload, should_cancel: {"clip_key": "k", "echo": payload["n"]})
    jid = reg.submit({"n": 7})
    job = _wait_for(reg, jid, JobStatus.COMPLETED)
    assert job.output == {"clip_key": "k", "echo": 7}
    assert job.status_dict() == {"id": jid, "status": "COMPLETED", "output": {"clip_key": "k", "echo": 7}}
    reg.shutdown()


def test_a_raising_job_is_failure_as_data_not_a_crash():
    def boom(payload, should_cancel):
        raise RuntimeError("keyframe missing")

    reg = JobRegistry(boom)
    jid = reg.submit({})
    job = _wait_for(reg, jid, JobStatus.FAILED)
    assert "keyframe missing" in (job.error or "")
    assert job.status_dict()["status"] == "FAILED"
    reg.shutdown()


def test_cancel_drops_a_queued_job_before_it_starts():
    release = threading.Event()

    def blocker(payload, should_cancel):
        release.wait(2.0)  # occupy the single worker
        return {"ok": True}

    reg = JobRegistry(blocker)
    a = reg.submit({})            # takes the worker
    _wait_for(reg, a, JobStatus.IN_PROGRESS)
    b = reg.submit({})            # waits IN_QUEUE behind A
    assert reg.cancel(b) is True
    jobb = _wait_for(reg, b, JobStatus.FAILED)
    assert "canceled before start" in (jobb.error or "")
    release.set()
    _wait_for(reg, a, JobStatus.COMPLETED)
    reg.shutdown()


def test_cooperative_cancel_aborts_a_running_job():
    started = threading.Event()

    def cooperative(payload, should_cancel):
        started.set()
        for _ in range(500):
            if should_cancel():
                raise Cancelled()
            time.sleep(0.01)
        return {"ok": True}

    reg = JobRegistry(cooperative)
    jid = reg.submit({})
    assert started.wait(2.0)
    assert reg.cancel(jid) is True
    job = _wait_for(reg, jid, JobStatus.FAILED)
    assert job.error == "canceled"
    reg.shutdown()


def test_cancel_of_an_unknown_id_is_idempotently_true():
    reg = JobRegistry(lambda p, c: {})
    assert reg.cancel("does-not-exist") is True
    reg.shutdown()

def test_raising_job_logs_failure_to_stderr(capsys):
    def boom(payload, should_cancel):
        raise RuntimeError("keyframe missing")

    reg = JobRegistry(boom)
    jid = reg.submit({})
    job = _wait_for(reg, jid, JobStatus.FAILED)
    assert "keyframe missing" in (job.error or "")
    err = capsys.readouterr().err
    assert f"vivijure-local: job {jid} FAILED:" in err
    assert "keyframe missing" in err
    reg.shutdown()


def test_log_jobs_is_silent_unless_enabled(monkeypatch, capsys):
    monkeypatch.delenv("LOG_JOBS", raising=False)
    reg = JobRegistry(lambda payload, should_cancel: {"ok": True})
    jid = reg.submit({"project": "p", "shot_id": "s", "action": "i2v_clip", "token": "s3cret-token"})
    _wait_for(reg, jid, JobStatus.COMPLETED)
    reg.shutdown()
    out = capsys.readouterr().out
    assert "vj-local" not in out
    assert "s3cret-token" not in out


def test_log_jobs_emits_submit_and_completed_without_the_payload(monkeypatch, capsys):
    monkeypatch.setenv("LOG_JOBS", "1")
    reg = JobRegistry(lambda payload, should_cancel: {"ok": True})
    jid = reg.submit({
        "project": "lighthouse dawn",
        "shot_id": "shot_01",
        "action": "i2v_clip",
        "token": "s3cret-token",
        "prompt": "do not log this prompt",
    })
    _wait_for(reg, jid, JobStatus.COMPLETED)
    reg.shutdown()
    out = capsys.readouterr().out
    assert f"vj-local job={jid} project=lighthouse_dawn shot=shot_01 action=i2v_clip event=submit" in out
    assert f"vj-local job={jid} project=lighthouse_dawn shot=shot_01 action=i2v_clip event=COMPLETED" in out
    assert "s3cret-token" not in out
    assert "do not log this prompt" not in out


def test_log_jobs_failed_reason_omits_exception_text(monkeypatch, capsys):
    monkeypatch.setenv("LOG_JOBS", "1")

    def boom(payload, should_cancel):
        raise RuntimeError("token=s3cret-token leaked in the exception")

    reg = JobRegistry(boom)
    jid = reg.submit({"project": "p", "shot_id": "s", "action": "preview"})
    _wait_for(reg, jid, JobStatus.FAILED)
    reg.shutdown()
    out = capsys.readouterr().out
    assert f"vj-local job={jid} project=p shot=s action=preview event=FAILED reason=error" in out
    assert "s3cret-token" not in out
    assert "leaked" not in out


def test_log_jobs_names_a_queued_cancel(monkeypatch, capsys):
    monkeypatch.setenv("LOG_JOBS", "1")
    release = threading.Event()

    def blocker(payload, should_cancel):
        release.wait(2.0)
        return {"ok": True}

    reg = JobRegistry(blocker)
    first = reg.submit({"project": "p", "shot_id": "a", "action": "i2v_clip"})
    _wait_for(reg, first, JobStatus.IN_PROGRESS)
    second = reg.submit({"project": "p", "shot_id": "b", "action": "i2v_clip"})
    assert reg.cancel(second) is True
    _wait_for(reg, second, JobStatus.FAILED)
    release.set()
    _wait_for(reg, first, JobStatus.COMPLETED)
    reg.shutdown()
    out = capsys.readouterr().out
    assert f"vj-local job={second} project=p shot=b action=i2v_clip event=FAILED reason=canceled_before_start" in out


def _caller_released_the_lock(lock):
    """True when this thread can take the lock.

    A lock this thread already holds never succeeds, because Lock is not reentrant.
    Another thread may hold it for one critical section, so a single miss is retried.
    A miss on every attempt means the caller is still inside the lock.
    """
    for _ in range(8):
        if lock.acquire(blocking=False):
            lock.release()
            return True
        time.sleep(0.005)
    return False


def test_caller_released_stays_false_while_this_thread_holds_the_lock():
    lock = threading.Lock()
    lock.acquire()
    try:
        assert _caller_released_the_lock(lock) is False
    finally:
        lock.release()
    assert _caller_released_the_lock(lock) is True


def test_audit_line_does_not_hold_the_registry_lock(monkeypatch):
    monkeypatch.setenv("LOG_JOBS", "1")
    import vivijure_local.core.jobs as jobs

    def watch(registry, seen):
        def wrapped(job_id, payload, event, reason=None):
            seen.append((event, _caller_released_the_lock(registry._lock)))

        monkeypatch.setattr(jobs, "_audit_line", wrapped)

    def wait_seen(n):
        deadline = time.time() + 2
        while time.time() < deadline:
            if len(seen) >= n:
                return
            time.sleep(0.01)
        raise AssertionError(seen)

    seen = []
    finished = threading.Event()

    def finish(payload, should_cancel):
        finished.set()
        return {"ok": True}

    reg = JobRegistry(finish)
    watch(reg, seen)
    reg.submit({"project": "p", "shot_id": "s", "action": "i2v_clip"})
    assert finished.wait(2.0)
    wait_seen(2)
    reg.shutdown()

    def boom(payload, should_cancel):
        raise RuntimeError("boom")

    reg_fail = JobRegistry(boom)
    watch(reg_fail, seen)
    reg_fail.submit({"project": "p", "shot_id": "s", "action": "preview"})
    wait_seen(4)
    reg_fail.shutdown()

    release = threading.Event()
    started = threading.Event()

    def blocker(payload, should_cancel):
        started.set()
        release.wait(2.0)
        return {"ok": True}

    reg_cancel = JobRegistry(blocker)
    watch(reg_cancel, seen)
    reg_cancel.submit({"project": "p", "shot_id": "a", "action": "i2v_clip"})
    assert started.wait(2.0)
    second = reg_cancel.submit({"project": "p", "shot_id": "b", "action": "i2v_clip"})
    assert reg_cancel.cancel(second) is True
    release.set()
    wait_seen(8)
    reg_cancel.shutdown()

    # submit and the terminal line race: the worker can finish before the
    # submit audit on the calling thread is appended. The lock result does not.
    assert sorted(event for event, _held in seen) == sorted([
        "submit",
        "COMPLETED",
        "submit",
        "FAILED",
        "submit",
        "submit",
        "FAILED",
        "COMPLETED",
    ])
    assert all(held for _event, held in seen)
