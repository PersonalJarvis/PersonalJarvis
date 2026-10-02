"""Human-takeover guard for macOS input actuation.

The detector lives in :mod:`jarvis.cu.human_activity`; this module applies its
policy at the common actuator facade. A cached actuator remains safe because
ownership is checked on every mutating primitive rather than only when the
backend object is constructed.

Long typing is split into short chunks so a person taking the keyboard is
noticed during the burst. macOS drags are posted here step-by-step for the same
reason: if physical input appears after button-down, Jarvis stops moving and
releases the button at the user's current cursor position.
"""
from __future__ import annotations

import sys
import time
from typing import Any

from jarvis.core.protocols import ToolResult
from jarvis.cu.actuate.base import (
    LANDING_TOLERANCE,
    ActuationUnavailable,
    Actuator,
)
from jarvis.cu.human_activity import human_input_allows_automation

# Upper bound between ownership checks during deliberately slow typing/dragging.
TAKEOVER_POLL_INTERVAL_S = 0.10
HUMAN_TAKEOVER_OUTCOME = "human_takeover"


class HumanInputTakeover(ActuationUnavailable):
    """Physical user input is active, or macOS ownership cannot be proven."""


def human_takeover_tool_result(exc: BaseException | str) -> ToolResult:
    """Preserve takeover as a structured tool outcome across ToolExecutor."""
    detail = str(exc).strip() or "physical input belongs to the user"
    return ToolResult(
        success=False,
        output={"outcome": HUMAN_TAKEOVER_OUTCOME, "detail": detail},
        error=detail,
    )


def human_takeover_detail(result: Any) -> str | None:
    """Return takeover detail from a structured tool result, else None."""
    if bool(getattr(result, "success", False)):
        return None
    output = getattr(result, "output", None)
    if not isinstance(output, dict):
        return None
    if output.get("outcome") != HUMAN_TAKEOVER_OUTCOME:
        return None
    return str(output.get("detail") or getattr(result, "error", "") or "").strip() or (
        "physical input belongs to the user"
    )


def require_human_input_clear() -> None:
    """Fail closed before synthetic macOS input when ownership is uncertain."""
    if sys.platform != "darwin":
        return
    allowed, detail = human_input_allows_automation()
    if not allowed:
        raise HumanInputTakeover(
            "Pausing macOS Computer-Use because Jarvis does not currently own "
            f"mouse/keyboard input ({detail}). Retry after the user is idle."
        )


def _guarded_macos_drag(
    actuator: Actuator,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    duration_s: float,
) -> None:
    """Quartz drag with ownership checks during the held-button interval."""
    import Quartz  # type: ignore[import-not-found] # noqa: PLC0415

    start = actuator.cursor_pos()
    if start is None or (
        abs(start[0] - int(x1)) > LANDING_TOLERANCE
        or abs(start[1] - int(y1)) > LANDING_TOLERANCE
    ):
        raise RuntimeError(
            "cursor moved after drag-start verification; refusing to drag"
        )

    require_human_input_clear()
    down = Quartz.kCGEventLeftMouseDown
    up = Quartz.kCGEventLeftMouseUp
    dragged = Quartz.kCGEventLeftMouseDragged
    button = Quartz.kCGMouseButtonLeft

    press = Quartz.CGEventCreateMouseEvent(None, down, start, button)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, press)

    steps = max(2, min(40, int(max(0.0, duration_s) * 60)))
    pause = max(0.0, duration_s) / steps
    release_point = start
    takeover = False
    try:
        for index in range(1, steps + 1):
            require_human_input_clear()
            point = (
                int(x1 + (x2 - x1) * index / steps),
                int(y1 + (y2 - y1) * index / steps),
            )
            event = Quartz.CGEventCreateMouseEvent(None, dragged, point, button)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            release_point = point
            if pause:
                time.sleep(pause)
    except HumanInputTakeover:
        takeover = True
        current = actuator.cursor_pos()
        if current is not None:
            release_point = current
        raise
    finally:
        # Never strand a mouse button. During takeover, release exactly where
        # the person moved the cursor instead of forcing the old target point.
        if takeover:
            current = actuator.cursor_pos()
            if current is not None:
                release_point = current
        release = Quartz.CGEventCreateMouseEvent(None, up, release_point, button)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, release)


