"""bootstrap_missions wires connected plugins into the claude-cli worker.

`_assemble_worker_mcp_servers` is the bootstrap-side glue: it reads the
connected marketplace plugins and produces the claude-cli ``mcpServers`` map
for ClaudeDirectWorker. It must NEVER raise — a marketplace / keyring hiccup
must degrade to "worker runs without plugins", not crash mission dispatch.
"""
from __future__ import annotations

from jarvis.missions.init import _assemble_worker_mcp_servers
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore


def test_assembles_connected_plugins() -> None:
    ts = TokenStore(InMemoryBackend())
    ts.save("github", Tokens(access="ghp_X"))
    servers = _assemble_worker_mcp_servers(token_store=ts, mcp_json_servers={})
    assert "github" in servers


def test_no_connections_is_empty() -> None:
    servers = _assemble_worker_mcp_servers(
        token_store=TokenStore(InMemoryBackend()), mcp_json_servers={}
    )
    assert servers == {}


def test_failure_degrades_to_empty_not_raise() -> None:
    class _BoomStore:
        def load(self, _plugin_id: str):  # noqa: ANN001
            raise RuntimeError("keyring unavailable")

    # Must not raise; a broken store yields no plugins.
    assert _assemble_worker_mcp_servers(token_store=_BoomStore(), mcp_json_servers={}) == {}


def test_mcp_json_servers_reach_the_worker() -> None:
    # self-added MCP server (the "MCPs" section) with NO marketplace plugin
    ts = TokenStore(InMemoryBackend())
    mcp_json = {"my-srv": {"command": "npx", "args": ["-y", "x"], "enabled": True}}
    servers = _assemble_worker_mcp_servers(token_store=ts, mcp_json_servers=mcp_json)
    assert "my-srv" in servers
    assert servers["my-srv"]["command"] == "npx"


def test_marketplace_and_mcp_json_merge() -> None:
    ts = TokenStore(InMemoryBackend())
    ts.save("github", Tokens(access="ghp_X"))
    mcp_json = {"my-srv": {"command": "npx", "enabled": True}}
    servers = _assemble_worker_mcp_servers(token_store=ts, mcp_json_servers=mcp_json)
    assert "github" in servers and "my-srv" in servers
