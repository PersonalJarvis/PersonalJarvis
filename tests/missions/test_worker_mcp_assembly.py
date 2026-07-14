"""bootstrap_missions wires connected plugins into the claude-cli worker.

`_assemble_worker_mcp_servers` is the bootstrap-side glue: it reads the
connected marketplace plugins and produces the claude-cli ``mcpServers`` map
for ClaudeDirectWorker. It must NEVER raise — a marketplace / keyring hiccup
must degrade to "worker runs without plugins", not crash mission dispatch.

It also filters the exported servers down to those RELEVANT to the mission's
task text (the same plugin-relevance gate the router uses), one layer below the
router. The worker runs ``--permission-mode bypassPermissions``, so an exported
off-topic server is actually reachable and would re-introduce the ~35 s
wrong-MCP stall. The filter is reversible (``relevance_filter=False`` or the
``[brain.routing].worker_mcp_relevance_filter`` kill-switch) and ALWAYS degrades
to exporting on a fault (never silently strips a mission's MCPs).
"""

from __future__ import annotations

from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore
from jarvis.missions.init import _assemble_worker_mcp_servers


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
    # self-added MCP server (the "MCPs" section) with NO marketplace plugin.
    # No task_text => no relevance context => full export (degraded/back-compat).
    ts = TokenStore(InMemoryBackend())
    mcp_json = {"my-srv": {"command": "npx", "args": ["-y", "x"], "enabled": True}}
    servers = _assemble_worker_mcp_servers(token_store=ts, mcp_json_servers=mcp_json)
    assert "my-srv" in servers
    assert servers["my-srv"]["command"] == "npx"


def test_marketplace_and_mcp_json_merge() -> None:
    # No task_text => no relevance context => full export (degraded/back-compat).
    ts = TokenStore(InMemoryBackend())
    ts.save("github", Tokens(access="ghp_X"))
    mcp_json = {"my-srv": {"command": "npx", "enabled": True}}
    servers = _assemble_worker_mcp_servers(token_store=ts, mcp_json_servers=mcp_json)
    assert "github" in servers and "my-srv" in servers


# --- Per-task relevance filter ------------------------------------------------


def test_relevant_task_exports_named_server() -> None:
    # The task NAMES the server ("weather mcp"), so it is exported even with the
    # filter ON.
    ts = TokenStore(InMemoryBackend())
    mcp_json = {"weather-mcp": {"command": "npx", "enabled": True}}
    servers = _assemble_worker_mcp_servers(
        token_store=ts,
        mcp_json_servers=mcp_json,
        task_text="check the weather forecast for tomorrow",
        relevance_filter=True,
    )
    assert "weather-mcp" in servers


def test_topical_tool_noun_exports_server() -> None:
    # The server id does NOT name-match, but a distinctive noun mined from its
    # OWN tools ("flashcards") matches the task — the smart relevance stage.
    ts = TokenStore(InMemoryBackend())
    mcp_json = {"srv-x": {"command": "npx", "enabled": True}}
    tools = {
        "srv-x": [
            {
                "name": "srv-x/flashcards_create",
                "description": "make flashcards from your sources",
            }
        ]
    }
    servers = _assemble_worker_mcp_servers(
        token_store=ts,
        mcp_json_servers=mcp_json,
        task_text="please create some flashcards for me",
        relevance_filter=True,
        server_tools=tools,
    )
    assert "srv-x" in servers


def test_unrelated_task_drops_server() -> None:
    # An off-topic mission must NOT export an unrelated server (the over-trigger
    # hole this fix closes).
    ts = TokenStore(InMemoryBackend())
    mcp_json = {"weather-mcp": {"command": "npx", "enabled": True}}
    servers = _assemble_worker_mcp_servers(
        token_store=ts,
        mcp_json_servers=mcp_json,
        task_text="refactor the authentication module and add unit tests",
        relevance_filter=True,
        server_tools={"weather-mcp": []},
    )
    assert "weather-mcp" not in servers


def test_kill_switch_off_restores_full_export() -> None:
    # relevance_filter=False reproduces the prior behaviour exactly: the
    # unrelated server is exported (no filtering).
    ts = TokenStore(InMemoryBackend())
    mcp_json = {"weather-mcp": {"command": "npx", "enabled": True}}
    servers = _assemble_worker_mcp_servers(
        token_store=ts,
        mcp_json_servers=mcp_json,
        task_text="refactor the authentication module and add unit tests",
        relevance_filter=False,
        server_tools={"weather-mcp": []},
    )
    assert "weather-mcp" in servers


def test_relevance_fault_falls_back_to_export(monkeypatch) -> None:  # noqa: ANN001
    # A fault inside the relevance step must NEVER strip a mission's MCPs — the
    # server is exported (degrade to the old full-export behaviour).
    import jarvis.marketplace.plugin_relevance as pr

    def _boom(*_a, **_k):  # noqa: ANN002, ANN003
        raise RuntimeError("relevance engine exploded")

    monkeypatch.setattr(pr, "plugin_is_relevant", _boom)

    ts = TokenStore(InMemoryBackend())
    mcp_json = {"weather-mcp": {"command": "npx", "enabled": True}}
    servers = _assemble_worker_mcp_servers(
        token_store=ts,
        mcp_json_servers=mcp_json,
        task_text="refactor the authentication module",  # would normally drop
        relevance_filter=True,
        server_tools={"weather-mcp": []},
    )
    assert "weather-mcp" in servers


def test_live_registry_inspection_fault_falls_back_to_full_export(
    monkeypatch,
) -> None:  # noqa: ANN001
    """A swallowed registry fault must not look like valid empty evidence."""
    import jarvis.core.runtime_refs as runtime_refs

    def _boom():
        raise RuntimeError("live registry unavailable")

    monkeypatch.setattr(runtime_refs, "get_mcp_registry", _boom)
    ts = TokenStore(InMemoryBackend())
    mcp_json = {"weather-mcp": {"command": "npx", "enabled": True}}

    servers = _assemble_worker_mcp_servers(
        token_store=ts,
        mcp_json_servers=mcp_json,
        task_text="refactor the authentication module",
        relevance_filter=True,
    )

    assert "weather-mcp" in servers
