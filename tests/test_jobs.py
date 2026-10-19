"""The in-process async job registry: lifecycle, failure-as-data, and cancel (no GPU)."""
import ast
import pathlib
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


# --- the audit line must never be emitted while holding the registry lock ---------------------
#
# This is a fact about the SOURCE, so it is asserted against the source. A runtime probe (patch
# _audit_line, then call `registry._lock.acquire(blocking=False)` from inside it) cannot tell
# "the caller holds the lock" from "another thread holds it", so it reds at random against
# correct code; it also only ever sees the call sites a given test happens to execute. The walk
# below is deterministic, covers EVERY call site in the module, and no timing can defeat it.
#
# Lexical containment is the property: a call written inside the `with` body. A helper DEFINED
# inside the body and called later would not be flagged, and that shape does not exist here.

_BLOCKING_CALLS = ("_audit_line", "print")


def _called_name(node):
    """The bare name of a call's target, or None when the node is not a call."""
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _self_attr_name(node):
    """The attribute name of a `self.X` expression, else None."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
        return node.attr
    return None


def _lock_attr_names(tree):
    """`self._lock`, plus any Condition built on it: a Condition holds the SAME lock."""
    names = {"_lock"}
    while True:
        before = set(names)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Assign) and _called_name(node.value) == "Condition"):
                continue
            if not any(_self_attr_name(arg) in names for arg in node.value.args):
                continue
            for target in node.targets:
                held = _self_attr_name(target)
                if held is not None:
                    names.add(held)
        if names == before:
            return names


def _lock_blocks(tree, lock_names):
    """Every `with self.<lock>:` statement in the tree."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.With, ast.AsyncWith))
        and any(_self_attr_name(item.context_expr) in lock_names for item in node.items)
    ]


def _blocking_calls_under_lock(tree, lock_names):
    """Blocking calls lexically inside a lock-holding `with` body, as sorted (name, lineno)."""
    found = set()
    for block in _lock_blocks(tree, lock_names):
        for stmt in block.body:
            for node in ast.walk(stmt):
                name = _called_name(node)
                if name in _BLOCKING_CALLS:
                    found.add((name, node.lineno))
    return sorted(found)


# A known-defective source. The walk must flag this, or a clean verdict on the real module would
# only prove the instrument is blind.
_DEFECTIVE = """
class R:
    def __init__(self):
        self._lock = threading.Lock()

    def submit(self, payload):
        with self._lock:
            _audit_line("id", payload, "submit")
"""


def test_no_blocking_call_sits_inside_the_registry_lock():
    import vivijure_local.core.jobs as jobs

    # Positive control first.
    fixture = ast.parse(_DEFECTIVE)
    flagged = _blocking_calls_under_lock(fixture, _lock_attr_names(fixture))
    assert [name for name, _lineno in flagged] == ["_audit_line"], flagged

    # Assert on the source of the module that is actually IMPORTED, not on a guessed path.
    path = pathlib.Path(jobs.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lock_names = _lock_attr_names(tree)

    # Denominators. If the lock or the audit helper is renamed, fail loudly here rather than pass
    # vacuously on a walk that found nothing to inspect.
    assert "_lock" in lock_names, lock_names
    assert len(_lock_blocks(tree, lock_names)) >= 5, path
    audit_sites = sorted(n.lineno for n in ast.walk(tree) if _called_name(n) == "_audit_line")
    assert len(audit_sites) >= 5, audit_sites

    under_lock = _blocking_calls_under_lock(tree, lock_names)
    assert under_lock == [], (
        "blocking call inside a lock-holding `with` in "
        + str(path)
        + ": "
        + repr(under_lock)
        + ". A blocked stdout stalls the serial registry. Copy what the line needs, leave the "
        "lock, then emit."
    )
