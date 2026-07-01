"""read_visible_ui_state tool: returns the current UI state as feedback.

Gives the agent structured information about what is currently visible
on screen — window title, visible texts, node count.
Optionally also a screenshot artifact for vision-capable brains.

Risk tier: ``safe`` — read-only, no state change.

Architecture: the tool lazily instantiates a ``UIATreeSource`` on the
first call. Alternatively the caller can inject an existing source via
factory (see the ``brain.factory`` wiring with VisionEngine).
"""
from __future__ import annotations

from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult


class ReadVisibleUIStateTool:
    name: str = "read_visible_ui_state"
    risk_tier: str = "safe"
    description: str = (
        "Reads the current UI state: window title, visible text, and "
        "UI element count. Optionally also a screenshot."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "include_screenshot": {
                "type": "boolean",
                "default": False,
                "description": "If True, also return a screenshot as an image artifact",
            },
            "max_text_chars": {
                "type": "integer",
                "default": 2000,
                "description": "Limit for the aggregated text — prevents a token explosion",
            },
        },
        "required": [],
    }

    def __init__(self, vision_source: Any | None = None) -> None:
        # Optionally injectable for tests + central engine reuse.
        self._vision_source = vision_source

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        include_screenshot = bool(args.get("include_screenshot", False))
        max_text_chars = int(args.get("max_text_chars", 2000))

        try:
            from jarvis.vision.tree_factory import make_ui_tree_source
        except ImportError as exc:
            return ToolResult(
                success=False, output=None,
                error=f"UI-tree source unavailable: {exc}",
            )

        source = self._vision_source or make_ui_tree_source()
        try:
            obs = await source.observe()
        except Exception as exc:  # noqa: BLE001 — UIA can raise a variety of errors
            return ToolResult(
                success=False, output=None,
                error=f"UI observation failed: {exc}",
            )

        # Aggregate texts from the nodes
        texts: list[str] = []
        char_count = 0
        for node in obs.nodes:
            t = (node.text or "").strip()
            if not t:
                continue
            if char_count + len(t) > max_text_chars:
                texts.append("…")
                break
            texts.append(t)
            char_count += len(t)

        state = {
            "window_title": obs.window_title,
            "active_pid": obs.active_pid,
            "node_count": len(obs.nodes),
            "visible_texts": texts,
        }

        artifacts: tuple[dict[str, Any], ...] = ()
        if include_screenshot and obs.screenshot_path:
            try:
                import base64
                from pathlib import Path
                data = Path(obs.screenshot_path).read_bytes()
                artifacts = (
                    {
                        "type": "image",
                        "mime": "image/png",
                        "data": base64.b64encode(data).decode("ascii"),
                    },
                )
            except OSError:
                # Screenshot file not readable — not a hard fail
                pass

        return ToolResult(
            success=True,
            output=state,
            artifacts=artifacts,
        )
