"""MCP integration: client, registry, bootstrap, and tool adapter.

Exposes `MCPClient`, `MCPRegistry`, the `BOOTSTRAP_SERVERS` list, and
`MCPToolAdapter`. Bootstrap helpers live in `jarvis.mcp.bootstrap`.
"""
from __future__ import annotations

from .adapter import MCPToolAdapter, register_mcp_tools_in_registry
from .client import MCPClient
from .notification_filter import (
    NotificationValidationFilter,
    install_notification_log_filter,
)
from .registry import BOOTSTRAP_SERVERS, MCPRegistry, MCPServerSpec

__all__ = [
    "BOOTSTRAP_SERVERS",
    "MCPClient",
    "MCPRegistry",
    "MCPServerSpec",
    "MCPToolAdapter",
    "NotificationValidationFilter",
    "install_notification_log_filter",
    "register_mcp_tools_in_registry",
]
