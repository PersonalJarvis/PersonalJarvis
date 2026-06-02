"""PluginToolLoader — the single static entry point for the plugin tool slot.

Registered once in pyproject.toml: plugin-tools = jarvis.marketplace.plugin_loader:PluginToolLoader.
The brain factory recognises is_virtual_loader=True and calls expand() -> list[Tool].
Mirror of jarvis/clis/loader.py: it returns the active tools of the shared
PluginToolRegistry, or [] when none is published yet (the BrainToolsChanged
live-reload re-runs expand() once bootstrap finishes / a plugin connects).
"""
from __future__ import annotations

import logging
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)


class PluginToolLoader:
    is_virtual_loader: bool = True

    name: str = "plugin_tools_loader"
    description: str = (
        "Virtual plugin-tool loader. Never called by the brain directly — "
        "the factory expands it into N MCPToolAdapter instances."
    )
    risk_tier: str = "block"
    schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    def expand(self) -> list[Any]:
        try:
            from jarvis.marketplace.plugin_shared import get_active_plugin_registry

            registry = get_active_plugin_registry()
        except Exception as exc:  # noqa: BLE001
            log.debug("plugin-loader: shared registry lookup failed: %s", exc)
            return []
        if registry is None:
            return []
        try:
            return list(registry.active_tools())
        except Exception as exc:  # noqa: BLE001
            log.debug("plugin-loader: active_tools() failed: %s", exc)
            return []

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        return ToolResult(
            success=False,
            output=None,
            error="PluginToolLoader is a virtual loader; expand it, don't execute it.",
        )


__all__ = ["PluginToolLoader"]
