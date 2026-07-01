"""The "ready banner": surface the copy-paste values the moment the stack is actually usable.

Conrad's bar -- transparent + easy: the homelabber runs ONE command and gets a clear, copy-paste-ready
banner with the backend URL + token, WITHOUT grepping any logs or knowing what a tunnel is. This runs
as the compose `ready` service: it waits for the tunnel to be up AND the backend healthy AND the token
written (a readiness gate, not a race), then prints the banner once and idles so `docker compose logs`
always shows it.

Reads, from the shared volume the other services write:
  /shared/token   -- the LOCAL_BACKEND_TOKEN (the backend writes it, generated if the operator left it blank)
  /shared/cf.log  -- the cloudflared output (the quick-tunnel URL is parsed from here)
Env: ANNOUNCE_BACKEND (default http://vivijure-local-backend:8000), TUNNEL_TOKEN (set => named tunnel).
No torch, no heavy deps -- stdlib only.
"""
from __future__ import annotations

import os
import re
import time
import urllib.request
from pathlib import Path

SHARED = Path("/shared")
TRYCF = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")


def _wait(predicate, timeout_s: int = 300, every_s: float = 2.0):
    """Poll `predicate` until it returns a truthy value or the timeout elapses; return it (or None)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        val = predicate()
        if val:
            return val
        time.sleep(every_s)
    return None


def _token() -> str:
    f = SHARED / "token"
    return f.read_text().strip() if f.exists() else ""


def _quick_url() -> str:
    f = SHARED / "cf.log"
    if not f.exists():
        return ""
    m = TRYCF.search(f.read_text(errors="ignore"))
    return m.group(0) if m else ""


def _healthy(backend: str) -> bool:
    try:
        return urllib.request.urlopen(backend + "/health", timeout=3).status == 200
    except Exception:
        return False


def main() -> int:
    backend = os.environ.get("ANNOUNCE_BACKEND", "http://vivijure-local-backend:8000")
    named = bool(os.environ.get("TUNNEL_TOKEN"))

    token = _wait(_token, 300) or "(check `docker compose logs vivijure-local-backend`)"
    healthy = _wait(lambda: _healthy(backend), 600)  # first boot pulls ~10GB of weights; be patient
    url = "(your configured named-tunnel hostname)" if named else (_wait(_quick_url, 300) or "(check `docker compose logs cloudflared`)")

    line = "=" * 64
    status = "LIVE" if healthy else "starting (model still downloading -- it will be LIVE shortly)"
    print("\n" + line, flush=True)
    print(f"  Vivijure local backend is {status}", flush=True)
    print("", flush=True)
    print(f"  Backend URL:    {url}", flush=True)
    print(f"  Backend token:  {token}", flush=True)
    print("", flush=True)
    print('  -> Paste these into your Vivijure studio\'s "Local (your GPU)" door', flush=True)
    print("     (LOCAL_BACKEND_URL + LOCAL_BACKEND_TOKEN). That is the whole setup.", flush=True)
    print(line + "\n", flush=True)

    # Idle so `docker compose logs ready` always shows the banner (don't exit -> don't churn-restart).
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    raise SystemExit(main())
