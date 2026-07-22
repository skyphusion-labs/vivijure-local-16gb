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
