"""drag-Tool: press-and-hold drag from one absolute pixel to another.

The press-move-release gesture a plain click cannot do — rotating a map/globe,
panning a canvas, or moving a slider. Coordinates are ABSOLUTE screen pixels (the
Computer-Use loop resolves its 0-1000 normalized grid to pixels before calling
this tool, exactly as it does for ``click``).

Risk-Tier: ``monitor`` — a drag can move/resize/reorder things and is not always
cleanly reversible, but it raises no irreversible system change. Routing it
through this tool (instead of an inline call) gives the drag the same risk-tier /
blacklist / audit path as every other Computer-Use action (audit #13).

pyautogui is imported lazily so the module still loads on a non-desktop host (the
harness is desktop-gated anyway); on a host without pyautogui the tool fails
gracefully with a clear message rather than raising.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

#: Default drag duration (ms) — slow enough that a map/globe/slider registers the
#: press-move-release as a real gesture rather than an instantaneous teleport.
_DEFAULT_DRAG_DURATION_MS = 400


def _perform_drag(x1: int, y1: int, x2: int, y2: int, duration_s: float) -> None:
    """Press left at ``(x1, y1)``, drag to ``(x2, y2)``, release. Blocking;
    callers run it via ``asyncio.to_thread``.

    Windows keeps the proven pyautogui path unchanged. Elsewhere the backend
    is resolved via the capability probe so Wayland/headless/missing-deps
    hosts raise ``ActuationUnavailable`` with the actionable message instead
    of a raw pyautogui error.
    """
    if os.name == "nt":
        import pyautogui  # noqa: PLC0415 — lazy: keeps non-desktop import clean

        pyautogui.moveTo(x1, y1)
        pyautogui.dragTo(x2, y2, duration=max(0.0, duration_s), button="left")
        return

    from jarvis.cu.actuate.base import get_actuator  # noqa: PLC0415

    get_actuator().drag(x1, y1, x2, y2, duration_s=max(0.0, duration_s))


class DragTool:
    name: str = "drag"
    risk_tier: str = "monitor"
    description: str = (
        "Drags (press-move-release) from one pixel coordinate to another "
        "— for map/globe rotation, panning, or sliders. "
        "Coordinates are absolute screen pixels."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "x1": {"type": "integer", "description": "Start X (pixels)"},
            "y1": {"type": "integer", "description": "Start Y (pixels)"},
            "x2": {"type": "integer", "description": "End X (pixels)"},
            "y2": {"type": "integer", "description": "End Y (pixels)"},
            "duration_ms": {
                "type": "integer",
                "default": _DEFAULT_DRAG_DURATION_MS,
                "description": "Duration of the drag motion in milliseconds",
            },
        },
        "required": ["x1", "y1", "x2", "y2"],
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        try:
            x1 = int(args["x1"])
            y1 = int(args["y1"])
            x2 = int(args["x2"])
            y2 = int(args["y2"])
        except (KeyError, TypeError, ValueError):
            return ToolResult(
                success=False, output=None,
                error="drag requires integer x1, y1, x2, y2 pixel coordinates",
            )
        try:
            duration_s = max(
                0.0, float(args.get("duration_ms", _DEFAULT_DRAG_DURATION_MS)) / 1000.0
            )
        except (TypeError, ValueError):
            return ToolResult(
                success=False, output=None,
                error="drag 'duration_ms' must be a number of milliseconds",
            )
        from jarvis.cu.actuate.base import ActuationUnavailable  # noqa: PLC0415

        try:
            await asyncio.to_thread(_perform_drag, x1, y1, x2, y2, duration_s)
        except ActuationUnavailable as exc:
            return ToolResult(success=False, output=None, error=str(exc))
        except ImportError as exc:
            return ToolResult(
                success=False, output=None,
                error=f"drag needs pyautogui on a desktop host: {exc}",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                success=False, output=None,
                error=f"drag ({x1},{y1})->({x2},{y2}) failed: {exc}",
            )
        return ToolResult(success=True, output=f"dragged ({x1},{y1})->({x2},{y2})")