class HumanTakeoverActuator(Actuator):
    """Thin actuator proxy that yields to physical input on macOS."""

    def __init__(self, delegate: Actuator) -> None:
        self._delegate = delegate
        self.name = delegate.name

    def cursor_pos(self) -> tuple[int, int] | None:
        # Read-only observation never needs ownership of the input stream.
        return self._delegate.cursor_pos()

    def move(self, x: int, y: int) -> None:
        require_human_input_clear()
        self._delegate.move(x, y)

    def click(
        self,
        x: int,
        y: int,
        *,
        button: str = "left",
        double: bool = False,
    ) -> None:
        # Two checks: before pointer movement and immediately before button-down.
        self.move(x, y)
        self.click_at_cursor(
            button=button,
            double=double,
            expected=(int(x), int(y)),
        )

    def click_at_cursor(
        self,
        *,
        button: str = "left",
        double: bool = False,
        expected: tuple[int, int] | None = None,
    ) -> None:
        require_human_input_clear()
        method = getattr(self._delegate, "click_at_cursor", None)
        if not callable(method):
            raise RuntimeError("input backend lacks an at-cursor click primitive")
        method(button=button, double=double, expected=expected)

    def drag(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        duration_s: float = 0.4,
    ) -> None:
        self.move(x1, y1)
        self.drag_from_cursor(x1, y1, x2, y2, duration_s=duration_s)

    def drag_from_cursor(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        duration_s: float = 0.4,
    ) -> None:
        require_human_input_clear()
        if sys.platform == "darwin":
            _guarded_macos_drag(
                self._delegate,
                x1,
                y1,
                x2,
                y2,
                duration_s=max(0.0, duration_s),
            )
            return
        method = getattr(self._delegate, "drag_from_cursor", None)
        if callable(method):
            method(x1, y1, x2, y2, duration_s=max(0.0, duration_s))
            return
        self._delegate.drag(
            x1, y1, x2, y2, duration_s=max(0.0, duration_s)
        )

    def scroll(
        self,
        direction: str,
        notches: int,
        *,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        # Position and scroll are separate ownership boundaries when a target
        # point is supplied, so takeover between them cannot emit a stale wheel.
        if x is not None and y is not None:
            self.move(int(x), int(y))
            require_human_input_clear()
            self._delegate.scroll(direction, notches, x=None, y=None)
            return
        require_human_input_clear()
        self._delegate.scroll(direction, notches, x=x, y=y)

    def key_combo(self, keys: list[str]) -> None:
        require_human_input_clear()
        self._delegate.key_combo(keys)

    def type_text(self, text: str, *, delay_s: float = 0.02) -> Any:
        if sys.platform != "darwin" or delay_s <= 0 or len(text) <= 1:
            require_human_input_clear()
            return self._delegate.type_text(text, delay_s=delay_s)

        chars_per_check = max(
            1,
            min(32, int(TAKEOVER_POLL_INTERVAL_S / max(delay_s, 0.001))),
        )
        dropped_total = 0
        returns_count = False
        for start in range(0, len(text), chars_per_check):
            require_human_input_clear()
            chunk = text[start : start + chars_per_check]
            dropped = self._delegate.type_text(chunk, delay_s=delay_s)
            if dropped is not None:
                returns_count = True
                dropped_total += int(dropped)
        return dropped_total if returns_count else None


def guard_actuator(actuator: Actuator) -> Actuator:
    """Wrap macOS actuators once; leave other platforms untouched."""
    if sys.platform != "darwin" or isinstance(actuator, HumanTakeoverActuator):
        return actuator
    return HumanTakeoverActuator(actuator)


__all__ = [
    "HUMAN_TAKEOVER_OUTCOME",
    "HumanInputTakeover",
    "HumanTakeoverActuator",
    "TAKEOVER_POLL_INTERVAL_S",
    "human_takeover_detail",
    "human_takeover_tool_result",
    "guard_actuator",
    "require_human_input_clear",
]
