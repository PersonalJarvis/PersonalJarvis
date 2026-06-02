"""Tests for the marketplace -> claude-cli MCP bridge.

`assemble_claude_mcp_servers` turns *connected* marketplace plugins (a saved
token + the catalog `mcp_server` spec) plus any user `mcp.json` servers into a
claude-cli-compatible ``mcpServers`` map, so the delegated worker can actually
call the connected plugins. Uses the real `data/plugin_catalog.json` so the
test exercises the real transport variants (stdio, http, rest_wrapper).
"""
from __future__ import annotations

import json

from jarvis.marketplace.catalog_data import load_catalog
from jarvis.marketplace.mcp_bridge import assemble_claude_mcp_servers
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore


def _store(**connected: str) -> TokenStore:
    ts = TokenStore(InMemoryBackend())
    for plugin_id, token in connected.items():
        ts.save(plugin_id, Tokens(access=token))
    return ts


def test_stdio_plugin_resolves_env_token() -> None:
    # github = stdio MCP; token rides in env_template GITHUB_PERSONAL_ACCESS_TOKEN
    servers = assemble_claude_mcp_servers(load_catalog(), _store(github="ghp_SECRET"))
    gh = servers["github"]
    assert gh["command"] == "docker"
    assert gh["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_SECRET"
    # placeholder must be fully resolved, never leak verbatim
    assert "$plugin_github_access_token" not in json.dumps(gh)


def test_stdio_plugin_resolves_arg_placeholder() -> None:
    # supabase = stdio MCP; token rides as an ``--access-token`` argv value
    servers = assemble_claude_mcp_servers(load_catalog(), _store(supabase="sbp_SECRET"))
    sb = servers["supabase"]
    assert sb["command"] == "npx"
    assert "sbp_SECRET" in sb["args"]
    assert "$plugin_supabase_access_token" not in json.dumps(sb)


def test_http_plugin_builds_bearer_header() -> None:
    # notion = hosted http MCP; token rides in an Authorization bearer header
    servers = assemble_claude_mcp_servers(load_catalog(), _store(notion="ntn_SECRET"))
    no = servers["notion"]
    assert no["type"] == "http"
    assert no["url"] == "https://mcp.notion.com/mcp"
    assert no["headers"]["Authorization"] == "Bearer ntn_SECRET"
    assert "${plugin_notion_access_token}" not in json.dumps(no)


def test_rest_wrapper_plugin_is_skipped() -> None:
    # vercel uses transport=rest_wrapper -> NOT a real MCP server -> skip
    servers = assemble_claude_mcp_servers(load_catalog(), _store(vercel="vcp_SECRET"))
    assert "vercel" not in servers


def test_unconnected_plugin_is_skipped() -> None:
    # slack has an http mcp_server but no saved token -> not connected -> skip
    servers = assemble_claude_mcp_servers(load_catalog(), _store(github="ghp_X"))
    assert "slack" not in servers


def test_extra_mcp_json_servers_are_merged() -> None:
    extra = {"local-fs": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]}}
    servers = assemble_claude_mcp_servers(
        load_catalog(), _store(github="ghp_X"), extra_servers=extra
    )
    assert servers["local-fs"] == extra["local-fs"]
    assert "github" in servers  # plugin entries still present alongside extras


def test_no_connections_returns_only_extras() -> None:
    servers = assemble_claude_mcp_servers(load_catalog(), _store())
    assert servers == {}


def _seed_catalog():
    """The tracked package seed (carries the new coming-soon plugins even
    before the data/ override is synced)."""
    from jarvis.marketplace import catalog_data
    from jarvis.marketplace.catalog_data import clear_cache, load_catalog

    clear_cache()
    cat = load_catalog(catalog_data._PACKAGE_SEED_PATH)
    clear_cache()
    return cat


def test_connected_stripe_becomes_http_mcp_with_bearer() -> None:
    servers = assemble_claude_mcp_servers(_seed_catalog(), _store(stripe="sk_live_abc"))
    assert servers["stripe"]["type"] == "http"
    assert servers["stripe"]["url"] == "https://mcp.stripe.com"
    assert servers["stripe"]["headers"] == {"Authorization": "Bearer sk_live_abc"}


def test_unconnected_cloudflare_is_absent() -> None:
    servers = assemble_claude_mcp_servers(_seed_catalog(), _store())
    assert "cloudflare" not in servers
