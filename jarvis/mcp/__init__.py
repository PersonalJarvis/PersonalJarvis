"""MCP integration: client, registry, bootstrap, and tool adapter.

Exposes `MCPClient`, `MCPRegistry`, the `BOOTSTRAP_SERVERS` list, and
`MCPToolAdapter`. Bootstrap helpers live in `jarvis.mcp.bootstrap`.
"""
from __future__ import annotations

from .adapter import MCPToolAdapter, register_mcp_tools_in_registry
from .client import MCPClient
from .registry import BOOTSTRAP_SERVERS, MCPRegistry, MCPServerSpec

__all__ = [
    "BOOTSTRAP_SERVERS",
    "MCPClient",
    "MCPRegistry",
    "MCPServerSpec",
    "MCPToolAdapter",
    "register_mcp_tools_in_registry",
]
