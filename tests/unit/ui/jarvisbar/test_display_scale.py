"""Screen-adaptive bar geometry (Wispr-style relative sizing).

The bar's pill/window sizes are raw pixels tuned on a desktop monitor; on a
small laptop screen the same fixed size reads as clunky (maintainer feedback,
14" MacBook 2026-07-17). ``compute_display_scale`` derives a screen-relative
factor and ``apply_display_scale`` recomputes the module geometry. These tests
pin three contracts:

1. Screens at least as big as the reference keep the EXACT historical look
   (scale 1.0 must reproduce the old constants byte-identically).
2. Small screens shrink proportionally, bounded by ``MIN_DISPLAY_SCALE``.
3. The renderer picks up a rescale at instantiation time (no import-time
   freeze) and renders frames at the recomputed window size.
"""
from __future__ import annotations

import pytest

from jarvis.ui.jarvisbar import renderer


@pytest.fixture(autouse=True)
def _restore_scale():
    """Every test leaves the module at the default 1.0 geometry."""
    yield
    renderer.apply_display_scale(1.0)


# --------------------------------------------------------------------------- #
# compute_display_scale                                                       #
# --------------------------------------------------------------------------- #
def test_reference_and_bigger_screens_keep_scale_one():
    assert renderer.compute_display_scale(1920, 1080) == 1.0
    assert renderer.compute_display_scale(2560, 1440) == 1.0
    assert renderer.compute_display_scale(3840, 2160) == 1.0


def test_small_laptop_screen_shrinks_proportionally():
    # A 14" MacBook is ~1512 Tk points wide → width is the binding axis.
    s = renderer.compute_display_scale(1512, 982)
    assert s == pytest.approx(1512 / 1920, abs=0.001)
    assert s < 1.0


def test_height_can_be_the_binding_axis():
    # Ultra-wide but flat screen: the height ratio must win.
    s = renderer.compute_display_scale(2560, 900)
    assert s == pytest.approx(900 / 1080, abs=0.001)


def test_tiny_screen_clamps_at_the_floor():
    assert renderer.compute_display_scale(800, 600) == renderer.MIN_DISPLAY_SCALE


def test_invalid_screen_degrades_to_one():
    assert renderer.compute_display_scale(0, 0) == 1.0
    assert renderer.compute_display_scale(-1, 1080) == 1.0
    assert renderer.compute_display_scale(None, None) == 1.0  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# apply_display_scale                                                         #
# --------------------------------------------------------------------------- #
# The maintainer-approved desktop look — scale 1.0 must reproduce these
# byte-identically (regression guard for "my monitor size was perfect").
_HISTORICAL = {
    "COLLAPSED_W": 48,
    "COLLAPSED_H": 8,
    "OPEN_W": 68,
    "OPEN_H": 19,
    "ACTIVE_W": 95,
    "ACTIVE_H": 34,
    "WIN_W": 107,
    "WIN_H": 48,
}


def test_scale_one_is_the_historical_geometry():
    renderer.apply_display_scale(1.0)
    for name, value in _HISTORICAL.items():
        assert getattr(renderer, name) == value, name


def test_scale_recomputes_geometry_and_restores_cleanly():
    renderer.apply_display_scale(0.6)
    assert renderer.DISPLAY_SCALE == 0.6
    assert renderer.OPEN_W == round(68.16 * 0.6)
    assert renderer.WIN_W < _HISTORICAL["WIN_W"]
    assert renderer.WIN_H < _HISTORICAL["WIN_H"]
    # Window still contains the biggest pill.
    assert renderer.WIN_W > renderer.ACTIVE_W
    assert renderer.WIN_H > renderer.ACTIVE_H
    renderer.apply_display_scale(1.0)
    for name, value in _HISTORICAL.items():
        assert getattr(renderer, name) == value, name


def test_scale_never_enlarges_and_never_undershoots_the_floor():
    renderer.apply_display_scale(1.4)
    assert renderer.DISPLAY_SCALE == 1.0
    renderer.apply_display_scale(0.1)
    assert renderer.DISPLAY_SCALE == renderer.MIN_DISPLAY_SCALE


# --------------------------------------------------------------------------- #
# renderer integration                                                        #
# --------------------------------------------------------------------------- #
def test_render_state_reads_scale_at_instantiation_time():
    renderer.apply_display_scale(0.6)
    r = renderer.JarvisBarRenderer()
    assert r._st.pw == float(renderer.COLLAPSED_W)  # noqa: SLF001
    assert r._st.ph == float(renderer.COLLAPSED_H)  # noqa: SLF001


def test_render_produces_frames_at_the_scaled_window_size():
    renderer.apply_display_scale(0.6)
    img = renderer.JarvisBarRenderer().render(0.5, "listen", 0.4)
    assert img.size == (renderer.WIN_W, renderer.WIN_H)
    renderer.apply_display_scale(1.0)
    img = renderer.JarvisBarRenderer().render(0.5, "listen", 0.4)
    assert img.size == (_HISTORICAL["WIN_W"], _HISTORICAL["WIN_H"])
