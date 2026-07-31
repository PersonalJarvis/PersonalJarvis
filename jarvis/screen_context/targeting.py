"""Decides WHICH surface a capture grabs — before any pixels move.

The rule the feature is specified around: **the monitor the mouse cursor is on
at trigger time**, with the on-screen bar's monitor as the fallback when no
cursor is readable, and the OS primary as the last resort.

This differs on purpose from every other capture path in the tree, which
follows the *foreground window* (``jarvis.vision.screenshot.select_capture_monitor``).
The two diverge constantly and visibly: the user reads an error on the right
screen while the focused window still sits on the left. Following the window
there captures the screen the user is not looking at — which is the single most
confusing failure this feature can have, because the answer sounds confident and
describes the wrong screen.

Everything here is pure given its inputs. The cursor point is *passed in*, never
read: see :func:`resolve_target` for why that is a correctness requirement and
not a testing convenience.
"""
from __future__ import annotations

import logging

from jarvis.screen_context.models import (
    CaptureTarget,
    Degradation,
    DegradationCode,
    TargetKind,
    TargetReason,
    VisualIntent,
    WindowFacts,
)
from jarvis.screen_context.ports import CaptureUnavailable, Point, Rect

log = logging.getLogger(__name__)

#: A window narrower or shorter than this is treated as unusable — a collapsed,
#: minimized (Windows parks these at -32000) or mid-animation window would
#: otherwise produce a capture of a sliver of nothing.
_MIN_WINDOW_EXTENT_PX = 64


def _rect_of(monitor: dict) -> Rect:
    return (
        int(monitor.get("left", 0)),
        int(monitor.get("top", 0)),
        int(monitor.get("width", 0)),
        int(monitor.get("height", 0)),
    )


def _contains(monitor: dict, point: Point) -> bool:
    left, top, width, height = _rect_of(monitor)
    x, y = point
    return left <= x < left + width and top <= y < top + height


def _distance_to(monitor: dict, point: Point) -> int:
    """Squared distance from ``point`` to the monitor rect (0 when inside)."""
    left, top, width, height = _rect_of(monitor)
    x, y = point
    dx = max(left - x, 0, x - (left + width - 1))
    dy = max(top - y, 0, y - (top + height - 1))
    return dx * dx + dy * dy


def monitor_for_point(monitors: list[dict], point: Point) -> dict | None:
    """The monitor containing ``point``, else the nearest one.

    ``monitors`` is mss-shaped: ``[0]`` is the virtual bounding box over every
    screen, ``[1:]`` are the physical ones. Only physical screens are
    candidates — returning the virtual box would capture every monitor at once,
    which is precisely what "only the monitor the cursor is on" rules out.

    Nearest-match rather than ``None`` for a point outside every screen: a
    cursor can legitimately sit in an L-shaped layout gap or one pixel past an
    edge, and refusing to capture there would be a mysterious intermittent
    failure. This mirrors Windows' own ``MONITOR_DEFAULTTONEAREST``.
    """
    physical = monitors[1:] if len(monitors) > 1 else monitors
    if not physical:
        return None
    for monitor in physical:
        if _contains(monitor, point):
            return monitor
    nearest = min(physical, key=lambda m: _distance_to(m, point))
    log.debug(
        "screen_context: point %s is on no monitor — using nearest %s",
        point,
        nearest.get("name"),
    )
    return nearest


def _primary_monitor(monitors: list[dict], *, override: str = "primary") -> dict | None:
    """The OS primary, via the shared resolver (honours ``main_monitor``)."""
    physical = monitors[1:] if len(monitors) > 1 else monitors
    if not physical:
        return None
    try:
        from jarvis.platform.monitors import resolve_primary_monitor  # noqa: PLC0415

        return resolve_primary_monitor(monitors, override=override)
    except Exception:  # noqa: BLE001 — never fail targeting over a probe
        log.debug("primary monitor resolution failed", exc_info=True)
        return physical[0]


def _monitor_name(monitor: dict, monitors: list[dict]) -> str:
    """A stable, human-readable identity for the receipt.

    mss does not name monitors, so an index-based label is synthesized. It is
    what the user is told ("captured monitor 2"), so it must be the same label
    across captures — the index into the enumeration is, the geometry is not.
    """
    explicit = str(monitor.get("name", "") or "").strip()
    if explicit:
        return explicit
    physical = monitors[1:] if len(monitors) > 1 else monitors
    for index, candidate in enumerate(physical, start=1):
        if _rect_of(candidate) == _rect_of(monitor):
            return f"{index}"
    return "?"


