"""scroll tool: scroll at the current cursor position or a verified target.

Windows uses native ``SendInput``; macOS uses native Quartz wheel events; X11
uses the available pynput/pyautogui desktop backend. Wayland, headless hosts,
and missing desktop extras degrade with an actionable error.

This is the missing scroll primitive for computer-use: without it, lists (contacts,
chats, file pickers) cannot be scrolled.

Risk-Tier: ``monitor`` — scrolling is non-destructive but moves the viewport and can
change what subsequent clicks land on. Toast notification visible, no approval dialog.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

# One wheel notch in Windows units (WHEEL_DELTA).
_WHEEL_DELTA: int = 120

# Win32 mouse-event flags for wheel scrolling.
_MOUSEEVENTF_WHEEL: int = 0x0800   # vertical wheel
_MOUSEEVENTF_HWHEEL: int = 0x01000  # horizontal wheel

_VALID_DIRECTIONS: frozenset[str] = frozenset({"up", "down", "left", "right"})


def _notch_for(direction: str, amount: int) -> int:
    """Return the signed wheel delta for ``direction`` and ``amount`` notches.

    Vertical: "up" is positive, "down" is negative (WHEEL_DELTA down = -120).
    Horizontal: "right" is positive, "left" is negative.
    """
    magnitude = abs(int(amount)) * _WHEEL_DELTA
    if direction in ("down", "left"):
        return -magnitude
    return magnitude


def _scroll_with_verified_target(
    actuator: Any,
    direction: str,
    amount: int,
    x: int | None,
    y: int | None,
    *,
    expected_window_signature: tuple[Any, ...] | None = None,
) -> None:
    """Land on an explicit scroll target before emitting the wheel event."""
    if x is not None and y is not None:
        from jarvis.cu.actuate import verified_move  # noqa: PLC0415

        landing = verified_move(actuator, int(x), int(y))
        if not landing.ok:
            raise RuntimeError(landing.detail)
    if expected_window_signature is not None:
        from jarvis.cu.target_guard import (  # noqa: PLC0415
            foreground_matches_or_same_app,
        )

        if not foreground_matches_or_same_app(expected_window_signature):
            raise RuntimeError("foreground window changed after the screenshot")
    actuator.scroll(direction, amount)


def _scroll_windows(
    direction: str,
    amount: int,
    x: int | None,
    y: int | None,
    *,
    expected_window_signature: tuple[Any, ...] | None = None,
) -> int:
    """Scroll via Win32 ``SendInput``; returns the signed wheel delta transmitted.

    Delegates to the shared CU v2 Windows backend. If both ``x`` and ``y``
    are given, the wheel event is prefixed with an ABSOLUTE virtual-desktop
    move (negative-origin monitors included) — an upgrade over the previous
    ``SetCursorPos``, which is unreliable across the primary boundary.
    """
    if os.name != "nt":
        raise RuntimeError("Native scrolling is only available on Windows")

    direction_l = direction.lower()
    if direction_l not in _VALID_DIRECTIONS:
        raise ValueError(
            f"Unknown direction: {direction!r}. Allowed: up/down/left/right"
        )

    from jarvis.cu.actuate.windows import WindowsActuator  # noqa: PLC0415

    _scroll_with_verified_target(
        WindowsActuator(),
        direction_l,
        amount,
        x,
        y,
        expected_window_signature=expected_window_signature,
    )
    return _notch_for(direction_l, amount)


def _scroll_posix(
    direction: str,
    amount: int,
    x: int | None,
    y: int | None,
    *,
    expected_window_signature: tuple[Any, ...] | None = None,
) -> int:
    """Cross-platform scroll via the actuate backend (pynput preferred)."""
    from jarvis.cu.actuate import get_actuator

    _scroll_with_verified_target(
        get_actuator(),
        direction.lower(),
        amount,
        x,
        y,
        expected_window_signature=expected_window_signature,
    )
    return _notch_for(direction.lower(), amount)


class ScrollTool:
    name: str = "scroll"
    risk_tier: str = "monitor"
    description: str = (
        "Scrolls the mouse wheel in a given direction. Use to scroll lists "
        "(contacts, chats, file pickers) and pages. Optionally targets a "
        "screen coordinate (x, y) by moving the cursor there first. Amount is "
        "the number of wheel notches."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["up", "down", "left", "right"],
                "description": "Scroll direction",
            },
            "amount": {
                "type": "integer",
                "default": 3,
                "description": "Number of wheel notches to scroll",
            },
            "x": {
                "type": "integer",
                "description": "Optional X coordinate to target (requires y)",
            },
            "y": {
                "type": "integer",
                "description": "Optional Y coordinate to target (requires x)",
            },
        },
        "required": ["direction"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        direction = str(args.get("direction", "")).lower()
        if direction not in _VALID_DIRECTIONS:
            return ToolResult(
                success=False,
                output=None,
                error=(
                    f"Invalid or missing direction={args.get('direction')!r}. "
                    "Allowed: up/down/left/right"
                ),
            )

        try:
            amount = int(args.get("amount", 3))
        except (TypeError, ValueError):
            return ToolResult(
                success=False,
                output=None,
                error="amount must be an integer number of wheel notches",
            )

        # Coordinates are only used when BOTH are present.
        x: int | None = None
        y: int | None = None
        if args.get("x") is not None and args.get("y") is not None:
            try:
                x = int(args["x"])
                y = int(args["y"])
            except (TypeError, ValueError):
                return ToolResult(
                    success=False,
                    output=None,
                    error="x and y must be integer coordinates",
                )

        expected_raw = args.get("_expected_window_signature")
        if expected_raw is not None and not isinstance(expected_raw, (list, tuple)):
            return ToolResult(
                success=False,
                output=None,
                error="Refusing scroll: invalid captured-window identity.",
            )
        expected_signature = (
            tuple(expected_raw) if expected_raw is not None else None
        )

        if os.name == "nt":
            try:
                await asyncio.to_thread(
                    _scroll_windows,
                    direction,
                    amount,
                    x,
                    y,
                    expected_window_signature=expected_signature,
                )
                return ToolResult(
                    success=True,
                    output=f"Scrolled {direction} by {amount}",
                )
            except (ValueError, OSError, RuntimeError) as exc:
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"Scroll {direction} by {amount} failed: {exc}",
                )

        from jarvis.cu.actuate import ActuationUnavailable

        try:
            await asyncio.to_thread(
                _scroll_posix,
                direction,
                amount,
                x,
                y,
                expected_window_signature=expected_signature,
            )
            return ToolResult(
                success=True,
                output=f"Scrolled {direction} by {amount}",
            )
        except ActuationUnavailable as exc:
            return ToolResult(success=False, output=None, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))
