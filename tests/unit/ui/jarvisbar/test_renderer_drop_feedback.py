"""A drop on the bar must be VISIBLY answered, at any size, on any monitor.

Before this, dropping a file onto the bar gave nothing back: the window went
opaque while the drag hovered and then said nothing at all about whether the
content had become context. These pin the three properties that make the
answer trustworthy:

1. the confirmation runs a bounded timeline and then gets out of the way;
2. every dimension of it derives from the LIVE pill height, so it survives the
   "Bar size" slider and a different monitor's DPI (the resting pill is ~37x6
   px on a 1440p screen — a fixed-pixel glyph would not even fit); and
3. a frame with no drop in flight is byte-identical to one from before the
   feature existed.
"""
from __future__ import annotations

import numpy as np
import pytest

from jarvis.ui.jarvisbar import renderer


@pytest.fixture(autouse=True)
def _reset_geometry() -> None:
    """Every test starts from the signed-off default geometry."""
    renderer.apply_display_scale(1.0, renderer.USER_SIZE_DEFAULT)


# --------------------------------------------------------------------- #
# The confirmation timeline                                             #
# --------------------------------------------------------------------- #
def test_the_tick_strokes_itself_on_then_holds_then_fades() -> None:
    at_start = renderer.drop_confirm_phase(0.0)
    mid_draw = renderer.drop_confirm_phase(renderer.DROP_CONFIRM_DRAW_S / 2)
    holding = renderer.drop_confirm_phase(
        renderer.DROP_CONFIRM_DRAW_S + renderer.DROP_CONFIRM_HOLD_S / 2
    )
    fading = renderer.drop_confirm_phase(
        renderer.DROP_CONFIRM_DRAW_S
        + renderer.DROP_CONFIRM_HOLD_S
        + renderer.DROP_CONFIRM_FADE_S / 2
    )

    assert at_start[0] == 0.0  # nothing drawn yet
    assert 0.0 < mid_draw[0] < 1.0  # partially stroked
    assert holding == (1.0, 1.0)  # whole tick, fully opaque
    assert fading[0] == 1.0 and 0.0 < fading[1] < 1.0  # complete but dimming


def test_the_confirmation_expires_so_the_bar_can_settle_again() -> None:
    """A verdict that never ended would pin the pill open forever.

    The surface reads exactly this to retire the state and release the
    idle-repaint veto, so "past the end" must be unambiguous.
    """
    assert renderer.drop_confirm_phase(renderer.DROP_CONFIRM_TOTAL_S) == (0.0, 0.0)
    assert renderer.drop_confirm_phase(renderer.DROP_CONFIRM_TOTAL_S + 5.0) == (0.0, 0.0)


def test_a_negative_elapsed_draws_nothing_rather_than_flashing() -> None:
    # Clock sources differ across the surfaces (perf_counter here, an IPC hop
    # on macOS); a slightly-in-the-future stamp must not paint a full tick.
    assert renderer.drop_confirm_phase(-0.2) == (0.0, 0.0)


# --------------------------------------------------------------------- #
# The tick geometry — scale-relative, progressively revealed            #
# --------------------------------------------------------------------- #
def test_the_tick_reveals_from_its_start_and_completes() -> None:
    start = renderer.tick_polyline(50.0, 20.0, 16.0, 0.0)
    half = renderer.tick_polyline(50.0, 20.0, 16.0, 0.5)
    whole = renderer.tick_polyline(50.0, 20.0, 16.0, 1.0)

    assert len(start) < 2  # nothing to stroke yet
    assert 2 <= len(half) <= 3
    assert len(whole) == 3
    # The revealed part always begins at the tick's own start point.
    assert half[0] == whole[0]


def test_the_tick_scales_with_the_pill_not_with_fixed_pixels() -> None:
    """The whole cross-platform requirement in one assertion.

    Doubling the pill height must double the glyph — that is what keeps the
    confirmation intact across the "Bar size" slider, a 4K monitor and a
    laptop screen, all of which change only this number.
    """
    small = renderer.tick_polyline(0.0, 0.0, 10.0, 1.0)
    large = renderer.tick_polyline(0.0, 0.0, 20.0, 1.0)

    for (sx, sy), (lx, ly) in zip(small, large, strict=True):
        assert lx == pytest.approx(sx * 2)
        assert ly == pytest.approx(sy * 2)


def test_the_tick_stays_inside_the_pill_it_is_drawn_in() -> None:
    """A glyph poking through the rim shows the color key as a pink fleck."""
    ph = float(renderer.OPEN_H)
    pw = float(renderer.OPEN_W)
    cx, cy = pw / 2, ph / 2

    for x, y in renderer.tick_polyline(cx, cy, ph, 1.0):
        assert 0.0 < x < pw
        assert 0.0 < y < ph


