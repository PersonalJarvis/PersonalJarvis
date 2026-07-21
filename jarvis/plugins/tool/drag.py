"""drag-Tool: press-and-hold drag from one absolute pixel to another.

The press-move-release gesture a plain click cannot do — rotating a map/globe,
panning a canvas, or moving a slider. Coordinates are ABSOLUTE screen pixels (the
Computer-Use loop resolves its 0-1000 normalized grid to pixels before calling
this tool, exactly as it does for ``click``).

Risk-Tier: ``monitor`` — a drag can move/resize/reorder things and is not always
cleanly reversible, but it raises no irreversible system change. Routing it
through this tool (instead of an inline call) gives the drag the same risk-tier /
blacklist / audit path as every other Computer-Use action (audit #13).

The shared verified-actuation layer selects native ``SendInput`` on Windows,
native Quartz mouse events on macOS, or a supported X11 desktop backend. It
remains import-clean on headless hosts and fails with an actionable message
when no safe input backend is available.
"""
from __future__ import annotations

import asyncio
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

#: Default drag duration (ms) — slow enough that a map/globe/slider registers the
#: press-move-release as a real gesture rather than an instantaneous teleport.
_DEFAULT_DRAG_DURATION_MS = 400


def _perform_drag(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration_s: float,
    *,
    expected_window_signature: tuple[Any, ...] | None = None,
) -> None:
    """Press left at ``(x1, y1)``, drag to ``(x2, y2)``, release. Blocking;
    callers run it via ``asyncio.to_thread``.

    The platform backend is resolved through the capability probe and the
    start/end pointer positions are verified around the gesture.
    """
    from jarvis.cu.actuate.base import get_actuator, verified_drag  # noqa: PLC0415
    from jarvis.plugins.tool.click import _window_signature_matches  # noqa: PLC0415

    result = verified_drag(
        get_actuator(),
        x1,
        y1,
        x2,
        y2,
        duration_s=max(0.0, duration_s),
        pre_action_check=(
            (lambda: _window_signature_matches(expected_window_signature))
            if expected_window_signature is not None
            else None
        ),
    )
    if not result.ok:
        raise RuntimeError(result.detail)


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

        expected_raw = args.get("_expected_window_signature")
        if expected_raw is not None and not isinstance(expected_raw, (list, tuple)):
            return ToolResult(
                success=False, output=None,
                error="Refusing drag: invalid captured-window identity.",
            )
        try:
            if expected_raw is None:
                await asyncio.to_thread(_perform_drag, x1, y1, x2, y2, duration_s)
            else:
                from jarvis.cu.target_guard import (  # noqa: PLC0415
                    coerce_signature,
                )

                expected_signature = coerce_signature(expected_raw)
                from jarvis.plugins.tool.click import (  # noqa: PLC0415
                    _window_signature_matches,
                )

                if not _window_signature_matches(expected_signature):
                    return ToolResult(
                        success=False,
                        output=None,
                        error=(
                            "Refusing drag: foreground window identity is "
                            "unavailable or changed after the screenshot."
                        ),
                    )
                await asyncio.to_thread(
                    _perform_drag,
                    x1,
                    y1,
                    x2,
                    y2,
                    duration_s,
                    expected_window_signature=expected_signature,
                )
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
