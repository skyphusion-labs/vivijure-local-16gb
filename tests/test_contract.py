"""The door's R2-key slug MUST match vivijure-backend's keys._slug exactly, or a keyframe the studio
wrote under one slug spelling 404s when this door looks under another. These are the cases that used to
diverge (double space, tabs, leading/trailing whitespace, empty)."""
from vivijure_local.contract import _safe, clip_key_for, keyframe_key_for


def test_safe_matches_backend_slug_on_the_cases_that_used_to_diverge():
    # backend keys._slug == "_".join(x.strip().split()).replace("/", "_") or "untitled"
    assert _safe("My Film") == "My_Film"
    assert _safe("My  Film") == "My_Film"        # double space: was "My__Film" -> keyframe 404
    assert _safe("  My Film  ") == "My_Film"     # leading/trailing stripped
    assert _safe("My\tFilm") == "My_Film"        # tabs are whitespace too
    assert _safe("a/b") == "a_b"                 # slash -> underscore
    assert _safe("") == "untitled"               # empty -> the backend fallback
    assert _safe("   ") == "untitled"            # whitespace-only -> fallback


def test_key_helpers_use_the_aligned_slug():
    assert keyframe_key_for("My  Film", "shot 1") == "renders/My_Film/keyframes/shot_1.png"
    assert clip_key_for("My  Film", "shot 1") == "renders/My_Film/clips/shot_1_i2v.mp4"
