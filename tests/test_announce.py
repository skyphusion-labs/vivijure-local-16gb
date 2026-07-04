"""Regression tests for the ready-banner quick-tunnel URL parse (#52 / twin).

`/shared/cf.log` lives on a persistent volume and cloudflared APPENDS a fresh quick-tunnel URL on
every (re)start, so the parser must surface the LAST (current) URL, never the first (stale/dead) one.
"""
from vivijure_local.core import announce


def _cf(tmp_path, text):
    (tmp_path / "cf.log").write_text(text)


def test_quick_url_single(monkeypatch, tmp_path):
    monkeypatch.setattr(announce, "SHARED", tmp_path)
    _cf(tmp_path, "INF url=https://quiet-meadow-1234.trycloudflare.com\n")
    assert announce._quick_url() == "https://quiet-meadow-1234.trycloudflare.com"


def test_quick_url_returns_last_url_after_restart(monkeypatch, tmp_path):
    # cloudflared restarted and appended a second URL; the first is now a dead tunnel (#52).
    monkeypatch.setattr(announce, "SHARED", tmp_path)
    _cf(
        tmp_path,
        "INF url=https://old-dead-tunnel-aaa.trycloudflare.com\n"
        "INF reconnecting...\n"
        "INF url=https://fresh-live-tunnel-bbb.trycloudflare.com\n",
    )
    assert announce._quick_url() == "https://fresh-live-tunnel-bbb.trycloudflare.com"


def test_quick_url_empty_when_log_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(announce, "SHARED", tmp_path)
    assert announce._quick_url() == ""


def test_quick_url_empty_when_no_url_line_yet(monkeypatch, tmp_path):
    monkeypatch.setattr(announce, "SHARED", tmp_path)
    _cf(tmp_path, "INF starting cloudflared, no url yet\n")
    assert announce._quick_url() == ""
