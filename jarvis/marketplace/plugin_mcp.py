"""Map a connected Marketplace plugin into the in-process MCPServerSpec.

This is the live-brain analogue of marketplace.mcp_bridge (which targets the
claude-cli worker). Here we build a jarvis.mcp.MCPServerSpec + env_overrides
so an in-process MCPClient can connect and expose the plugin's tools to the
router-brain directly. Token placeholders reuse mcp_bridge's resolver so the
two paths stay byte-identical on placeholder semantics.
"""
from __future__ import annotations

from jarvis.marketplace.catalog import PluginSpec
from jarvis.marketplace.mcp_bridge import _resolve_placeholders, _token_replacements
from jarvis.marketplace.token_store import Tokens
from jarvis.mcp.registry import MCPServerSpec


def plugin_to_mcp_server_spec(
    plugin: PluginSpec, tokens: Tokens
) -> tuple[MCPServerSpec, dict[str, str]] | None:
    """Return (MCPServerSpec, env_overrides) for a connected plugin, or None.

    None when the plugin has no mcp_server block or an MCP-incompatible
    transport (rest_wrapper / unknown). stdio + http are supported — the same
    two transports the worker bridge speaks.
    """
    spec = plugin.mcp_server
    if not spec:
        return None
    repl = _token_replacements(plugin.id, tokens.access)
    transport = str(spec.get("transport") or "").lower()

    if transport == "http":
        url = spec.get("url")
        if not url:
            return None
        headers: dict[str, str] = {}
        header_template = spec.get("auth_header_template")
        if header_template:
            resolved = _resolve_placeholders(str(header_template), repl)
            key, sep, val = resolved.partition(":")
            if sep:
                headers[key.strip()] = val.strip()
        server_spec = MCPServerSpec(
            name=plugin.id,
            display=plugin.display_name,
            description=plugin.description,
            install_command=[],
            transport="http",
            url=str(url),
            headers=headers,
        )
        return server_spec, {}

    if transport == "stdio":
        install = spec.get("install") or []
        if not install:
            return None
        resolved_install = [_resolve_placeholders(str(a), repl) for a in install]
        env_template = spec.get("env_template") or {}
        env_overrides = {
            str(k): _resolve_placeholders(str(v), repl) for k, v in env_template.items()
        }
        server_spec = MCPServerSpec(
            name=plugin.id,
            display=plugin.display_name,
            description=plugin.description,
            install_command=resolved_install,
            transport="stdio",
        )
        return server_spec, env_overrides

    return None
