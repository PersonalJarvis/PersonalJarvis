"""move_mouse-Tool: bewegt den Mauszeiger ohne zu klicken.

Risk-Tier: ``safe`` — die Bewegung selbst loest keinen App-State-Change
aus. Erst ein Klick wuerde das tun.
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
        raise RuntimeError("Native Mausbewegung nur auf Windows verfuegbar")

    glide_os_cursor(int(x), int(y))
    try:
        get_virtual_cursor().show_move(int(x), int(y))
    except Exception:  # noqa: BLE001 — overlay must never break a move
        pass


class MoveMouseTool:
    name: str = "move_mouse"
    risk_tier: str = "safe"
    description: str = "Bewegt den Mauszeiger an absolute Bildschirm-Koordinaten ohne zu klicken."
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "X-Koordinate (Pixel)"},
            "y": {"type": "integer", "description": "Y-Koordinate (Pixel)"},
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
                error="x und y muessen Integer-Koordinaten sein",
            )

        if os.name == "nt":
            try:
                await asyncio.to_thread(_move_windows, x, y)
                return ToolResult(success=True, output=f"Maus an ({x}, {y})")
            except OSError as exc:
                return ToolResult(success=False, output=None, error=str(exc))

        try:
            import pyautogui
            pyautogui.moveTo(x, y)
            return ToolResult(success=True, output=f"Maus (pyautogui) an ({x},{y})")
        except ImportError as exc:
            return ToolResult(
                success=False, output=None,
                error=f"Plattform nicht Windows und pyautogui fehlt: {exc}",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output=None, error=str(exc))
