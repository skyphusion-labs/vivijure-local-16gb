"""The pure RunPod-compatible router + the i2v run_fn wired to a fake store (no GPU, no sockets).

Auth model (hardened for the public tunnel): /health + the no-GPU selftest are open; every GPU-touching
route (i2v /run, /status, /cancel) REQUIRES a valid LOCAL_BACKEND_TOKEN -- an unconfigured token is a
503 (refuse to run open), a missing/wrong token is a 401.
"""
import time
from pathlib import Path

from vivijure_local.core.jobs import JobRegistry
from vivijure_local.core.server import apply_vram_cap, build_i2v_run_fn, route, token_error, validate_offload_or_exit

TOK = "s3cret-token"


def _reg():
    return JobRegistry(lambda payload, should_cancel: {"clip_key": "renders/p/clips/s_i2v.mp4", "shot_id": "s", "fps": 8, "num_frames": 49})


def _run_body():
    return {"input": {"action": "i2v_clip", "project": "p", "shot_id": "s", "prompt": "dolly in"}}


def test_health_is_open_no_token():
    code, body = route("GET", "/health", None, registry=_reg(), token=None, expected_token=TOK)
    assert code == 200 and body["ok"] is True and body["engine"] == "cogvideox"


def test_selftest_is_open_no_token():
    code, body = route("POST", "/run", {"selftest": True}, registry=_reg(), token=None, expected_token=TOK)
    assert code == 200 and body["selftest"] is True


def test_token_error_helper():
    assert token_error(TOK, TOK) is None                 # match -> allowed
    assert token_error("wrong", TOK)[0] == 401           # mismatch -> 401
    assert token_error(None, TOK)[0] == 401              # missing -> 401
    assert token_error(TOK, "")[0] == 503                # no token configured -> 503 (refuse open)


def test_apply_vram_cap_is_a_noop_when_unset(monkeypatch):
    # Unset env: no-op, returns None, and never even imports torch (so it is safe on the CPU CI box).
    monkeypatch.delenv("VIVIJURE_MAX_VRAM_GB", raising=False)
    logs = []
    assert apply_vram_cap(logger=logs.append) is None
    assert logs == []


def test_apply_vram_cap_is_a_noop_without_cuda(monkeypatch):
    # Env set but no torch/CUDA on the CPU CI box: still a no-op (never raises, never logs a false cap).
    monkeypatch.setenv("VIVIJURE_MAX_VRAM_GB", "11")
    logs = []
    assert apply_vram_cap(logger=logs.append) is None
    assert logs == []


def test_i2v_run_refuses_when_no_token_configured_503():
    # The whole point: behind a public tunnel, an unconfigured backend must NOT serve i2v.
    code, body = route("POST", "/run", _run_body(), registry=_reg(), token=None, expected_token="")
    assert code == 503 and "open i2v endpoint" in body["error"]


def test_i2v_run_rejects_missing_or_wrong_token_401():
    code, _ = route("POST", "/run", _run_body(), registry=_reg(), token=None, expected_token=TOK)
    assert code == 401
    code, _ = route("POST", "/run", _run_body(), registry=_reg(), token="wrong", expected_token=TOK)
    assert code == 401


def test_i2v_run_submits_with_a_valid_token():
    reg = _reg()
    code, body = route("POST", "/run", _run_body(), registry=reg, token=TOK, expected_token=TOK)
    assert code == 200 and isinstance(body["id"], str)
    reg.shutdown()


def test_run_rejects_missing_prompt_400_when_authed():
    code, body = route("POST", "/run", {"input": {"action": "i2v_clip", "project": "p", "shot_id": "s"}},
                       registry=_reg(), token=TOK, expected_token=TOK)
    assert code == 400 and "prompt is required" in body["error"]


def test_run_rejects_unsupported_action_when_authed():
    code, body = route("POST", "/run", {"input": {"action": "render"}}, registry=_reg(), token=TOK, expected_token=TOK)
    assert code == 400 and "unsupported action" in body["error"]


def test_status_requires_token_then_404_for_unknown():
    # unauthed -> 401; authed unknown id -> the 404 envelope the module's jobGone() detects (#141)
    code, _ = route("GET", "/status/deadbeef", None, registry=_reg(), token=None, expected_token=TOK)
    assert code == 401
    code, body = route("GET", "/status/deadbeef", None, registry=_reg(), token=TOK, expected_token=TOK)
    assert code == 404 and body["status"] == 404


def test_status_reports_completed_job_with_pointer_output():
    reg = _reg()
    _, run_body = route("POST", "/run", _run_body(), registry=reg, token=TOK, expected_token=TOK)
    jid = run_body["id"]
    deadline = time.time() + 3.0
    body = {}
    while time.time() < deadline:
        _, body = route("GET", f"/status/{jid}", None, registry=reg, token=TOK, expected_token=TOK)
        if body.get("status") == "COMPLETED":
            break
        time.sleep(0.01)
    assert body["status"] == "COMPLETED" and body["output"]["clip_key"].endswith("_i2v.mp4")
    reg.shutdown()


def test_cancel_requires_token_then_idempotent_ok():
    code, _ = route("POST", "/cancel/whatever", None, registry=_reg(), token=None, expected_token=TOK)
    assert code == 401
    code, body = route("POST", "/cancel/whatever", None, registry=_reg(), token=TOK, expected_token=TOK)
    assert code == 200 and body["ok"] is True


