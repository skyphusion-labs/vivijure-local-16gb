"""GRID/vGPU-slice detection (pure parse + the door-gated boot warning). No torch, no CUDA, no sockets.

The corruption motivating this: CogVideoX-5B-I2V renders pure-noise clips on a mediated GRID/vGPU SLICE
while reporting COMPLETED (16gb#35/#42). The parse is exercised against captured `nvidia-smi -q` shapes;
the warning is exercised through an injected detector + logger so it never needs real silicon.
"""
from vivijure_local.core import gpu_virt, server

# A GRID/vGPU guest: `nvidia-smi -q` reports a sliced profile. THIS is the corrupting case.
SMI_VGPU = """
==============NVSMI LOG==============

Attached GPUs                             : 1
GPU 00000000:06:00.0
    Product Name                          : NVIDIA A16-16Q
    GPU Virtualization Mode
        Virtualization Mode               : VGPU
        Host VGPU Mode                    : N/A
    FB Memory Usage
        Total                             : 16384 MiB
"""

# Bare-metal / consumer card: no virtualization. The clean case.
SMI_BAREMETAL = """
GPU 00000000:01:00.0
    Product Name                          : NVIDIA GeForce RTX 4090
    GPU Virtualization Mode
        Virtualization Mode               : None
        Host VGPU Mode                    : N/A
"""

# A WHOLE card passed through to a VM. The issue is explicit: passthrough works, so never flag it.
SMI_PASSTHROUGH = """
GPU 00000000:01:00.0
    Product Name                          : NVIDIA RTX 4000 Ada Generation
    GPU Virtualization Mode
        Virtualization Mode               : Pass-Through
        Host VGPU Mode                    : N/A
"""

# Old / minimal nvidia-smi with no virtualization section at all: ambiguous -> must stay silent.
SMI_NO_FIELD = """
GPU 00000000:01:00.0
    Product Name                          : NVIDIA Tesla T4
    FB Memory Usage
        Total                             : 15360 MiB
"""


def _proc(stdout, returncode=0):
    class P:
        pass
    p = P()
    p.stdout = stdout
    p.returncode = returncode
    return p


# ---- parse_virtualization_mode ------------------------------------------------------------------

def test_parse_reads_the_vgpu_slice_mode():
    assert gpu_virt.parse_virtualization_mode(SMI_VGPU) == "vgpu"


def test_parse_reads_baremetal_and_passthrough():
    assert gpu_virt.parse_virtualization_mode(SMI_BAREMETAL) == "none"
    assert gpu_virt.parse_virtualization_mode(SMI_PASSTHROUGH) == "pass-through"


def test_parse_ignores_the_section_header_and_host_field():
    # "GPU Virtualization Mode" (header, no colon) and "Host VGPU Mode : N/A" must not be mistaken
    # for the Virtualization Mode value.
    assert gpu_virt.parse_virtualization_mode(SMI_VGPU) == "vgpu"  # not "n/a" from Host VGPU Mode


def test_parse_missing_field_or_empty_is_none():
    assert gpu_virt.parse_virtualization_mode(SMI_NO_FIELD) is None
    assert gpu_virt.parse_virtualization_mode("") is None
    assert gpu_virt.parse_virtualization_mode(None) is None


# ---- is_sliced_vgpu -----------------------------------------------------------------------------

def test_is_sliced_vgpu_only_true_for_the_slice():
    assert gpu_virt.is_sliced_vgpu("vgpu") is True
    for benign in ("none", "pass-through", "host vgpu", None, ""):
        assert gpu_virt.is_sliced_vgpu(benign) is False


# ---- detect_virtualization_mode (injected runner; never touches real nvidia-smi) ----------------

def test_detect_parses_a_successful_run():
    assert gpu_virt.detect_virtualization_mode(runner=lambda: _proc(SMI_VGPU)) == "vgpu"


def test_detect_nonzero_exit_is_none():
    assert gpu_virt.detect_virtualization_mode(runner=lambda: _proc(SMI_VGPU, returncode=9)) is None


def test_detect_swallows_missing_binary_to_none():
    def boom():
        raise FileNotFoundError("nvidia-smi")
    assert gpu_virt.detect_virtualization_mode(runner=boom) is None


# ---- warn_if_sliced_vgpu (the boot guard; door-gated, warn-not-fail) ----------------------------

def test_warn_fires_loudly_on_a_detected_slice():
    logs = []
    warned = server.warn_if_sliced_vgpu(logger=logs.append, detector=lambda: "vgpu")
    assert warned is True
    blob = "\n".join(logs)
    assert "vGPU" in blob and "12GB LTX door" in blob   # loud + points to the tolerant door


def test_warn_is_silent_on_baremetal_or_passthrough():
    for mode in ("none", "pass-through", None):
        logs = []
        assert server.warn_if_sliced_vgpu(logger=logs.append, detector=lambda m=mode: m) is False
        assert logs == []


def test_warn_never_fires_when_door_is_vgpu_tolerant(monkeypatch):
    # The byte-identical core also runs on the LTX door, which is vGPU-tolerant and does NOT set the
    # flag: getattr defaults it off, so even a detected slice is a no-op (no false warning there).
    import vivijure_local.door as door
    monkeypatch.delattr(door, "VGPU_UNSUPPORTED", raising=False)
    logs = []
    assert server.warn_if_sliced_vgpu(logger=logs.append, detector=lambda: "vgpu") is False
    assert logs == []
