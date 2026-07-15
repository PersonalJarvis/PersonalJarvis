"""move_mouse tool: moves the mouse cursor without clicking.

Risk tier: ``safe`` — the movement itself does not trigger an
app state change. Only a click would do that.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from jarvis.control.cursor_motion import glide_os_cursor
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.overlay.virtual_cursor import get_virtual_cursor


def _move_windows(x: int, y: int) -> None:
    """Glide the real cursor to ``(x, y)`` and mirror it on the overlay.

    Movement is animated (eased glide) instead of an instant teleport so the
    user can see the mouse travel; the virtual-cursor overlay tracks it.
    """
    if os.name != "nt":
        raise RuntimeError("Native mouse movement is only available on Windows")

    from jarvis.cu.actuate.base import verified_move  # noqa: PLC0415
    from jarvis.cu.actuate.windows import WindowsActuator  # noqa: PLC0415

    glide_os_cursor(int(x), int(y))
    landing = verified_move(WindowsActuator(), int(x), int(y))
    if not landing.ok:
        raise OSError(landing.detail)
    try:
        get_virtual_cursor().show_move(int(x), int(y))
    except Exception:  # noqa: BLE001, S110 — overlay must never break a move
        pass


class MoveMouseTool:
    name: str = "move_mouse"
    risk_tier: str = "safe"
    description: str = "Moves the mouse cursor to absolute screen coordinates without clicking."
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "X coordinate (pixels)"},
            "y": {"type": "integer", "description": "Y coordinate (pixels)"},
        },
        "required": ["x", "y"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        try:
            x = int(args["x"])
            y = int(args["y"])
        except (KeyError, TypeError, ValueError):
            return ToolResult(
                success=False, output=None,
                error="x and y must be integer coordinates",
            )

        if os.name == "nt":
            try:
                await asyncio.to_thread(_move_windows, x, y)
                return ToolResult(success=True, output=f"Mouse at ({x}, {y})")
            except OSError as exc:
                return ToolResult(success=False, output=None, error=str(exc))

        # Non-Windows: resolve the input backend via the capability probe so
        # Wayland/headless/missing-deps hosts fail with the actionable
        # ActuationUnavailable message instead of a raw pyautogui error.
        from jarvis.cu.actuate.base import (
            ActuationUnavailable,
            get_actuator,
            verified_move,
        )

        try:
            actuator = get_actuator()
            landing = await asyncio.to_thread(verified_move, actuator, x, y)
            if not landing.ok:
                return ToolResult(success=False, output=None, error=landing.detail)
            return ToolResult(
                success=True, output=f"Mouse ({actuator.name}) at ({x},{y})"
            )
        except ActuationUnavailable as exc:
            return ToolResult(success=False, output=None, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))
