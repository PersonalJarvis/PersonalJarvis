"""Screen-adaptive bar geometry (constant physical size across displays).

The bar's pill/window sizes are raw pixels; a raw pixel is a different
physical size on every display, so a fixed pixel geometry reads "perfect"
only on the screen it was tuned on (maintainer feedback, 14" MacBook
2026-07-17; "way too big" on the desktop monitor 2026-07-21).
``compute_physical_scale`` converts the approved look into the pixel budget
that reproduces the same MILLIMETRE size on the actual display;
``compute_display_scale`` stays the resolution-relative fallback for hosts
with missing/implausible physical data; ``apply_display_scale`` recomputes
the module geometry. These tests pin four contracts:

1. Screens at least as big as the reference keep the EXACT historical look
   under the fallback (scale 1.0 reproduces the old constants
   byte-identically).
2. Small screens shrink proportionally, bounded by ``MIN_DISPLAY_SCALE``.
3. Physical sizing is self-consistent on the reference laptop, shrinks on
   physically-coarse monitors, may exceed 1.0 on physically-fine panels, and
   REFUSES implausible physical data (``None`` → fallback).
4. The renderer picks up a rescale at instantiation time (no import-time
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
# compute_physical_scale / resolve_display_scale                              #
# --------------------------------------------------------------------------- #
# The reference laptop panel the approved look was signed off on:
# 14" MacBook Pro, 1512x982 points on a 302.4x196.4 mm panel.
_REF = (1512, 982, 302.4, 196.4)


def test_physical_scale_reproduces_the_approved_look_on_the_reference_laptop():
    s = renderer.compute_physical_scale(*_REF)
    assert s == pytest.approx(renderer.compute_display_scale(1512, 982), abs=0.005)


def test_physical_scale_shrinks_on_a_coarse_pixel_desktop_monitor():
    # 24" 1080p (531x299 mm): each pixel is ~1.4x the reference millimetres,
    # so the SAME physical bar needs ~40% fewer pixels — this is the exact
    # "way too big on my monitor" complaint.
    s = renderer.compute_physical_scale(1920, 1080, 531.0, 299.0)
    assert s is not None
    assert s < 0.65
    ref = renderer.compute_physical_scale(*_REF)
    assert ref is not None
    # Same physical width on both screens (pixels x mm-per-pixel).
    assert s * (531.0 / 1920) == pytest.approx(ref * (302.4 / 1512), rel=0.02)


def test_physical_scale_may_exceed_one_on_fine_pixel_panels():
    # 27" 4K at 100% OS scaling (596x335 mm): physically tiny pixels need
    # MORE of them for the same millimetres — legal above 1.0 now.
    s = renderer.compute_physical_scale(3840, 2160, 596.0, 335.0)
    assert s is not None
    assert 1.0 < s <= renderer.MAX_DISPLAY_SCALE


def test_physical_scale_rejects_implausible_data():
    assert renderer.compute_physical_scale(1920, 1080, 0.0, 0.0) is None
    assert renderer.compute_physical_scale(1920, 1080, -531.0, 299.0) is None
    # Toy values (a projector/VM EDID lying about a 5 cm panel).
    assert renderer.compute_physical_scale(1920, 1080, 50.0, 30.0) is None
    # Wall-sized values.
    assert renderer.compute_physical_scale(1920, 1080, 3000.0, 1700.0) is None
    # mm aspect wildly off the pixel aspect → synthesized/bogus data.
    assert renderer.compute_physical_scale(1920, 1080, 531.0, 531.0) is None
    assert renderer.compute_physical_scale(0, 0, 531.0, 299.0) is None
    assert (
        renderer.compute_physical_scale(1920, 1080, "x", "y")  # type: ignore[arg-type]
        is None
    )


def test_physical_scale_clamps_to_the_floor_and_ceiling():
    # Enormous physical pixels (32"-class 1080p): clamps at the floor.
    assert (
        renderer.compute_physical_scale(1920, 1080, 708.0, 398.0)
        == renderer.MIN_DISPLAY_SCALE
    )


def test_resolve_prefers_physical_and_falls_back_cleanly():
    # Plausible mm → the physical result.
    assert renderer.resolve_display_scale(*_REF) == renderer.compute_physical_scale(
        *_REF
    )
    # Missing or implausible mm → the resolution-relative fallback.
    assert renderer.resolve_display_scale(1512, 982) == renderer.compute_display_scale(
        1512, 982
    )
    assert renderer.resolve_display_scale(
        1512, 982, 50.0, 30.0
    ) == renderer.compute_display_scale(1512, 982)


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


def test_scale_clamps_to_ceiling_and_floor():
    # Physical sizing may legitimately exceed 1.0 (fine-pixel panels), but
    # never the ceiling; the floor keeps the controls clickable.
    renderer.apply_display_scale(1.4)
    assert renderer.DISPLAY_SCALE == 1.4
    renderer.apply_display_scale(3.0)
    assert renderer.DISPLAY_SCALE == renderer.MAX_DISPLAY_SCALE
    renderer.apply_display_scale(0.1)
    assert renderer.DISPLAY_SCALE == renderer.MIN_DISPLAY_SCALE
    # An enlarged geometry still contains its biggest pill.
    renderer.apply_display_scale(1.4)
    assert renderer.WIN_W > renderer.ACTIVE_W
    assert renderer.WIN_H > renderer.ACTIVE_H


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
