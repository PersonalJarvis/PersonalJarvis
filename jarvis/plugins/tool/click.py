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
)
from jarvis.cu.actuate.windows import (
    normalize_virtualdesk as _normalize_virtualdesk,  # noqa: F401 — re-export (tests + callers)
)
from jarvis.cu.target_guard import (
    foreground_matches_or_same_app,
    foreground_signature,
)
from jarvis.overlay.virtual_cursor import get_virtual_cursor


def _foreground_window_signature() -> tuple[Any, ...]:
    return foreground_signature()


def _window_signature_matches(expected: tuple[Any, ...]) -> bool:
    # Same-app tolerant (macOS): the engine holds the strict per-frame
    # baseline; this later re-check must not refuse a batch because our own
    # click just made a dropdown/sheet the frontmost same-app window.
    return foreground_matches_or_same_app(expected)


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


def _click_windows(
    x: int,
    y: int,
    button: str,
    double: bool,
    *,
    expected_window_signature: tuple[Any, ...] | None = None,
) -> None:
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

    from jarvis.cu.actuate.base import verified_move  # noqa: PLC0415
    from jarvis.cu.actuate.windows import WindowsActuator  # noqa: PLC0415

    glide_os_cursor(int(x), int(y))
    actuator = WindowsActuator()
    landing = verified_move(actuator, int(x), int(y))
    if not landing.ok:
        raise OSError(landing.detail)
    try:
        get_virtual_cursor().show_click(int(x), int(y), button=button_l, double=double)
    except Exception:  # noqa: BLE001, S110 — overlay must never break a real click
        pass
    if (
        expected_window_signature is not None
        and not _window_signature_matches(expected_window_signature)
    ):
        raise OSError(
            "foreground window changed during cursor movement; refusing to click",
        )
    actuator.click_at_cursor(
        button=button_l,
        double=double,
        expected=(int(x), int(y)),
    )


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

        current_signature = _foreground_window_signature()
        expected_raw = args.get("_expected_window_signature")
        if expected_raw is not None and not isinstance(expected_raw, (list, tuple)):
            return ToolResult(
                success=False, output=None,
                error="Refusing click: invalid captured-window identity.",
            )
        expected_signature = (
            tuple(expected_raw) if expected_raw is not None else current_signature
        )
        if not _window_signature_matches(expected_signature):
            return ToolResult(
                success=False,
                output=None,
                error=(
                    "Refusing click: foreground window identity is unavailable "
                    "or changed after the screenshot."
                ),
            )

        if os.name == "nt":
            try:
                await asyncio.to_thread(
                    _click_windows,
                    x,
                    y,
                    button,
                    double,
                    expected_window_signature=expected_signature,
                )
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

        from jarvis.cu.actuate import (
            ActuationUnavailable,
            get_actuator,
            verified_click,
        )

        try:
            actuator = get_actuator()
            landing = await asyncio.to_thread(
                verified_click,
                actuator,
                x,
                y,
                button=button,
                double=double,
                pre_action_check=lambda: _window_signature_matches(
                    expected_signature,
                ),
            )
            if not landing.ok:
                return ToolResult(success=False, output=None, error=landing.detail)
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
