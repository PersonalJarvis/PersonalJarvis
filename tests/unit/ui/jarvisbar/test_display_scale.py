"""Screen-adaptive bar geometry (screen-relative sizing).

The bar's pill/window sizes are raw pixels tuned on a desktop monitor; on a
small laptop screen the same fixed size reads as clunky (maintainer feedback,
14" MacBook 2026-07-17). ``compute_display_scale`` derives a screen-relative
factor and ``apply_display_scale`` recomputes the module geometry. These tests
pin three contracts:

1. Screens at least as big as the reference get the signed-off
   ``BASE_DISPLAY_SCALE`` ceiling (0.85 — measured from the maintainer's
   too-big/good-example screenshots 2026-07-21); scale 1.0 still reproduces
   the historical constants byte-identically as the geometry baseline.
2. Small screens shrink proportionally, bounded by ``MIN_DISPLAY_SCALE``.
3. The renderer picks up a rescale at instantiation time (no import-time
   freeze) and renders frames at the recomputed window size.
"""
from __future__ import annotations

import pytest

from jarvis.ui.jarvisbar import renderer


@pytest.fixture(autouse=True)
def _restore_scale():
    """Every test leaves the module at the default 1.0 geometry.

    Resets BOTH axes — the screen-adaptive scale and the user "Bar size"
    multiplier — so a test that exercises ``user_size`` cannot leak the
    enlarged geometry into the next test (``USER_SIZE_SCALE`` is a separate
    module global that ``apply_display_scale(1.0)`` alone would not reset).
    """
    yield
    renderer.apply_display_scale(1.0, user_size=renderer.USER_SIZE_DEFAULT)


# --------------------------------------------------------------------------- #
# compute_display_scale                                                       #
# --------------------------------------------------------------------------- #
def test_reference_and_bigger_screens_get_the_approved_ceiling():
    # The ceiling is the signed-off look, NOT 1.0: the historical constants
    # were judged "too big" (47 px idle pill vs the 40 px good example).
    assert renderer.BASE_DISPLAY_SCALE == 0.85
    assert renderer.compute_display_scale(1920, 1080) == renderer.BASE_DISPLAY_SCALE
    assert renderer.compute_display_scale(2560, 1440) == renderer.BASE_DISPLAY_SCALE
    assert renderer.compute_display_scale(3840, 2160) == renderer.BASE_DISPLAY_SCALE


def test_small_laptop_screen_shrinks_proportionally():
    # A 14" MacBook is ~1512 Tk points wide → width is the binding axis
    # (0.7875 < the 0.85 ceiling). This is the independently signed-off
    # laptop look — the ceiling change must NOT alter it.
    s = renderer.compute_display_scale(1512, 982)
    assert s == pytest.approx(1512 / 1920, abs=0.001)
    assert s < renderer.BASE_DISPLAY_SCALE


def test_height_can_be_the_binding_axis():
    # Ultra-wide but flat screen: the height ratio must win.
    s = renderer.compute_display_scale(2560, 900)
    assert s == pytest.approx(900 / 1080, abs=0.001)


def test_tiny_screen_clamps_at_the_floor():
    assert renderer.compute_display_scale(800, 600) == renderer.MIN_DISPLAY_SCALE


def test_invalid_screen_degrades_to_the_approved_ceiling():
    base = renderer.BASE_DISPLAY_SCALE
    assert renderer.compute_display_scale(0, 0) == base
    assert renderer.compute_display_scale(-1, 1080) == base
    assert renderer.compute_display_scale(None, None) == base  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# apply_display_scale                                                         #