def test_i2v_run_fn_fetches_keyframe_animates_and_uploads_pointer(monkeypatch, tmp_path):
    calls = {}

    class FakeStore:
        def get_file(self, key, dest):
            calls["get"] = key
            Path(dest).write_bytes(b"png")
            return dest

        def put_file(self, src, key, content_type=None):
            calls["put"] = key
            return key

    import vivijure_local.i2v_cogvideox as eng
    from vivijure_local.i2v_cogvideox import I2VResult

    def fake_animate(shot_id, keyframe, prompt, cfg, out_path, *, progress_cb=None):
        Path(out_path).write_bytes(b"mp4")
        if progress_cb:
            progress_cb(1, cfg.steps)
        return I2VResult(shot_id=shot_id, path=Path(out_path), num_frames=49, fps=8, seconds=6.125, distilled=False)

    monkeypatch.setattr(eng, "animate", fake_animate)

    run = build_i2v_run_fn(FakeStore(), workdir=tmp_path)
    out = run({"action": "i2v_clip", "project": "My Film", "shot_id": "shot_02", "prompt": "slow dolly in",
               "config": {"quality": "draft"}}, lambda: False)

    assert calls["get"] == "renders/My_Film/keyframes/shot_02.png"
    assert calls["put"] == "renders/My_Film/clips/shot_02_i2v.mp4"
    assert out["clip_key"] == "renders/My_Film/clips/shot_02_i2v.mp4" and out["num_frames"] == 49


def test_preflight_r2_passes_when_all_present(monkeypatch):
    # all four required R2 vars set -> no exit, no message
    for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        monkeypatch.setenv(k, "x")
    logs = []
    from vivijure_local.core.server import preflight_r2_or_exit
    preflight_r2_or_exit(logger=logs.append, sleep_s=0)  # returns None, does not raise
    assert logs == []


def test_preflight_r2_exits_with_plain_message_when_missing(monkeypatch):
    # missing R2 creds -> a plain actionable message + SystemExit(1), never a traceback, no value echo
    import pytest

    for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        monkeypatch.delenv(k, raising=False)
    logs = []
    from vivijure_local.core.server import preflight_r2_or_exit
    with pytest.raises(SystemExit) as ei:
        preflight_r2_or_exit(logger=logs.append, sleep_s=0)
    assert ei.value.code == 1
    blob = "\n".join(logs)
    assert "R2 credentials are not set" in blob
    assert "docker compose up" in blob            # actionable next step
    assert "R2_ACCOUNT_ID" in blob                # names which var is missing (not its value)


def test_first_job_announces_the_weights_load_once(monkeypatch, tmp_path, capsys):
    # The cold-box weights load is invisible on /status (IN_PROGRESS the whole time); the backend must
    # say so, once, so an operator can tell a several-minute load from a hang.
    class FakeStore:
        def get_file(self, key, dest):
            Path(dest).write_bytes(b"png")
            return dest

        def put_file(self, src, key, content_type=None):
            return key

    import vivijure_local.i2v_cogvideox as eng
    from vivijure_local.i2v_cogvideox import I2VResult

    def fake_animate(shot_id, keyframe, prompt, cfg, out_path, *, progress_cb=None):
        Path(out_path).write_bytes(b"mp4")
        return I2VResult(shot_id=shot_id, path=Path(out_path), num_frames=121, fps=24, seconds=5.04, distilled=False)

    monkeypatch.setattr(eng, "animate", fake_animate)

    run = build_i2v_run_fn(FakeStore(), workdir=tmp_path)
    payload = {"action": "i2v_clip", "project": "P", "shot_id": "s1", "prompt": "dolly",
               "config": {"quality": "draft"}}
    run(payload, lambda: False)
    run(payload, lambda: False)
    out = capsys.readouterr().out
    assert out.count("the model weights load now") == 1   # announced once, not per job
def test_health_version_tracks_package_version():
    # Drift guard: route()'s default version derives from __init__.__version__, so /health can never
    # report a stale hardcoded literal after a version bump.
    from vivijure_local import __version__
    code, body = route("GET", "/health", None, registry=_reg(), token=None, expected_token=TOK)
    assert code == 200 and body["version"] == __version__


def test_token_compare_rejects_equal_length_wrong_token():
    # Timing-safe compare (hmac.compare_digest) still rejects a same-length wrong token as 401.
    assert token_error("s3cret-tokeX", TOK)[0] == 401
    assert token_error(TOK, TOK) is None


# --- VIVIJURE_OFFLOAD startup guard (16gb#74 / 12gb#91) --------------------------------------------

def test_validate_offload_is_a_noop_when_unset(monkeypatch):
    monkeypatch.delenv("VIVIJURE_OFFLOAD", raising=False)
    logs = []
    assert validate_offload_or_exit(logger=logs.append) is None
    assert logs == []  # silent when unset (each tier keeps its default)


def test_validate_offload_accepts_a_valid_mode_and_logs(monkeypatch):
    from vivijure_local.config import Offload
    monkeypatch.setenv("VIVIJURE_OFFLOAD", "none")
    logs = []
    assert validate_offload_or_exit(logger=logs.append) is Offload.NONE
    assert any("VIVIJURE_OFFLOAD" in m and "none" in m for m in logs)  # operator sees the active mode


def test_validate_offload_exits_with_plain_message_when_invalid(monkeypatch):
    # A bad value must fail loud at startup (plain message + SystemExit(1)), never silently default.
    import pytest
    monkeypatch.setenv("VIVIJURE_OFFLOAD", "resident")
    logs = []
    with pytest.raises(SystemExit) as ei:
        validate_offload_or_exit(logger=logs.append, sleep_s=0)
    assert ei.value.code == 1
    blob = "\n".join(logs)
    assert "VIVIJURE_OFFLOAD" in blob and "invalid" in blob
    assert "Traceback" not in blob  # a plain, actionable message, never a stack trace
