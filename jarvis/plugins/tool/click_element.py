"""click_element tool: click a UIA/AX element by its NAME (and optional role).

Instead of guessing pixel coordinates, this tool observes the live accessibility
tree, finds the matching element, and activates it semantically on macOS when
possible. Other hosts, unsupported AX actions, right-clicks, and double-clicks
use the verified pointer backend. This removes the most common computer-use
failure mode: the planner mentally computing click coordinates from a bounding
box.

Matching rules:
  - ``automation_id`` (if given) is an exact match and takes precedence.
  - ``name`` is matched case-insensitively as a substring of UIANode.name.
  - ``role`` (if given) is matched case-insensitively (exact role string).
Disabled elements and zero-area elements are skipped.

Risk-Tier: ``monitor`` — a click is often not reversible (buttons,
submits, file operations). Toast notification is shown, no approval gate.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.plugins.tool.click import (
    _click_windows,
    _window_signature_matches,
)
from jarvis.plugins.tool.click import (
    _foreground_window_signature as _click_foreground_window_signature,
)

# Re-export ``_click_windows`` as a module global so tests can patch it via
# ``monkeypatch.setattr("jarvis.plugins.tool.click_element._click_windows", ...)``.
__all__ = ["ClickElementTool", "_click_windows"]

_VALID_BUTTONS = ("left", "right", "middle")
_MAX_AVAILABLE_NAMES = 15


def _foreground_window_signature() -> tuple[Any, ...]:
    """Re-exported seam for tests; implementation is shared with raw click."""
    return _click_foreground_window_signature()


class ClickElementTool:
    name: str = "click_element"
    risk_tier: str = "monitor"
    description: str = (
        "Clicks a UI element identified by its NAME (case-insensitive "
        "substring) and optional role (e.g. Button, Edit, ListItem) or "
        "automation_id. Observes the live accessibility tree and activates "
        "the matched element without model-guessed pixel coordinates. On "
        "macOS, a normal left click uses native AXPress when the control "
        "supports it; otherwise the verified pointer backend is used. Prefer "
        "this over the raw 'click' tool whenever the target has a visible label."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Element label, matched case-insensitively as a substring "
                    "of the accessibility Name property"
                ),
            },
            "role": {
                "type": "string",
                "description": (
                    "Optional control type, matched case-insensitively "
                    "(e.g. Button, Edit, ListItem)"
                ),
            },
            "automation_id": {
                "type": "string",
                "description": (
                    "Optional exact AutomationId/AXIdentifier match "
                    "(takes precedence over name)"
                ),
            },
            "button": {
                "type": "string",
                "enum": list(_VALID_BUTTONS),
                "default": "left",
            },
            "double": {
                "type": "boolean",
                "default": False,
                "description": "Double-click instead of single click",
            },
            "nth": {
                "type": "integer",
                "default": 0,
                "description": "When several elements match, pick the nth (0-based)",
            },
        },
        "required": ["name"],
    }

    def __init__(self, vision_source: Any | None = None) -> None:
        self._vision_source = vision_source

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        name_needle = (args.get("name") or "").strip()
        role_needle = (args.get("role") or "").strip()
        automation_id = (args.get("automation_id") or "").strip()
        button = str(args.get("button", "left")).lower()
        double = bool(args.get("double", False))
        try:
            nth = int(args.get("nth", 0))
        except (TypeError, ValueError):
            nth = 0
        nth = max(nth, 0)

        if not name_needle and not automation_id:
            return ToolResult(
                success=False,
                output=None,
                error="Provide at least one of 'name' or 'automation_id'",
            )
        if button not in _VALID_BUTTONS:
            return ToolResult(
                success=False,
                output=None,
                error=f"Unknown button={button!r}. Allowed: left/right/middle",
            )

        # 1. Observe the live UI-element tree (per-OS source via the factory).
        try:
            from jarvis.vision.tree_factory import make_ui_tree_source
        except ImportError as exc:
            return ToolResult(
                success=False,
                output=None,
                error=f"UI-tree source unavailable: {exc}",
            )

        source = self._vision_source or make_ui_tree_source()
        observed_signature = _foreground_window_signature()
        if observed_signature[0] == "none":
            return ToolResult(
                success=False,
                output=None,
                error=(
                    "Refusing click_element: the foreground window identity "
                    "is unavailable."
                ),
            )
        expected_raw = args.get("_expected_window_signature")
        if expected_raw is not None:
            if not isinstance(expected_raw, (list, tuple)):
                return ToolResult(
                    success=False,
                    output=None,
                    error="Refusing click_element: invalid captured-window identity.",
                )
            expected_signature = tuple(expected_raw)
            if observed_signature != expected_signature:
                return ToolResult(
                    success=False,
                    output=None,
                    error=(
                        "Refusing click_element: the foreground window changed "
                        "after the screenshot."
                    ),
                )
        else:
            expected_signature = observed_signature
        try:
            obs = await source.observe()
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                success=False,
                output=None,
                error=f"UIA observation failed: {exc}",
            )

        name_lower = name_needle.lower()
        role_lower = role_needle.lower()

        # 2. Build the candidate list.
        candidates = []
        for node in obs.nodes:
            if not node.enabled:
                continue
            _, _, w, h = node.bounds
            if w <= 0 or h <= 0:
                continue
            if automation_id:
                if node.automation_id != automation_id:
                    continue
            elif name_lower:
                if name_lower not in (node.name or "").lower():
                    continue
            if role_lower and (node.role or "").lower() != role_lower:
                continue
            candidates.append(node)

        # 3. No candidates -> list visible enabled labels to help the planner.
        if not candidates:
            available = [
                (n.name or "").strip()
                for n in obs.nodes
                if n.enabled and n.bounds[2] > 0 and n.bounds[3] > 0 and (n.name or "").strip()
            ][:_MAX_AVAILABLE_NAMES]
            wanted = automation_id and f"automation_id={automation_id!r}" or f"name~{name_needle!r}"
            if role_needle:
                wanted += f", role={role_needle!r}"
            return ToolResult(
                success=False,
                output=None,
                error=(
                    f"No matching element ({wanted}). "
                    f"Available labels: {available}"
                ),
            )

        # 4. Pick the nth match and compute its center.
        matched = candidates[min(nth, len(candidates) - 1)]
        x, y, w, h = matched.bounds
        cx = x + w // 2
        cy = y + h // 2

        # Observation is asynchronous (AX/UIA can block). Bind the selected
        # node to the window current before observation and, for CU, to the
        # exact window captured by the engine. A same-labelled control in a
        # newly focused app must never receive the stale click.
        if _foreground_window_signature() != expected_signature:
            return ToolResult(
                success=False,
                output=None,
                error=(
                    "Refusing click_element: the foreground window changed "
                    "while its UI tree was being observed."
                ),
            )

        # 5. Accessibility-first on macOS. AXPress acts on the semantic control,
        # not on pixels. Only an explicitly unsupported action falls back to
        # pointer input; identity/permission failures fail closed.
        if sys.platform == "darwin" and button == "left" and not double:
            from jarvis.cu.macos_semantic import try_press_at

            semantic = await asyncio.to_thread(
                try_press_at,
                cx,
                cy,
                expected_name=matched.name or name_needle,
                expected_role=matched.role or role_needle,
                expected_automation_id=matched.automation_id or automation_id,
                pre_action_check=lambda: _window_signature_matches(
                    expected_signature,
                ),
            )
            if semantic.performed:
                return ToolResult(
                    success=True,
                    output=(
                        f"Activated {matched.role or 'element'} '{matched.name}' "
                        "with native macOS AXPress"
                    ),
                )
            if semantic.status != "unsupported":
                return ToolResult(success=False, output=None, error=semantic.detail)

        # 6. Verified pointer fallback — native on Windows, capability-gated
        # elsewhere. On macOS this is used only when AXPress is unsupported or
        # the requested gesture is not a normal left click.
        if os.name == "nt":
            try:
                await asyncio.to_thread(
                    _click_windows,
                    cx,
                    cy,
                    button,
                    double,
                    expected_window_signature=expected_signature,
                )
            except (ValueError, OSError) as exc:
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"Click on '{matched.name}' at ({cx},{cy}) failed: {exc}",
                )
        else:
            from jarvis.cu.actuate.base import (
                ActuationUnavailable,
                get_actuator,
                verified_click,
            )

            try:
                actuator = get_actuator()
            except ActuationUnavailable as exc:
                return ToolResult(success=False, output=None, error=str(exc))
            try:
                landing = await asyncio.to_thread(
                    verified_click,
                    actuator,
                    cx,
                    cy,
                    button=button,
                    double=double,
                    pre_action_check=lambda: _window_signature_matches(
                        expected_signature,
                    ),
                )
                if not landing.ok:
                    return ToolResult(
                        success=False, output=None, error=landing.detail,
                    )
            except Exception as exc:  # noqa: BLE001
                return ToolResult(success=False, output=None, error=str(exc))

        # 7. Success.
        return ToolResult(
            success=True,
            output=f"Clicked {role_needle or 'element'} '{matched.name}' at ({cx},{cy})",
        )