# --------------------------------------------------------------------------- #
# The geometry baseline — scale 1.0 must reproduce these byte-identically
# (regression guard for the signed-off proportions). ACTIVE/WIN/COLLAPSED
# reflect the two 2026-07-21 maintainer calibration rounds: slim active
# height (18 px on the 0.85-scaled monitor), then -15% per side off the
# active width (~60 px there) and -5% per side off the idle width (~37 px).
_HISTORICAL = {
    "COLLAPSED_W": 44,
    "COLLAPSED_H": 8,
    "OPEN_W": 68,
    "OPEN_H": 19,
    "ACTIVE_W": 70,
    "ACTIVE_H": 21,
    "WIN_W": 82,
    "WIN_H": 35,
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


# --------------------------------------------------------------------------- #
# user "Bar size" multiplier                                                  #
# --------------------------------------------------------------------------- #
def test_default_user_size_reproduces_the_historical_geometry():
    # The user axis defaults to 1.0, so passing it explicitly must still yield
    # the byte-identical signed-off geometry (regression guard: the new second
    # arg must not perturb the default look).
    renderer.apply_display_scale(1.0, user_size=1.0)
    for name, value in _HISTORICAL.items():
        assert getattr(renderer, name) == value, name


def test_user_size_scales_width_and_height_together():
    # The whole geometry multiplies by ONE factor, so the pill's aspect ratio
    # is preserved — exactly "the shape stays, only the size changes".
    renderer.apply_display_scale(1.0, user_size=1.0)
    base_active_w, base_active_h = renderer.ACTIVE_W, renderer.ACTIVE_H
    base_win_w, base_win_h = renderer.WIN_W, renderer.WIN_H

    renderer.apply_display_scale(1.0, user_size=2.0)
    # Both axes grow, and grow by (about) the same factor.
    assert renderer.ACTIVE_W > base_active_w
    assert renderer.ACTIVE_H > base_active_h
    assert renderer.WIN_W > base_win_w
    assert renderer.WIN_H > base_win_h
    wr = renderer.ACTIVE_W / base_active_w
    hr = renderer.ACTIVE_H / base_active_h
    assert abs(wr - 2.0) < 0.06  # ~2x, allowing integer rounding at small px
    assert abs(hr - 2.0) < 0.12
    assert abs(wr - hr) < 0.15  # shape preserved: width ratio ≈ height ratio


def test_user_size_below_one_shrinks_the_bar():
    renderer.apply_display_scale(1.0, user_size=1.0)
    base_win_w = renderer.WIN_W
    renderer.apply_display_scale(1.0, user_size=0.5)
    assert renderer.WIN_W < base_win_w


def test_user_size_none_keeps_the_current_multiplier():
    # A screen-scale-only call (user_size omitted) must not reset the user's
    # chosen size — the two axes are independent.
    renderer.apply_display_scale(1.0, user_size=2.0)
    big_win_w = renderer.WIN_W
    renderer.apply_display_scale(1.0)  # screen scale only, user size untouched
    assert renderer.WIN_W == big_win_w
    assert renderer.USER_SIZE_SCALE == 2.0


def test_user_size_clamps_to_the_supported_range():
    assert renderer.clamp_user_size(99.0) == renderer.USER_SIZE_MAX
    assert renderer.clamp_user_size(0.01) == renderer.USER_SIZE_MIN
    assert renderer.clamp_user_size(1.25) == 1.25
    # Corrupt persisted values degrade to the default instead of bricking.
    assert renderer.clamp_user_size(float("nan")) == renderer.USER_SIZE_DEFAULT
    assert renderer.clamp_user_size("oops") == renderer.USER_SIZE_DEFAULT  # type: ignore[arg-type]
    # apply_display_scale folds the clamp in, so an out-of-range request is safe.
    renderer.apply_display_scale(1.0, user_size=99.0)
    assert renderer.USER_SIZE_SCALE == renderer.USER_SIZE_MAX


def test_user_size_multiplies_on_top_of_the_screen_scale():
    # The effective factor is screen × user, so a small screen scaled up by the
    # user meets in the middle. 0.6 screen × 1.5 user == 0.9 effective.
    renderer.apply_display_scale(0.6, user_size=1.5)
    assert renderer.OPEN_W == round(68.16 * 0.9)
