"""Tests for the render worker's protocol loop + its stdout hygiene (16gb#77 / 12gb#94), no GPU.

Two things are proven here that the client tests (which use a fake worker) cannot:
  1. serve_loop() drives the real protocol: ready -> progress relay -> result / error, and stops on
     shutdown / EOF, with a render failure surfaced as an honest error event (never a crash).
  2. _open_protocol_writer() keeps the protocol channel clean: after it runs, a stray print() / anything
     written to fd 1 lands on STDERR, and only explicit protocol events reach the real stdout. This is
     the guard against torch/diffusers/tqdm corrupting the newline-JSON framing.
"""
import json
import os
import subprocess
import sys
import textwrap

from vivijure_local.core import render_worker


def _feed(lines):
    """A readline() stand-in that yields the given lines then EOF."""
    it = iter(lines)

    def readline():
        try:
            return next(it)
        except StopIteration:
            return ""

    return readline


def test_serve_loop_emits_ready_progress_then_result():
    events = []
    prog = {"cb": None}

    def make_run_fn(on_progress):
        prog["cb"] = on_progress

        def run_fn(payload, should_cancel):
            on_progress(1, 2)
            on_progress(2, 2)
            assert should_cancel() is False  # the worker never self-cancels
            return {"clip_key": "renders/p/clips/%s_i2v.mp4" % payload["shot_id"], "num_frames": 49}

        return run_fn

    render_worker.serve_loop(
        events.append,
        make_run_fn,
        _feed([json.dumps({"t": "render", "job": "j1", "input": {"shot_id": "s"}}) + "\n"]),
    )
    assert events[0] == {"t": "ready"}
    assert {"t": "progress", "job": "j1", "step": 1, "total": 2} in events
    assert {"t": "progress", "job": "j1", "step": 2, "total": 2} in events
    result = events[-1]
    assert result["t"] == "result" and result["job"] == "j1"
    assert result["output"]["clip_key"] == "renders/p/clips/s_i2v.mp4"


def test_serve_loop_render_failure_becomes_error_event_and_keeps_serving():
    events = []

    def make_run_fn(on_progress):
        def run_fn(payload, should_cancel):
            raise RuntimeError("keyframe 404")

        return run_fn

    render_worker.serve_loop(
        events.append,
        make_run_fn,
        _feed([
            json.dumps({"t": "render", "job": "bad", "input": {}}) + "\n",
            json.dumps({"t": "render", "job": "ok", "input": {}}) + "\n",
        ]),
    )
    errs = [e for e in events if e.get("t") == "error"]
    assert len(errs) == 2  # both jobs failed honestly; the loop kept serving after the first
    assert errs[0]["job"] == "bad" and "keyframe 404" in errs[0]["message"]


def test_serve_loop_stops_on_shutdown():
    events = []
    called = {"n": 0}

    def make_run_fn(on_progress):
        def run_fn(payload, should_cancel):
            called["n"] += 1
            return {}

        return run_fn

    render_worker.serve_loop(
        events.append,
        make_run_fn,
        _feed([
            json.dumps({"t": "shutdown"}) + "\n",
            json.dumps({"t": "render", "job": "after", "input": {}}) + "\n",  # must NOT run
        ]),
    )
    assert called["n"] == 0  # shutdown broke the loop before the render command
    assert events == [{"t": "ready"}]


def test_serve_loop_ignores_malformed_and_unknown_lines():
    events = []
    render_worker.serve_loop(
        lambda o: events.append(o),
        lambda on_progress: (lambda p, c: {}),
        _feed(["not json\n", json.dumps({"t": "bogus"}) + "\n", "\n"]),
    )
    assert events == [{"t": "ready"}]  # nothing crashed; unknown/garbage lines skipped


def test_open_protocol_writer_redirects_stray_stdout_to_stderr(tmp_path):
    # Run a child that reserves the protocol channel, then writes a protocol event AND stray stdout. Only
    # the JSON must reach real stdout; the stray text must land on stderr.
    prog = textwrap.dedent(
        """
        import json, sys
        from vivijure_local.core.render_worker import _open_protocol_writer
        proto = _open_protocol_writer()
        print("STRAY-via-print")            # goes to fd 1, now redirected to stderr
        sys.stdout.write("STRAY-via-write\\n")
        sys.stdout.flush()
        proto.write(json.dumps({"t": "ready"}) + "\\n"); proto.flush()
        """
    )
    env = dict(os.environ)
    src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    r = subprocess.run([sys.executable, "-c", prog], capture_output=True, text=True, env=env)
    assert r.stdout.strip() == json.dumps({"t": "ready"})  # ONLY the protocol event on stdout
    assert "STRAY-via-print" in r.stderr and "STRAY-via-write" in r.stderr
    assert "STRAY" not in r.stdout