def _window_rect_is_usable(rect: Rect | None, monitors: list[dict]) -> bool:
    if rect is None:
        return False
    left, top, width, height = rect
    if width < _MIN_WINDOW_EXTENT_PX or height < _MIN_WINDOW_EXTENT_PX:
        return False
    physical = monitors[1:] if len(monitors) > 1 else monitors
    for monitor in physical:
        monitor_left, monitor_top, monitor_width, monitor_height = _rect_of(monitor)
        visible_width = min(left + width, monitor_left + monitor_width) - max(
            left,
            monitor_left,
        )
        visible_height = min(top + height, monitor_top + monitor_height) - max(
            top,
            monitor_top,
        )
        if (
            visible_width >= _MIN_WINDOW_EXTENT_PX
            and visible_height >= _MIN_WINDOW_EXTENT_PX
        ):
            return True
    return False


def resolve_target(
    intent: VisualIntent,
    *,
    monitors: list[dict],
    cursor_point: Point | None,
    bar_point: Point | None,
    window: WindowFacts | None,
    window_handle: int | None = None,
    main_monitor_override: str = "primary",
) -> tuple[CaptureTarget, tuple[Degradation, ...]]:
    """Pick the surface to capture and report what had to be worked around.

    ``cursor_point`` is passed in rather than read here, and that is a
    correctness requirement: the cursor must be sampled ONCE at trigger time
    and threaded through the whole capture. Reading it again at grab time lets
    a mouse move between the decision and the shutter change which screen is
    captured — a race that surfaces as "it photographed the wrong monitor" and
    is essentially impossible to reproduce on demand.

    Raises :class:`CaptureUnavailable` when the host has no addressable display
    at all (headless server, Wayland). That is the one condition targeting
    cannot degrade around: there is no screen to pick.
    """
    degradations: list[Degradation] = []
    facts = window or WindowFacts()

    if not monitors:
        raise CaptureUnavailable(
            "No display is available on this machine, so there is nothing to "
            "capture. This is expected on a headless server; on Wayland, "
            "screen capture has to go through the compositor and is not "
            "available to the app directly."
        )

    # ---- Window scope: only when the user actually scoped it there --------
    if intent is VisualIntent.WINDOW:
        if _window_rect_is_usable(facts.frame_rect, monitors):
            assert facts.frame_rect is not None  # narrowed by the check above
            return (
                CaptureTarget(
                    kind=TargetKind.WINDOW,
                    bbox=facts.frame_rect,
                    reason=TargetReason.FOCUSED_WINDOW,
                    monitor_name=_monitor_name(
                        monitor_for_point(
                            monitors,
                            (facts.frame_rect[0], facts.frame_rect[1]),
                        )
                        or {},
                        monitors,
                    ),
                    window=facts,
                    window_handle=window_handle,
                ),
                (),
            )
        degradations.append(
            Degradation(
                code=DegradationCode.WINDOW_RECT_UNUSABLE,
                message=(
                    "The focused window could not be measured (it may be "
                    "minimized or still opening), so the whole screen was "
                    "captured instead."
                ),
            )
        )
        reason_when_falling_back = TargetReason.WINDOW_FALLBACK_MONITOR
    else:
        reason_when_falling_back = None

    # ---- Monitor scope: cursor, then bar, then primary --------------------
    chosen: dict | None = None
    reason = TargetReason.PRIMARY_MONITOR

    if cursor_point is not None:
        chosen = monitor_for_point(monitors, cursor_point)
        if chosen is not None:
            reason = TargetReason.CURSOR_MONITOR
    else:
        degradations.append(
            Degradation(
                code=DegradationCode.NO_CURSOR,
                message=(
                    "The mouse position could not be read on this system, so "
                    "the screen showing the Jarvis bar was used instead."
                ),
            )
        )

    if chosen is None and bar_point is not None:
        chosen = monitor_for_point(monitors, bar_point)
        if chosen is not None:
            reason = TargetReason.BAR_MONITOR

    if chosen is None:
        if cursor_point is None and bar_point is None:
            degradations.append(
                Degradation(
                    code=DegradationCode.NO_BAR_POSITION,
                    message=(
                        "Neither the mouse position nor the Jarvis bar position "
                        "was available, so the main screen was used."
                    ),
                )
            )
        chosen = _primary_monitor(monitors, override=main_monitor_override)
        reason = TargetReason.PRIMARY_MONITOR

    if chosen is None:
        raise CaptureUnavailable(
            "No usable monitor could be selected on this machine, so nothing "
            "was captured."
        )

    # A window-scoped request that fell back keeps that fact in the reason, so
    # the receipt says "whole screen" rather than implying the window was shot.
    if reason_when_falling_back is not None:
        reason = reason_when_falling_back

    return (
        CaptureTarget(
            kind=TargetKind.MONITOR,
            bbox=_rect_of(chosen),
            reason=reason,
            monitor_name=_monitor_name(chosen, monitors),
            window=facts,
            window_handle=None,
        ),
        tuple(degradations),
    )


__all__ = ["monitor_for_point", "resolve_target"]
