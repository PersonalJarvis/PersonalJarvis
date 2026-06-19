"""McpToolLoader — virtual loader for connected MCP server tools.

Registered once in pyproject.toml:
  mcp-tools = jarvis.mcp.loader:McpToolLoader

The brain factory recognises ``is_virtual_loader=True`` and calls
``expand()`` → ``list[Tool]``.  The result is one ``MCPToolAdapter`` per
tool exposed by each currently *active* (running) MCP client.  If no MCP
registry is wired yet (e.g. headless test build), ``expand()`` silently
returns ``[]`` — the BrainToolsChanged live-reload re-runs ``expand()``
once the registry bootstraps or a server connects.

Mirror of ``jarvis/marketplace/plugin_loader.py`` and
``jarvis/clis/loader.py``: every failure path returns ``[]`` so a broken
MCP server can never crash the brain factory.  This loader is synchronous
— it reads the already-cached ``client._tools_cache`` list, never starts
or awaits any network I/O.
"""

from __future__ import annotations

import logging
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)


class McpToolLoader:
    """Virtual MCP-tool loader.

    The brain factory calls ``expand()`` to obtain one ``MCPToolAdapter``
    per tool of every *connected and running* MCP server.  The entry-point
    name ``mcp-tools`` appears in ``ROUTER_TOOLS`` so the loader is
    discovered and expanded by ``_load_tools_for_tier``.
    """

    is_virtual_loader: bool = True

    name: str = "mcp_tools_loader"
    description: str = (
        "Virtual MCP-tool loader. Never called by the brain directly — "
        "the factory expands it into one MCPToolAdapter per tool of each "
        "connected and running MCP server."
    )
    risk_tier: str = "block"
    schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    def expand(self) -> list[Any]:
        """Return one ``MCPToolAdapter`` per tool across all active MCP clients.

        Reads ``client._tools_cache`` synchronously — no network I/O,
        never ``await``.  Returns ``[]`` on any failure so the factory
        continues booting even when MCP support is absent or broken.
        """
        try:
            from jarvis.core import runtime_refs

            reg = runtime_refs.get_mcp_registry()
        except Exception as exc:  # noqa: BLE001
            log.debug("mcp-loader: runtime_refs lookup failed: %s", exc)
            return []

        if reg is None:
            return []

        try:
            active = reg.active_clients()
        except Exception as exc:  # noqa: BLE001
            log.debug("mcp-loader: active_clients() failed: %s", exc)
            return []

        adapters: list[Any] = []
        for client in active.values():
            tool_defs: list[dict[str, Any]] = getattr(client, "_tools_cache", [])
            for tool_def in tool_defs:
                try:
                    from jarvis.mcp.adapter import MCPToolAdapter

                    adapter = MCPToolAdapter(client, tool_def, risk_tier="monitor")
                    adapters.append(adapter)
                except Exception as exc:  # noqa: BLE001
                    log.debug(
                        "mcp-loader: failed to build adapter for %s/%s: %s",
                        getattr(getattr(client, "spec", None), "name", "?"),
                        tool_def.get("name", "?"),
                        exc,
                    )
        return adapters

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        return ToolResult(
            success=False,
            output=None,
            error="McpToolLoader is a virtual loader; expand it, don't execute it.",
        )


__all__ = ["McpToolLoader"]