# --------------------------------------------------------------------- #
# The pill opens for a drop                                             #
# --------------------------------------------------------------------- #
def test_the_resting_pill_opens_for_a_drop() -> None:
    """The resting sliver is far too small to release a file on or to show a
    tick in — a hovering payload has to open it, exactly like hover does."""
    resting = renderer.target_pill_size("idle", hovered=False)
    dropping = renderer.target_pill_size("idle", hovered=False, drop_open=True)

    assert resting == (renderer.COLLAPSED_W, renderer.COLLAPSED_H)
    assert dropping == (renderer.OPEN_W, renderer.OPEN_H)
    assert dropping[1] > resting[1]


def test_a_live_session_keeps_its_own_size_during_a_drop() -> None:
    # The active pill is already the biggest; a drop must not shrink it.
    assert renderer.target_pill_size("speak", hovered=False, drop_open=True) == (
        renderer.ACTIVE_W,
        renderer.ACTIVE_H,
    )


# --------------------------------------------------------------------- #
# The rim carries the state                                             #
# --------------------------------------------------------------------- #
def test_no_drop_leaves_the_rim_exactly_as_it_was() -> None:
    """Non-drop frames must be untouched by this feature."""
    base = renderer.PILL_BORDER
    assert renderer.drop_rim_color(1.23, base, renderer.DROP_STATE_NONE) == base
    assert renderer.drop_rim_color(9.9, renderer.MUTED_RED, "none") == renderer.MUTED_RED


def test_the_armed_rim_pulses_over_time() -> None:
    """A static brightening would be missed; the point is that it breathes."""
    period = 2 * 3.141592653589793 / renderer.DROP_ARM_PULSE_RAD_S
    samples = {
        renderer.drop_rim_color(period * frac, renderer.PILL_BORDER, "armed")
        for frac in (0.0, 0.25, 0.5, 0.75)
    }
    assert len(samples) > 1


def test_the_verdict_colors_are_distinguishable() -> None:
    ok = renderer.drop_rim_color(0.0, renderer.PILL_BORDER, renderer.DROP_STATE_OK)
    no = renderer.drop_rim_color(
        0.0, renderer.PILL_BORDER, renderer.DROP_STATE_REJECTED
    )
    assert ok == renderer.DROP_OK_GREEN
    assert no == renderer.MUTED_RED
    assert ok != no


# --------------------------------------------------------------------- #
# End to end through the real renderer                                  #
# --------------------------------------------------------------------- #
def test_a_confirmed_drop_paints_something_a_plain_idle_bar_does_not() -> None:
    bar = renderer.JarvisBarRenderer()
    # Settle the eased pill at the open size first, so the comparison is about
    # the glyph and not about the pill still growing.
    for _ in range(12):
        bar.render(0.0, "idle", 0.0, drop_state=renderer.DROP_STATE_ARMED)
    armed = bar.render(0.0, "idle", 0.0, drop_state=renderer.DROP_STATE_ARMED)
    confirmed = bar.render(
        0.0,
        "idle",
        0.0,
        drop_state=renderer.DROP_STATE_OK,
        drop_elapsed=renderer.DROP_CONFIRM_DRAW_S + 0.1,
    )

    assert armed.tobytes() != confirmed.tobytes()
    # The tick is green, and green is not in the bar's normal palette.
    pixels = np.asarray(confirmed.convert("RGB"), dtype=np.int16)
    target = np.asarray(renderer.DROP_OK_GREEN, dtype=np.int16)
    assert bool((np.abs(pixels - target).max(axis=-1) < 60).any())


def test_an_expired_confirmation_renders_as_no_drop_at_all() -> None:
    """Belt and braces: a surface slow to retire the state cannot pin the pill.

    The renderer downgrades a finished verdict itself, so the pill target
    collapses back even if ``drop_state`` is still set.
    """
    stale = renderer.JarvisBarRenderer()
    plain = renderer.JarvisBarRenderer()
    for _ in range(40):
        stale_img = stale.render(
            0.0,
            "idle",
            0.0,
            drop_state=renderer.DROP_STATE_OK,
            drop_elapsed=renderer.DROP_CONFIRM_TOTAL_S + 1.0,
        )
        plain_img = plain.render(0.0, "idle", 0.0)

    assert stale_img.tobytes() == plain_img.tobytes()


def test_rendering_a_drop_never_changes_the_frame_size() -> None:
    """The window is fixed; only the pill inside it eases."""
    bar = renderer.JarvisBarRenderer()
    for state in renderer.DROP_STATES:
        img = bar.render(0.0, "idle", 0.0, drop_state=state, drop_elapsed=0.1)
        assert img.size == (renderer.WIN_W, renderer.WIN_H)
