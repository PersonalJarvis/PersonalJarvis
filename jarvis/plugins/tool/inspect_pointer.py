"""``inspect-pointer`` — resolve the on-screen element under the mouse cursor.

Router-tier, risk ``safe`` (read-only). This is the AI-Pointer *pull* path: the
brain calls it when the user asks a deictic/spatial question about what they are
pointing at ("what is this?", "was ist das da?"). It resolves the element under
the cursor via the OS accessibility tree (``jarvis.pointer.context``) and returns
its name/role/value/app — never a blind full-screen screenshot.

A direct safe-gated read, never a spawn — it never enters a worker tool-set
(AP-5/AP-14). The deictic voice path additionally *pushes* this context into the
turn prompt (see ``jarvis.brain.manager``); this tool is the chat / explicit
affordance.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from jarvis.core.protocols import ToolResult
from jarvis.pointer.context import PointerContext, resolve_pointer_context_async

ResolveFn = Callable[[], Awaitable[PointerContext]]


class InspectPointerTool:
    """Read the accessibility element currently under the mouse cursor."""

    name: str = "inspect-pointer"
    risk_tier: str = "safe"
    description: str = (
        "Resolve the on-screen UI element the user's MOUSE CURSOR is pointing at, "
        "via the OS accessibility tree (not a screenshot). Use this ONLY when the "
        "user asks a deictic/spatial question about what they are pointing at — "
        "'what is this?', 'was ist das da?', 'worauf zeige ich?'. Returns the "
        "element's name, role, value, and owning app. Do NOT use it for questions "
        "unrelated to the cursor (e.g. the weather)."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def __init__(self, resolve_fn: ResolveFn | None = None) -> None:
        self._resolve_fn = resolve_fn

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        resolve = self._resolve_fn or resolve_pointer_context_async
        pc = await resolve()
        if not pc.available:
            return ToolResult(
                success=True,
                output={
                    "available": False,
                    "reason": pc.reason,
                    "summary": (
                        "No element is under the mouse cursor right now "
                        f"({pc.reason})."
                    ),
                },
            )
        el = pc.element
        return ToolResult(
            success=True,
            output={
                "available": True,
                "x": pc.x,
                "y": pc.y,
                "name": el.name if el else "",
                "role": el.role if el else "",
                "value": (el.value if el else "")[:500],
                "app": el.app_name if el else "",
                "window": el.window_title if el else "",
                "has_crop": pc.crop is not None,
                "summary": pc.render(),
            },
        )
