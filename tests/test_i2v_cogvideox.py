"""Pure frame/dimension math for the CogVideoX engine (no torch, no GPU)."""
from vivijure_local import i2v_cogvideox as eng
from vivijure_local.config import I2VConfig, QualityTier


def test_snap_frames_rounds_up_to_4k_plus_1():
    assert eng.snap_frames(1) == 1            # 4*0+1
    assert eng.snap_frames(5) == 5            # 4*1+1 exact
    assert eng.snap_frames(6) == 9            # round up to 4*2+1
    assert eng.snap_frames(25) == 25          # 4*6+1 exact
    assert (eng.snap_frames(24) - 1) % eng.TEMPORAL_STRIDE == 0


def test_snap_frames_clamps_below_the_ceiling_and_stays_valid():
    n = eng.snap_frames(10_000)
    assert n <= eng.MAX_FRAMES                 # capped at the 49-frame CogVideoX-5B-I2V ceiling
    assert n == 49
    assert (n - 1) % eng.TEMPORAL_STRIDE == 0  # still 4k+1 after the clamp


def test_snap_dim_rounds_down_to_multiple_of_16_with_a_floor():
    assert eng.snap_dim(720) == 720
    assert eng.snap_dim(700) == 688           # rounds DOWN (a clamped ceiling stays a ceiling)
    assert eng.snap_dim(10) == 16             # floor


def test_frames_for_derives_from_seconds_and_caps():
    assert eng.frames_for(6, 8) == 49         # 48 -> 4*12+1, at the ceiling
    assert eng.frames_for(None, 8) <= eng.MAX_FRAMES
    assert eng.frames_for(0, 8) <= eng.MAX_FRAMES


def test_clip_seconds_is_frames_over_fps():
    assert eng.clip_seconds(49, 8) == round(49 / 8, 3)


def test_resolve_engine_dims_snaps_both_axes_and_frames():
    cfg = I2VConfig.from_request({"quality": "standard"}, tier=QualityTier.STANDARD)
    w, h, n = eng.resolve_engine_dims(cfg)
    assert w % 16 == 0 and h % 16 == 0
    assert (n - 1) % eng.TEMPORAL_STRIDE == 0


def test_animate_raises_without_torch_rather_than_faking_output():
    # A producer stage never fakes a clip; with no torch/diffusers present the body must raise.
    import pytest

    cfg = I2VConfig.from_request({"quality": "draft"}, tier=QualityTier.DRAFT)
    try:
        import torch  # noqa: F401
        import diffusers  # noqa: F401
    except Exception:
        with pytest.raises(RuntimeError):
            eng.animate("shot_01", __file__, "a slow dolly in", cfg, "/tmp/none.mp4")
    else:
        pytest.skip("torch+diffusers present; the deferred-import guard cannot be exercised here")


# --------------------------------------------------------------------------- pipeline cache (process-lifetime)

def _fake_torch(cuda_available: bool, emptied: list | None = None):
    class _Cuda:
        @staticmethod
        def is_available():
            return cuda_available

        @staticmethod
        def empty_cache():
            if emptied is not None:
                emptied.append(True)

    class _Torch:
        bfloat16 = "bf16"
        cuda = _Cuda

    return _Torch


class _FakeCls:
    """A stand-in pipeline class: from_pretrained records each build and returns a bare object (so
    _apply_offload's best-effort hooks are all no-ops), no torch/diffusers needed."""

    def __init__(self):
        self.builds = []

    def from_pretrained(self, model, torch_dtype=None):
        self.builds.append((model, torch_dtype))
        return object()


def test_get_pipe_builds_once_and_reuses_per_key():
    eng._PIPE_CACHE.clear()
    cfg = I2VConfig.from_request({"quality": "draft"}, tier=QualityTier.DRAFT)
    cls = _FakeCls()
    torch = _fake_torch(cuda_available=False)

    p1 = eng._get_pipe(cfg, cls, torch)
    p2 = eng._get_pipe(cfg, cls, torch)

    assert p1 is p2                  # the warm box reuses the resident pipe
    assert len(cls.builds) == 1      # from_pretrained ran exactly once (the ~30s weights read)
    eng._PIPE_CACHE.clear()


def test_pipe_cache_key_separates_offload_and_tiling():
    import dataclasses

    from vivijure_local.config import Offload

    cfg = I2VConfig.from_request({"quality": "draft"}, tier=QualityTier.DRAFT)
    seq = dataclasses.replace(cfg, offload=Offload.SEQUENTIAL_CPU_OFFLOAD)
    no_tile = dataclasses.replace(cfg, vae_tiling=False)

    assert eng._pipe_cache_key(cfg) != eng._pipe_cache_key(seq)
    assert eng._pipe_cache_key(cfg) != eng._pipe_cache_key(no_tile)


def test_evict_pipe_drops_the_entry_and_frees_vram():
    eng._PIPE_CACHE.clear()
    cfg = I2VConfig.from_request({"quality": "draft"}, tier=QualityTier.DRAFT)
    emptied: list = []
    torch = _fake_torch(cuda_available=True, emptied=emptied)

    eng._get_pipe(cfg, _FakeCls(), torch)
    assert eng._PIPE_CACHE           # cached after a build

    eng._evict_pipe(cfg, torch)
    assert not eng._PIPE_CACHE        # entry gone -> next job rebuilds fresh
    assert emptied == [True]                    # VRAM freed explicitly, not left to GC timing


def test_evict_pipe_skips_empty_cache_when_no_cuda():
    eng._PIPE_CACHE.clear()
    cfg = I2VConfig.from_request({"quality": "draft"}, tier=QualityTier.DRAFT)
    emptied: list = []
    torch = _fake_torch(cuda_available=False, emptied=emptied)

    eng._evict_pipe(cfg, torch)       # CPU box: no CUDA to empty
    assert emptied == []
# --------------------------------------------------------------------------- offload-failure logging

def test_try_returns_true_on_clean_hook_and_false_on_absent():
    calls = []

    class Obj:
        def hook(self, *a):
            calls.append(a)

    assert eng._try(Obj(), "hook") is True
    assert calls == [()]
    assert eng._try(Obj(), "nope") is False   # absent hook -> quietly False


def test_try_logs_loudly_and_returns_false_when_a_present_hook_raises(capsys):
    class Boom:
        def hook(self):
            raise RuntimeError("no cuda here")

    assert eng._try(Boom(), "hook") is False
    err = capsys.readouterr().err
    assert "hook" in err and "VRAM" in err        # the swallowed failure is now surfaced


def test_apply_offload_warns_when_the_strategy_does_not_apply(capsys):
    cfg = I2VConfig.from_request({"quality": "draft"}, tier=QualityTier.DRAFT)

    class BarePipe:  # no offload hooks at all (a wrong/old diffusers build)
        pass

    eng._apply_offload(BarePipe(), cfg)
    err = capsys.readouterr().err
    assert "did not apply" in err                 # offload is the fit; silence would mask an OOM
