"""Bridge connected marketplace plugins into a claude-cli MCP config.

This is the missing wire between the Marketplace (where a user *connects* a
plugin by saving a token) and the delegated Heavy-Duty Worker (which runs the
`claude` CLI and can call MCP tools). The talker stays thin: it only delegates;
the worker is the one that actually issues the plugin/MCP tool calls (AD-OE4).

`assemble_claude_mcp_servers` reads every catalog plugin, keeps the ones that
have a saved token (= connected), and converts each plugin's `mcp_server`
spec + token into a claude-cli `mcpServers` entry:

* ``transport: "stdio"``  -> ``{"command", "args", "env"}`` (token resolved
  into the env_template / argv placeholders).
* ``transport: "http"``   -> ``{"type": "http", "url", "headers"}`` (token
  resolved into the Authorization bearer header).
* ``transport: "rest_wrapper"`` (or anything else) -> skipped: it is not a
  real MCP server claude-cli can speak to.

User ``mcp.json`` servers can be merged in via ``extra_servers``.
"""
from __future__ import annotations

import logging
from typing import Any

from jarvis.marketplace.catalog import PluginCatalog
from jarvis.marketplace.token_store import TokenStore

log = logging.getLogger(__name__)


def _resolve_placeholders(value: str, replacements: dict[str, str]) -> str:
    """Replace ``$plugin_<id>_access_token`` / ``${...}`` placeholders."""
    out = value
    for placeholder, real in replacements.items():
        out = out.replace(placeholder, real)
    return out


def _token_replacements(plugin_id: str, access_token: str) -> dict[str, str]:
    base = f"plugin_{plugin_id}_access_token"
    # Catalog uses both bare ($x) and braced (${x}) placeholder forms.
    return {f"${{{base}}}": access_token, f"${base}": access_token}


def _stdio_entry(spec: dict[str, Any], repl: dict[str, str]) -> dict[str, Any] | None:
    install = spec.get("install") or []
    if not install:
        return None
    command = _resolve_placeholders(str(install[0]), repl)
    args = [_resolve_placeholders(str(a), repl) for a in install[1:]]
    entry: dict[str, Any] = {"command": command, "args": args}
    env_template = spec.get("env_template") or {}
    env = {
        str(k): _resolve_placeholders(str(v), repl)
        for k, v in env_template.items()
    }
    if env:
        entry["env"] = env
    return entry


def _http_entry(spec: dict[str, Any], repl: dict[str, str]) -> dict[str, Any] | None:
    url = spec.get("url")
    if not url:
        return None
    entry: dict[str, Any] = {"type": "http", "url": str(url)}
    header_template = spec.get("auth_header_template")
    if header_template:
        resolved = _resolve_placeholders(str(header_template), repl)
        key, sep, val = resolved.partition(":")
        if sep:
            entry["headers"] = {key.strip(): val.strip()}
    return entry


def assemble_claude_mcp_servers(
    catalog: PluginCatalog,
    token_store: TokenStore,
    *,
    extra_servers: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """Build a claude-cli ``mcpServers`` map from connected plugins + extras.

    A plugin counts as *connected* when ``token_store.load(plugin.id)`` returns
    a token. Plugins without an MCP-capable transport (e.g. Vercel's
    ``rest_wrapper``) are skipped. Plugin entries take precedence over
    ``extra_servers`` on a name clash.
    """
    servers: dict[str, dict] = dict(extra_servers or {})

    for plugin in catalog.plugins:
        spec = plugin.mcp_server
        if not spec:
            continue
        try:
            tokens = token_store.load(plugin.id)
        except Exception as exc:  # noqa: BLE001 — a corrupt token must not nuke the rest
            log.warning("mcp_bridge: token load failed for %s: %s", plugin.id, exc)
            continue
        if tokens is None:
            continue  # not connected

        repl = _token_replacements(plugin.id, tokens.access)
        transport = str(spec.get("transport") or "").lower()
        if transport == "stdio":
            entry = _stdio_entry(spec, repl)
        elif transport == "http":
            entry = _http_entry(spec, repl)
        else:
            # rest_wrapper / sse / unknown -> not a claude-cli MCP server
            entry = None

        if entry is not None:
            servers[plugin.id] = entry

    return servers
