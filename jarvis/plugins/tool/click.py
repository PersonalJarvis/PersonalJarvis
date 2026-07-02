"""click tool: simulates mouse clicks at a screen coordinate.

Windows-native input dispatch now delegates to the shared CU v2 actuation
backend (``jarvis/cu/actuate/windows.py``): absolute virtual-desktop
positioning via SendInput (negative-origin monitors included) inside the
per-monitor thread DPI pin, so clicks stay on target on mixed-DPI desktops
even after pywebview flips the process DPI awareness. On macOS/Linux the
tool uses the platform backend from ``jarvis/cu/actuate`` (pynput preferred,
pyautogui fallback) instead of raw pyautogui, which clamps multi-monitor
coordinates.

Risk tier: ``monitor`` — mouse clicks are often not reversible (buttons,
form submits, file operations). A toast notification is shown, no approval
dialog.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from jarvis.control.cursor_motion import glide_os_cursor
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.cu.actuate.windows import (
    _MOUSE_FLAGS_DOWN,
    normalize_virtualdesk as _normalize_virtualdesk,  # noqa: F401 — re-export (tests + callers)
)
from jarvis.overlay.virtual_cursor import get_virtual_cursor


def _send_click(button: str, double: bool, abs_xy: tuple[int, int] | None = None) -> None:
    """Press a mouse button via the shared Windows SendInput backend.

    When ``abs_xy`` is given the input stream is prefixed with an ABSOLUTE
    virtual-desktop move to that pixel, so the click lands exactly there on any
    monitor (negative coords included) regardless of where SetCursorPos left the
    cursor. Without it, the click fires at the current cursor position (the legacy
    behaviour; positioning done beforehand by :func:`glide_os_cursor`).
    """
    from jarvis.cu.actuate.windows import WindowsActuator  # noqa: PLC0415

    actuator = WindowsActuator()
    if abs_xy is not None:
        actuator.click(int(abs_xy[0]), int(abs_xy[1]), button=button, double=double)
    else:
        actuator.click_at_cursor(button=button, double=double)


def _click_windows(x: int, y: int, button: str, double: bool) -> None:
    """Click at an absolute screen coordinate, with a visible cursor glide.

    The real OS cursor glides to ``(x, y)`` (so the user can watch where
    Computer-Use is acting), the virtual-cursor overlay fires a click pulse at
    the target, and only then does the actual button press go out via
    SendInput. ``glide_os_cursor`` lands the cursor exactly on the target, so
    the click never misses — even across a multi-monitor virtual desktop.
    """
    if os.name != "nt":
        raise RuntimeError("Native mouse click is only available on Windows")

    button_l = button.lower()
    if button_l not in _MOUSE_FLAGS_DOWN:
        raise ValueError(f"Unknown mouse button: {button!r}. Allowed: left/right/middle")

    glide_os_cursor(int(x), int(y))
    try:
        get_virtual_cursor().show_click(int(x), int(y), button=button_l, double=double)
    except Exception:  # noqa: BLE001 — overlay must never break a real click
        pass
    # Click ABSOLUTELY on the virtual desktop (negative-X monitors included) so a
    # flaky SetCursorPos during the glide can't make the button-press land on the
    # wrong screen — the glide is now purely the visible cursor animation.
    _send_click(button_l, double, abs_xy=(int(x), int(y)))


class ClickTool:
    name: str = "click"
    risk_tier: str = "monitor"
    description: str = (
        "Clicks the mouse at a screen coordinate. Optional right/middle "
        "mouse button, double-click. Coordinates are absolute "
        "(0,0 = top-left corner of the primary monitor)."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "X coordinate (pixels)"},
            "y": {"type": "integer", "description": "Y coordinate (pixels)"},
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "default": "left",
            },
            "double": {
                "type": "boolean",
                "default": False,
                "description": "Double-click instead of a single click",
            },
        },
        "required": ["x", "y"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        try:
            x = int(args["x"])
            y = int(args["y"])
        except (KeyError, TypeError, ValueError):
            return ToolResult(
                success=False,
                output=None,
                error="x and y must be integer coordinates",
            )
        button = str(args.get("button", "left")).lower()
        double = bool(args.get("double", False))

        if button not in _MOUSE_FLAGS_DOWN:
            return ToolResult(
                success=False,
                output=None,
                error=f"Unknown button={button!r}. Allowed: left/right/middle",
            )

        if os.name == "nt":
            try:
                await asyncio.to_thread(_click_windows, x, y, button, double)
                kind = "double-click" if double else "click"
                return ToolResult(
                    success=True,
                    output=f"{kind} ({button}) at ({x}, {y})",
                )
            except (ValueError, OSError) as exc:
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"Click at ({x},{y}) failed: {exc}",
                )

        from jarvis.cu.actuate import ActuationUnavailable, get_actuator

        try:
            actuator = get_actuator()
            await asyncio.to_thread(
                actuator.click, x, y, button=button, double=double,
            )
            return ToolResult(
                success=True,
                output=(
                    f"{'Double-' if double else ''}click ({actuator.name}) "
                    f"at ({x},{y})"
                ),
            )
        except ActuationUnavailable as exc:
            return ToolResult(success=False, output=None, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))
