"""wait_for_ui_state tool: polls until a desired UI state is reached.

Examples:
  - ``wait_for_ui_state(title_contains='Notepad', timeout_s=5)`` waits until
    a window with "Notepad" in its title is visible.
  - ``wait_for_ui_state(text_contains='OK')`` waits until an element with
    text "OK" appears anywhere in the UIA tree (e.g. a dialog button).

Polling interval: 250ms. Default timeout: 5s. Hard maximum: 60s.

Risk tier: ``safe`` — pure observation, no state change.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

_POLL_INTERVAL_S = 0.25
_DEFAULT_TIMEOUT_S = 5.0
_MAX_TIMEOUT_S = 60.0


class WaitForUIStateTool:
    name: str = "wait_for_ui_state"
    risk_tier: str = "safe"
    description: str = (
        "Waits until a UI match occurs — either a window-title substring "
        "(title_contains) or text appearing anywhere in the UIA tree "
        "(text_contains). Returns the found title on success."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title_contains": {
                "type": "string",
                "description": "Substring that must appear in the window title",
            },
            "text_contains": {
                "type": "string",
                "description": "Substring that must appear anywhere in the UIA tree",
            },
            "timeout_s": {
                "type": "number",
                "default": _DEFAULT_TIMEOUT_S,
                "description": f"Maximum wait time (max {_MAX_TIMEOUT_S}s)",
            },
        },
        "required": [],
    }

    def __init__(self, vision_source: Any | None = None) -> None:
        self._vision_source = vision_source

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        title_needle = (args.get("title_contains") or "").strip().lower()
        text_needle = (args.get("text_contains") or "").strip().lower()
        if not title_needle and not text_needle:
            return ToolResult(
                success=False, output=None,
                error="Provide at least one of 'title_contains' or 'text_contains'",
            )

        timeout = min(float(args.get("timeout_s", _DEFAULT_TIMEOUT_S)), _MAX_TIMEOUT_S)
        timeout = max(timeout, 0.1)

        try:
            from jarvis.vision.tree_factory import make_ui_tree_source
        except ImportError as exc:
            return ToolResult(
                success=False, output=None,
                error=f"UI-tree source unavailable: {exc}",
            )

        source = self._vision_source or make_ui_tree_source()
        deadline = time.monotonic() + timeout
        last_observed_title = ""

        while True:
            try:
                obs = await source.observe()
                last_observed_title = obs.window_title
            except Exception as exc:  # noqa: BLE001
                # UIA can fail transiently (e.g. during window animations).
                # We log silently and keep retrying on the next tick.
                obs = None
                if time.monotonic() >= deadline:
                    return ToolResult(
                        success=False, output=None,
                        error=f"UIA polling failed consistently: {exc}",
                    )

            if obs is not None:
                title_lower = (obs.window_title or "").lower()
                title_match = (not title_needle) or (title_needle in title_lower)
                text_match = True
                if text_needle:
                    text_match = any(
                        text_needle in (n.text or "").lower() for n in obs.nodes
                    )
                if title_match and text_match:
                    return ToolResult(
                        success=True,
                        output={
                            "window_title": obs.window_title,
                            "matched_title": bool(title_needle),
                            "matched_text": bool(text_needle),
                            "elapsed_s": round(timeout - (deadline - time.monotonic()), 2),
                        },
                    )

            if time.monotonic() >= deadline:
                return ToolResult(
                    success=False, output=None,
                    error=(
                        f"Timed out after {timeout}s. "
                        f"Last window title: {last_observed_title!r}. "
                        f"Searched for: title~{title_needle!r}, text~{text_needle!r}"
                    ),
                )
            await asyncio.sleep(_POLL_INTERVAL_S)
