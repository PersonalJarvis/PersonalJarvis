"""Jarvis X coordinate and timing math (HiDPI mapping, cards, pill)."""

from __future__ import annotations

import pytest

from jarvis.jarvisx import geometry

# A 4K panel at 150 % left of a 1080p panel at 100 %, as Windows reports it to
# a per-monitor DPI aware process (physical pixels).
_WIN_MONITORS = [
    {"left": 0, "top": 0, "width": 5760, "height": 2160},
    {"left": 0, "top": 0, "width": 3840, "height": 2160},
    {"left": 3840, "top": 0, "width": 1920, "height": 1080},
]


def test_selection_fractions_normalize_any_drag_direction() -> None:
    assert geometry.selection_fractions(300, 200, 100, 50, 400, 400) == pytest.approx(
        (0.25, 0.125, 0.5, 0.375)
    )


def test_selection_is_clamped_to_the_screen() -> None:
    frac = geometry.selection_fractions(-50, -50, 200, 100, 400, 200)
    assert frac == pytest.approx((0.0, 0.0, 0.5, 0.5))


def test_a_click_is_not_a_selection() -> None:
    assert geometry.selection_fractions(10, 10, 12, 40, 400, 400) is None


def test_hidpi_screen_maps_to_its_physical_monitor() -> None:
    # Qt reports the 4K panel at 150 % as 2560x1440 logical, dpr 1.5.
    qt_4k = {"x": 0, "y": 0, "w": 2560, "h": 1440, "dpr": 1.5}
    # Qt's logical origin of the second screen follows the first's logical width.
    qt_hd = {"x": 2560, "y": 0, "w": 1920, "h": 1080, "dpr": 1.0}
    assert geometry.match_monitor(qt_4k, _WIN_MONITORS) is _WIN_MONITORS[1]
    assert geometry.match_monitor(qt_hd, _WIN_MONITORS) is _WIN_MONITORS[2]


def test_fractions_become_physical_pixels_on_the_matched_monitor() -> None:
    monitor = _WIN_MONITORS[1]
    # A 1000x500 logical drag at (100, 100) on the 150 % screen.
    frac = geometry.selection_fractions(100, 100, 1100, 600, 2560, 1440)
    assert frac is not None
    assert geometry.fraction_to_bbox(monitor, frac) == (150, 150, 1500, 750)


def test_fraction_to_bbox_respects_monitor_origin() -> None:
    assert geometry.fraction_to_bbox(_WIN_MONITORS[2], (0.5, 0.5, 0.5, 0.5)) == (
        4800,
        540,
        960,
        540,
    )


def test_macos_points_match_without_scaling() -> None:
    monitors = [
        {"left": 0, "top": 0, "width": 1512, "height": 982},
        {"left": 0, "top": 0, "width": 1512, "height": 982},
    ]
    qt = {"x": 0, "y": 0, "w": 1512, "h": 982, "dpr": 2.0}
    assert geometry.match_monitor(qt, monitors) is monitors[1]


def test_match_monitor_without_monitors_is_none() -> None:
    assert geometry.match_monitor({"x": 0, "y": 0, "w": 1, "h": 1, "dpr": 1}, []) is None


def test_monitor_fraction_clips_to_the_monitor() -> None:
    frac = geometry.monitor_fraction((3740, 0, 300, 1080), _WIN_MONITORS[2])
    assert frac == pytest.approx((0.0, 0.0, 200 / 1920, 1.0))


def test_even_size_trims_odd_dimensions() -> None:
    assert geometry.even_size(1921, 1081) == (1920, 1080)
    assert geometry.even_size(1, 1) == (2, 2)


def test_cards_stack_upward_newest_lowest() -> None:
    corners = geometry.card_stack(1000, 800, [(300, 200), (300, 100)], margin=20, gap=10)
    assert corners == [(680, 580), (680, 470)]


def test_card_timer_expires_after_the_dismiss_time() -> None:
    timer = geometry.CardTimer(persist=False, dismiss_s=30, now=100.0)
    assert not timer.expired(129.9)
    assert timer.expired(130.0)


def test_persistent_card_never_expires() -> None:
    timer = geometry.CardTimer(persist=True, dismiss_s=1, now=0.0)
    assert timer.remaining(10_000.0) is None
    assert not timer.expired(10_000.0)


def test_hover_pauses_and_leaving_restarts_the_countdown() -> None:
    timer = geometry.CardTimer(persist=False, dismiss_s=10, now=0.0)
    timer.hover(True, 8.0)
    assert not timer.expired(50.0)
    timer.hover(False, 50.0)
    assert timer.remaining(55.0) == pytest.approx(5.0)
    assert timer.expired(60.0)


@pytest.mark.parametrize(("raw", "expected"), [(0, 1), (99999, 3600), ("x", 30), (12.7, 12)])
def test_dismiss_seconds_are_clamped(raw: object, expected: int) -> None:
    assert geometry.clamp_dismiss_s(raw) == expected


def test_pill_goes_below_the_region_when_there_is_room() -> None:
    spot = geometry.pill_placement((0.25, 0.25, 0.5, 0.5), 1000, 1000, 120, 30)
    assert not spot.overlaps
    assert spot.y >= 750


def test_pill_goes_above_when_the_region_reaches_the_bottom() -> None:
    spot = geometry.pill_placement((0.25, 0.25, 0.5, 0.75), 1000, 1000, 120, 30)
    assert not spot.overlaps
    assert spot.y + 30 <= 250


def test_full_screen_pill_overlaps_and_says_so() -> None:
    assert geometry.pill_placement(None, 1000, 1000, 120, 30).overlaps
    assert geometry.pill_placement((0.0, 0.0, 1.0, 1.0), 1000, 1000, 120, 30).overlaps


def test_elapsed_format() -> None:
    assert geometry.format_elapsed(12.4) == "00:12"
    assert geometry.format_elapsed(3723) == "1:02:03"
