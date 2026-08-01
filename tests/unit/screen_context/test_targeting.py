"""Surface selection: cursor monitor first, bar second, primary last.

The multi-monitor rule is the one behaviour a user notices immediately when it
is wrong ("it described the other screen"), so the fallback chain and the
divergence from the foreground-window heuristic are pinned here explicitly.
"""
from __future__ import annotations

import pytest

from jarvis.screen_context.models import (
    DegradationCode,
    TargetKind,
    TargetReason,
    VisualIntent,
    WindowFacts,
)
from jarvis.screen_context.ports import CaptureUnavailable
from jarvis.screen_context.targeting import monitor_for_point, resolve_target

# A realistic asymmetric layout: primary at the origin, a second screen to its
# LEFT (negative virtual X) — the arrangement that breaks naive "monitors[1]"
# and "origin == (0,0)" assumptions.
LEFT = {"left": -1920, "top": 0, "width": 1920, "height": 1080, "name": "left"}
PRIMARY = {"left": 0, "top": 0, "width": 2560, "height": 1440, "name": "primary"}
VIRTUAL = {"left": -1920, "top": 0, "width": 4480, "height": 1440, "name": "virtual"}
MONITORS = [VIRTUAL, LEFT, PRIMARY]


def test_point_inside_a_monitor_selects_it() -> None:
    assert monitor_for_point(MONITORS, (-800, 500)) is LEFT
    assert monitor_for_point(MONITORS, (1000, 500)) is PRIMARY


def test_virtual_bounding_box_is_never_selected() -> None:
    """Returning [0] would capture every monitor — the opposite of the rule."""
    for point in ((-800, 500), (1000, 500), (99_999, 99_999)):
        assert monitor_for_point(MONITORS, point) is not VIRTUAL


def test_point_outside_every_monitor_falls_to_the_nearest() -> None:
    """A cursor in an L-shaped layout gap must not fail the capture."""
    assert monitor_for_point(MONITORS, (-1920, 5000)) is LEFT
    assert monitor_for_point(MONITORS, (10_000, 100)) is PRIMARY


def test_cursor_monitor_wins_over_the_foreground_window() -> None:
    """The whole point of the rule: the user's eyes, not the focus.

    The focused window sits on the primary screen while the cursor is on the
    left one. Every other capture path in the tree would grab the primary.
    """
    target, degradations = resolve_target(
        VisualIntent.SCREEN,
        monitors=MONITORS,
        cursor_point=(-800, 400),
        bar_point=(1200, 1400),
        window=WindowFacts(title="Editor", frame_rect=(100, 100, 800, 600)),
    )
    assert target.kind is TargetKind.MONITOR
    assert target.bbox == (-1920, 0, 1920, 1080)
    assert target.reason is TargetReason.CURSOR_MONITOR
    assert degradations == ()


def test_bar_monitor_is_the_documented_cursor_fallback() -> None:
    target, degradations = resolve_target(
        VisualIntent.SCREEN,
        monitors=MONITORS,
        cursor_point=None,
        bar_point=(-1000, 900),
        window=None,
    )
    assert target.reason is TargetReason.BAR_MONITOR
    assert target.bbox == (-1920, 0, 1920, 1080)
    codes = {d.code for d in degradations}
    assert DegradationCode.NO_CURSOR in codes, "a silent fallback is a lie (AP-30)"


def test_primary_is_the_last_resort() -> None:
    target, degradations = resolve_target(
        VisualIntent.SCREEN,
        monitors=MONITORS,
        cursor_point=None,
        bar_point=None,
        window=None,
    )
    assert target.reason is TargetReason.PRIMARY_MONITOR
    codes = {d.code for d in degradations}
    assert {DegradationCode.NO_CURSOR, DegradationCode.NO_BAR_POSITION} <= codes


def test_window_scope_captures_the_window() -> None:
    target, _ = resolve_target(
        VisualIntent.WINDOW,
        monitors=MONITORS,
        cursor_point=(1000, 500),
        bar_point=None,
        window=WindowFacts(title="Report.pdf", frame_rect=(200, 150, 1200, 900)),
        window_handle=4242,
    )
    assert target.kind is TargetKind.WINDOW
    assert target.bbox == (200, 150, 1200, 900)
    assert target.window_handle == 4242
    assert target.reason is TargetReason.FOCUSED_WINDOW


def test_unusable_window_rect_falls_back_to_the_monitor() -> None:
    """A minimized window (Windows parks these at -32000) must not be shot."""
    target, degradations = resolve_target(
        VisualIntent.WINDOW,
        monitors=MONITORS,
        cursor_point=(1000, 500),
        bar_point=None,
        window=WindowFacts(title="Mail", frame_rect=(-32000, -32000, 0, 0)),
    )
    assert target.kind is TargetKind.MONITOR
    assert target.reason is TargetReason.WINDOW_FALLBACK_MONITOR
    assert DegradationCode.WINDOW_RECT_UNUSABLE in {d.code for d in degradations}


def test_stale_offscreen_window_rect_falls_back_to_a_physical_monitor() -> None:
    target, degradations = resolve_target(
        VisualIntent.WINDOW,
        monitors=MONITORS,
        cursor_point=(1000, 500),
        bar_point=None,
        window=WindowFacts(
            title="Disconnected display",
            frame_rect=(20_000, 20_000, 1200, 900),
        ),
        window_handle=4242,
    )

    assert target.kind is TargetKind.MONITOR
    assert target.bbox == (
        PRIMARY["left"],
        PRIMARY["top"],
        PRIMARY["width"],
        PRIMARY["height"],
    )
    assert target.window_handle is None
    assert DegradationCode.WINDOW_RECT_UNUSABLE in {item.code for item in degradations}


def test_screen_intent_never_narrows_to_the_window() -> None:
    """"Look at this" means the screen; only a window-scoped turn narrows."""
    target, _ = resolve_target(
        VisualIntent.SCREEN,
        monitors=MONITORS,
        cursor_point=(1000, 500),
        bar_point=None,
        window=WindowFacts(title="Editor", frame_rect=(100, 100, 800, 600)),
    )
    assert target.kind is TargetKind.MONITOR


def test_window_facts_travel_with_a_monitor_capture() -> None:
    """"Which app was the user in" is context even for a full-screen shot."""
    facts = WindowFacts(app_name="editor", title="notes.md", pid=99)
    target, _ = resolve_target(
        VisualIntent.SCREEN,
        monitors=MONITORS,
        cursor_point=(1000, 500),
        bar_point=None,
        window=facts,
    )
    assert target.window is facts


def test_headless_host_refuses_instead_of_guessing() -> None:
    with pytest.raises(CaptureUnavailable) as excinfo:
        resolve_target(
            VisualIntent.SCREEN,
            monitors=[],
            cursor_point=None,
            bar_point=None,
            window=None,
        )
    message = str(excinfo.value)
    assert "headless" in message.lower() or "no display" in message.lower()


def test_single_monitor_host_works() -> None:
    """mss reports [virtual, only-screen]; both entries describe one screen."""
    solo = [
        {"left": 0, "top": 0, "width": 1920, "height": 1080},
        {"left": 0, "top": 0, "width": 1920, "height": 1080},
    ]
    target, _ = resolve_target(
        VisualIntent.SCREEN,
        monitors=solo,
        cursor_point=(500, 500),
        bar_point=None,
        window=None,
    )
    assert target.bbox == (0, 0, 1920, 1080)
