"""wait_for_element tool: poll the UIA tree until a clickable element appears.

Unlike ``wait_for_ui_state`` (which only checks raw text/title substrings and
cannot confirm that an actionable element is present and enabled), this tool
locates a concrete UIA node by role / name / automation_id and returns its
center coordinates so the next action (e.g. a click) can target it directly.

Examples:
  - ``wait_for_element(role='Button', name_contains='OK')`` waits until an
    enabled-or-not OK button exists and returns its center ``x``/``y``.
  - ``wait_for_element(automation_id='submitBtn', enabled_required=True)``
    waits until the submit button is present AND enabled.

Polling interval: 250ms. Default timeout: 5s. Hard maximum: 60s.

Risk-Tier: ``safe`` — pure observation, no state change.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult


_POLL_INTERVAL_S = 0.25
_DEFAULT_TIMEOUT_S = 5.0
_MAX_TIMEOUT_S = 60.0


class WaitForElementTool:
    name: str = "wait_for_element"
    risk_tier: str = "safe"
    description: str = (
        "Polls the UIA tree until an element matching role / name_contains / "
        "automation_id (optionally enabled) appears, then returns its center "
        "x/y coordinates so the next action can click it directly. Prefer this "
        "over wait_for_ui_state when you need a clickable target, not just a "
        "text substring."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name_contains": {
                "type": "string",
                "description": "Case-insensitive substring of the element's UIA name",
            },
            "role": {
                "type": "string",
                "description": "Case-insensitive UIA role match (e.g. 'Button', 'Edit')",
            },
            "automation_id": {
                "type": "string",
                "description": "Exact UIA AutomationId match",
            },
            "enabled_required": {
                "type": "boolean",
                "default": False,
                "description": "If true, only match elements that are enabled",
            },
            "timeout_s": {
                "type": "number",
                "default": _DEFAULT_TIMEOUT_S,
                "description": f"Maximum wait time in seconds (max {_MAX_TIMEOUT_S}s)",
            },
        },
        "required": [],
    }

    def __init__(self, vision_source: Any | None = None) -> None:
        self._vision_source = vision_source

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        name_needle = (args.get("name_contains") or "").strip().lower()
        role_needle = (args.get("role") or "").strip().lower()
        automation_id_needle = (args.get("automation_id") or "").strip()
        if not name_needle and not role_needle and not automation_id_needle:
            return ToolResult(
                success=False, output=None,
                error="Provide at least one of 'name_contains', 'role' or 'automation_id'",
            )

        enabled_required = bool(args.get("enabled_required", False))

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
                # UIA can transiently fail (e.g. during window animations).
                # Log silently and retry on the next tick until the deadline.
                obs = None
                if time.monotonic() >= deadline:
                    return ToolResult(
                        success=False, output=None,
                        error=f"UIA polling failed continuously: {exc}",
                    )

            if obs is not None:
                for node in obs.nodes:
                    if enabled_required and not node.enabled:
                        continue
                    if name_needle and name_needle not in (node.name or "").lower():
                        continue
                    if role_needle and role_needle != (node.role or "").lower():
                        continue
                    if automation_id_needle and automation_id_needle != (node.automation_id or ""):
                        continue
                    x, y, w, h = node.bounds
                    if w <= 0 or h <= 0:
                        continue
                    cx = x + w // 2
                    cy = y + h // 2
                    return ToolResult(
                        success=True,
                        output={
                            "found": True,
                            "name": node.name,
                            "role": node.role,
                            "automation_id": node.automation_id,
                            "x": cx,
                            "y": cy,
                            "bounds": list(node.bounds),
                            "window_title": obs.window_title,
                            "elapsed_s": round(
                                timeout - (deadline - time.monotonic()), 2
                            ),
                        },
                    )

            if time.monotonic() >= deadline:
                return ToolResult(
                    success=False, output=None,
                    error=(
                        f"Timeout after {timeout}s. "
                        f"Last window: {last_observed_title!r}. "
                        f"Searched: name~{name_needle!r}, role~{role_needle!r}, "
                        f"automation_id=={automation_id_needle!r}, "
                        f"enabled_required={enabled_required}"
                    ),
                )
            await asyncio.sleep(_POLL_INTERVAL_S)
