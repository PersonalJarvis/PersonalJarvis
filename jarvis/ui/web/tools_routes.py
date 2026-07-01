"""REST API for tool-registry inspection.

Shows all tools available to the brain at runtime — MCP adapters, native
plugin tools, skills. A pure read-only API for the UI and debugging.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/tools", tags=["tools"])


def _tool_to_dict(name: str, tool: Any) -> dict[str, Any]:
    source = "native"
    if "/" in name:
        # MCPToolAdapter.name = "<server>/<tool>"
        source = "mcp"

    return {
        "name": name,
        "description": getattr(tool, "description", "") or "",
        "risk_tier": getattr(tool, "risk_tier", "monitor"),
        "schema": getattr(tool, "schema", {}) or {},
        "source": source,
        # MCP adapters have an MCPClient — we show the server name for the UI
        "mcp_server": (
            getattr(getattr(tool, "_client", None), "spec", None).name
            if hasattr(tool, "_client")
            else None
        ),
    }


@router.get("")
async def list_tools(request: Request) -> dict[str, Any]:
    registry = getattr(request.app.state, "tool_registry", None)
    if registry is None or not hasattr(registry, "items"):
        return {"tools": [], "total": 0, "by_source": {"mcp": 0, "native": 0}}

    tools = [_tool_to_dict(name, tool) for name, tool in registry.items()]
    by_source: dict[str, int] = {"mcp": 0, "native": 0}
    for t in tools:
        by_source[t["source"]] = by_source.get(t["source"], 0) + 1

    return {
        "tools": sorted(tools, key=lambda t: (t["source"], t["name"])),
        "total": len(tools),
        "by_source": by_source,
    }
