"""Unit tests for the whisper-bar pure renderer math + draw smoke."""
from __future__ import annotations

from jarvis.ui.whisperbar import renderer as R


def test_ease_moves_toward_target():
    assert R.ease(0.0, 1.0, 0.5) == 0.5
    assert R.ease(0.5, 1.0, 0.5) == 0.75


def test_bar_heights_zero_level_is_min():
    hs = R.bar_heights(0.0, 0.0, 7, max_h=40.0, min_h=4.0)
    assert len(hs) == 7
    assert all(abs(h - 4.0) < 1e-6 for h in hs)


def test_bar_heights_grow_with_level():
    lo = sum(R.bar_heights(0.3, 0.2, 7, max_h=40.0, min_h=4.0))
    hi = sum(R.bar_heights(0.3, 0.9, 7, max_h=40.0, min_h=4.0))
    assert hi > lo
    for h in R.bar_heights(1.7, 1.0, 7, max_h=40.0, min_h=4.0):
        assert 4.0 <= h <= 40.0 + 1e-6


def test_wave_points_bounded_inside_pill():
    pts = R.wave_points(0.4, 200, 52, cx=150, cy=36, n=48)
    assert len(pts) == 49
    for x, y in pts:
        assert 50 <= x <= 250
        assert 36 - 26 <= y <= 36 + 26  # within ±height*0.5


def test_render_returns_image_for_every_mode():
    rnd = R.WhisperBarRenderer(accent="#e7c46e")
    for mode in ("idle", "listen", "speak", "think"):
        img = rnd.render(0.1, mode, 0.5)
        assert img.size == (R.WIN_W, R.WIN_H)
        assert img.mode == "RGB"


def _settled(mode, hovered, frames=40, final_t=0.1):
    r = R.WhisperBarRenderer()
    for _ in range(frames):
        r.render(0.0, mode, 0.0)  # deterministic settle
    return list(r.render(final_t, mode, 0.0, hovered=hovered).getdata())


def test_hover_reveals_controls():
    # active + hover draws the X + square → pixels differ from the animation
    assert _settled("listen", hovered=False) != _settled("listen", hovered=True)
    assert _settled("think", hovered=False) != _settled("think", hovered=True)
    # idle + hover opens the bar and shows the dictation square → differs from
    # the clean collapsed standby pill
    assert _settled("idle", hovered=False, frames=80, final_t=0.0) != _settled(
        "idle", hovered=True, frames=80, final_t=0.0
    )


def test_idle_collapses_expansion_over_frames():
    rnd = R.WhisperBarRenderer()
    for _ in range(40):
        rnd.render(0.0, "listen", 0.5)
    expanded = rnd._st.expand
    for _ in range(80):
        rnd.render(0.0, "idle", 0.0)
    assert rnd._st.expand < expanded
    assert rnd._st.expand < 0.1
