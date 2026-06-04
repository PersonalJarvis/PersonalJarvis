"""scroll tool: simulates mouse-wheel scrolling at the current (or a given) cursor position.

Win32-native via ``SendInput`` with ``MOUSEEVENTF_WHEEL`` / ``MOUSEEVENTF_HWHEEL``.
Falls back to ``pyautogui.scroll`` / ``pyautogui.hscroll`` when Win32 is not available,
so the tests run on Linux/Mac even though real scrolling only works on Windows.

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


def _scroll_windows(direction: str, amount: int, x: int | None, y: int | None) -> int:
    """Scroll via Win32 ``SendInput``; returns the signed wheel delta transmitted.

    If both ``x`` and ``y`` are given, the cursor is moved there first via
    ``SetCursorPos`` so the wheel event targets that region/window. The struct
    layout mirrors ``click.py`` so that ``ctypes.sizeof(INPUT) == 40`` on x64
    (the cbSize bug class).
    """
    if os.name != "nt":
        raise RuntimeError("Native scrolling is only available on Windows")

    direction_l = direction.lower()
    if direction_l not in _VALID_DIRECTIONS:
        raise ValueError(
            f"Unknown direction: {direction!r}. Allowed: up/down/left/right"
        )

    import ctypes
    from ctypes import wintypes

    INPUT_MOUSE = 0
    ULONG_PTR = wintypes.WPARAM

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = (
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        )

    class INPUT_UNION(ctypes.Union):
        _fields_ = (("mi", MOUSEINPUT),)

    class INPUT(ctypes.Structure):
        _fields_ = (("type", wintypes.DWORD), ("union", INPUT_UNION))

    user32 = ctypes.windll.user32
    set_cursor = user32.SetCursorPos
    set_cursor.argtypes = (ctypes.c_int, ctypes.c_int)
    set_cursor.restype = wintypes.BOOL
    send_input = user32.SendInput
    send_input.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    send_input.restype = wintypes.UINT

    # Move the cursor to the target first, so the wheel event hits that window.
    if x is not None and y is not None:
        if not set_cursor(int(x), int(y)):
            raise ctypes.WinError(ctypes.get_last_error())

    notch = _notch_for(direction_l, amount)
    flag = _MOUSEEVENTF_HWHEEL if direction_l in ("left", "right") else _MOUSEEVENTF_WHEEL

    # mouseData is a DWORD; transmit the signed delta as its unsigned two's-complement
    # representation so negative wheel deltas (down/left) arrive correctly.
    mouse_data = ctypes.c_long(notch).value & 0xFFFFFFFF

    event = INPUT(
        type=INPUT_MOUSE,
        union=INPUT_UNION(mi=MOUSEINPUT(0, 0, mouse_data, flag, 0, ULONG_PTR(0))),
    )
    arr = (INPUT * 1)(event)
    sent = send_input(1, arr, ctypes.sizeof(INPUT))
    if sent != 1:
        raise ctypes.WinError(ctypes.get_last_error())
    return notch


def _scroll_pyautogui(direction: str, amount: int, x: int | None, y: int | None) -> int:
    """Cross-platform fallback via pyautogui; returns the signed wheel delta."""
    import pyautogui

    notch = _notch_for(direction.lower(), amount)
    if x is not None and y is not None:
        pyautogui.moveTo(x, y)
    if direction.lower() in ("left", "right"):
        pyautogui.hscroll(notch)
    else:
        pyautogui.scroll(notch)
    return notch


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

        if os.name == "nt":
            try:
                await asyncio.to_thread(_scroll_windows, direction, amount, x, y)
                return ToolResult(
                    success=True,
                    output=f"Scrolled {direction} by {amount}",
                )
            except (ValueError, OSError) as exc:
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"Scroll {direction} by {amount} failed: {exc}",
                )

        try:
            await asyncio.to_thread(_scroll_pyautogui, direction, amount, x, y)
            return ToolResult(
                success=True,
                output=f"Scrolled {direction} by {amount}",
            )
        except ImportError as exc:
            return ToolResult(
                success=False,
                output=None,
                error=(
                    f"Platform is not Windows ({os.name}) and pyautogui is missing: "
                    f"{exc}. pip install pyautogui"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))
