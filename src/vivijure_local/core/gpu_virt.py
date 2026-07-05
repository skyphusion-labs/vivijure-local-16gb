"""GRID/vGPU-slice detection at boot (pure parse + a best-effort nvidia-smi probe).

Why this exists: some door engines (CogVideoX-5B-I2V, the 16GB door) render pure-noise, corrupt clips
on a mediated GRID/vGPU SLICE (e.g. an NVIDIA A16-xQ profile) while still reporting COMPLETED, with no
error (16gb#35/#42). A whole-card PASSTHROUGH is fine; only the sliced vGPU corrupts. The server warns
the operator at startup when it detects one (warn, never fail: the operator may know better).

The parse (`parse_virtualization_mode`, `is_sliced_vgpu`) is PURE -- no subprocess, no torch, no CUDA --
so it unit-tests on a CPU box against captured `nvidia-smi -q` text. Only `detect_virtualization_mode`
shells out, and it swallows every failure to None (nvidia-smi absent, non-zero exit, timeout, garbage):
detection is best-effort and must NEVER crash startup or false-positive on an ambiguous read.

Part of the byte-identical `vivijure_local.core` package shared with the sibling door; WHETHER a door
warns is the per-door seam in `vivijure_local.door` (VGPU_UNSUPPORTED / VGPU_WARNING), read by the
server. The LTX (12GB) door renders correctly on vGPU and does not set that flag, so it stays silent.
"""
from __future__ import annotations


def parse_virtualization_mode(nvidia_smi_q_output: str | None) -> str | None:
    """Extract the `Virtualization Mode` value from `nvidia-smi -q` text, normalized to lower case
    (e.g. "vgpu", "pass-through", "none"). Returns None when the field is absent or the input is
    empty/unreadable -- an unknown read stays silent, it never guesses.

    Matches the nested `Virtualization Mode : <value>` line only. The `GPU Virtualization Mode` section
    header carries no colon (skipped), and `Host VGPU Mode : <value>` is a different field (its stripped
    line starts with "Host", so it never matches)."""
    if not nvidia_smi_q_output:
        return None
    for line in nvidia_smi_q_output.splitlines():
        s = line.strip()
        if s.startswith("Virtualization Mode") and ":" in s:
            value = s.split(":", 1)[1].strip()
            return value.lower() or None
    return None


def is_sliced_vgpu(mode: str | None) -> bool:
    """True ONLY for a GRID/vGPU-SLICED profile (the mediated-passthrough kind that corrupts CogVideoX).
    A whole-card "pass-through", a bare-metal "none", or an unknown/absent mode is NOT flagged: passthrough
    works, and an ambiguous read must stay silent (no false-positive warn spam)."""
    return mode == "vgpu"


def detect_virtualization_mode(runner=None) -> str | None:
    """Run `nvidia-smi -q` and parse its virtualization mode. Dependency-free (stdlib subprocess only).
    Returns None on ANY failure -- nvidia-smi missing, non-zero exit, timeout, or unparseable output --
    because boot-time detection is best-effort and must not raise or false-positive. `runner` is injected
    in tests (a callable returning an object with `.returncode` + `.stdout`)."""
    import subprocess

    def _default():
        return subprocess.run(
            ["nvidia-smi", "-q"], capture_output=True, text=True, timeout=10
        )

    run = runner or _default
    try:
        proc = run()
    except Exception:  # noqa: BLE001 -- nvidia-smi absent / not on PATH / timeout: stay silent
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    return parse_virtualization_mode(getattr(proc, "stdout", "") or "")
