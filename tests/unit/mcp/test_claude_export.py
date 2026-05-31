"""Convert Jarvis mcp.json entries -> claude-cli mcpServers shape.

So a self-added MCP server (the "MCPs" sidebar / mcp.json) reaches the delegated
worker exactly like a Marketplace plugin does. Mirrors get_enabled_names'
rule: a missing `enabled` flag means DISABLED.
"""
from __future__ import annotations

from jarvis.mcp.claude_export import mcp_json_to_claude_servers


def _resolver(key: str):
    return {"MY_SECRET": "sk-XYZ"}.get(key)


def test_enabled_stdio_server_converts_and_resolves() -> None:
    servers = {
        "fs": {
            "command": "npx",
            "args": ["-y", "srv", "--root", "{PROJECT_ROOT}"],
            "env": {"TOKEN": "$MY_SECRET"},
            "enabled": True,
            "description": "files",
            "transport": "stdio",
            "required_auth": ["MY_SECRET"],
        }
    }
    out = mcp_json_to_claude_servers(
        servers, secret_resolver=_resolver, project_root="C:/repo"
    )
    assert out["fs"]["command"] == "npx"
    assert "C:/repo" in out["fs"]["args"]          # {PROJECT_ROOT} resolved
    assert out["fs"]["env"] == {"TOKEN": "sk-XYZ"}  # $MY_SECRET resolved
    # Jarvis-only keys must NOT leak into the claude config
    for junk in ("enabled", "description", "transport", "required_auth"):
        assert junk not in out["fs"]


def test_disabled_server_excluded() -> None:
    assert mcp_json_to_claude_servers({"x": {"command": "a", "enabled": False}}) == {}


def test_missing_enabled_is_excluded() -> None:
    # matches state.get_enabled_names: default is DISABLED
    assert mcp_json_to_claude_servers({"x": {"command": "a"}}) == {}


def test_http_server_converts() -> None:
    servers = {
        "remote": {
            "url": "https://mcp.example.com/mcp",
            "transport": "http",
            "enabled": True,
            "headers": {"Authorization": "Bearer t"},
        }
    }
    out = mcp_json_to_claude_servers(servers)
    assert out["remote"]["type"] == "http"
    assert out["remote"]["url"] == "https://mcp.example.com/mcp"
    assert out["remote"]["headers"]["Authorization"] == "Bearer t"


def test_unresolvable_secret_drops_env_key() -> None:
    out = mcp_json_to_claude_servers(
        {"s": {"command": "a", "env": {"K": "$MISSING"}, "enabled": True}},
        secret_resolver=lambda k: None,
    )
    assert out["s"].get("env", {}) == {}  # never pass a literal "$MISSING"


def test_no_command_no_url_skipped() -> None:
    assert mcp_json_to_claude_servers(
        {"weird": {"enabled": True, "description": "nothing runnable"}}
    ) == {}
