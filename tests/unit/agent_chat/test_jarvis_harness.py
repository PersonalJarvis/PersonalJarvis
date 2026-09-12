"""The chat runs INSIDE the Jarvis harness — the wiring that makes that true.

Covers the three seams that decide whether a typed turn is Jarvis or a coding
agent in a folder: which tools are offered, how they are executed, and what the
spawned CLI is actually told.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from jarvis.agent_chat import jarvis_harness
from jarvis.core import runtime_refs
from jarvis.mcp import jarvis_tools_server as server


@dataclass
class _Result:
    success: bool = True
    output: Any = None
    error: str | None = None


@dataclass
class _Descriptor:
    name: str
    description: str = "a tool"
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    risk_tier: str = "safe"
    is_action_tool: bool = False


@dataclass
class _Gateway:
    names: tuple[str, ...] = ("open-app", "spawn-worker", "wiki-recall")
    calls: list[tuple[str, dict[str, Any], Any]] = field(default_factory=list)
    raises: bool = False

    def catalog(self) -> tuple[_Descriptor, ...]:
        return tuple(_Descriptor(name=n) for n in self.names)

    async def execute(self, name: str, arguments: dict[str, Any], request: Any) -> _Result:
        if self.raises:
            raise RuntimeError("boom")
        self.calls.append((name, arguments, request))
        return _Result(success=True, output={"ok": name})


@pytest.fixture(autouse=True)
def _clean_refs():
    yield
    runtime_refs._reset_for_tests()


def test_spawn_vehicles_are_never_offered_to_the_chat():
    """The session IS the worker; a spawn tool would only start another one (AP-5/AP-14)."""
    runtime_refs.set_supervisor_tool_gateway(_Gateway())
    offered = [entry.name for entry in server.offered_tools()]
    assert "open-app" in offered and "wiki-recall" in offered
    assert "spawn-worker" not in offered


def test_no_gateway_offers_nothing_instead_of_crashing():
    assert server.offered_tools() == []


def test_a_name_mcp_cannot_carry_is_dropped_not_renamed():
    runtime_refs.set_supervisor_tool_gateway(_Gateway(names=("fine-name", "not a name!")))
    assert [e.name for e in server.offered_tools()] == ["fine-name"]


def test_plugin_wire_names_are_stable_portable_and_unambiguous():
    canonical = "github/get_me"
    wire = server._wire_name(canonical)
    assert wire is not None and wire.startswith("github_get_me_")
    assert server._usable_name(wire) and len(wire) <= 64
    assert server._wire_name(canonical) == wire
    assert len(server._wire_name("a" * 128 + "/" + "b" * 128)) <= 64
    assert server._wire_name("github/not a tool") is None
    assert server._wire_name("github/get/me") is None
    assert server._wire_name("github_get_me") != wire
    assert server._wire_catalog([_Descriptor(canonical), _Descriptor(wire)]) == {}


@pytest.mark.asyncio
async def test_plugin_wire_listing_and_execution_preserve_canonical_gateway_name():
    import mcp.types as types

    gateway = _Gateway(names=("github/get_me", "spawn-worker"))
    runtime_refs.set_supervisor_tool_gateway(gateway)
    instance = server.build_server()
    listed = await instance.request_handlers[types.ListToolsRequest](types.ListToolsRequest())
    tools = listed.root.tools
    assert len(tools) == 1
    wire = tools[0].name
    assert "github/get_me" in tools[0].description
    request = types.CallToolRequest(params=types.CallToolRequestParams(name=wire, arguments={}))
    await instance.request_handlers[types.CallToolRequest](request)
    assert gateway.calls[-1][0] == "github/get_me"
    gateway.names = ()
    await instance.request_handlers[types.CallToolRequest](request)
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_plugin_wire_catalog_rechecks_session_scope():
    class ScopedGateway(_Gateway):
        permitted = ("github/get_me",)

        async def session_catalog(self, session_id):
            assert session_id == "scoped-chat"
            return tuple(_Descriptor(name) for name in self.permitted)

    gateway = ScopedGateway(names=("github/get_me", "other/list"))
    runtime_refs.set_supervisor_tool_gateway(gateway)
    token = server.CHAT_SESSION_REF.set("scoped-chat")
    try:
        entries = await server._session_wire_catalog()
        assert [entry.name for entry in entries.values()] == ["github/get_me"]
        gateway.permitted = ()
        assert await server._session_wire_catalog() == {}
    finally:
        server.CHAT_SESSION_REF.reset(token)


def test_a_tool_result_becomes_text_the_model_can_read():
    assert server._render(_Result(success=True, output="plain")) == "plain"
    assert server._render(_Result(success=True, output={"a": 1})) == '{"a": 1}'
    assert server._render(_Result(success=True, output=None)) == "Done."
    assert "no reason given" in server._render(_Result(success=False))
    assert "went wrong" in server._render(_Result(success=False, error="went wrong"))


def test_the_config_is_withheld_until_the_app_can_actually_serve_it(monkeypatch):
    """Half a config is worse than none: the CLI would fail to connect mid-turn."""
    monkeypatch.setattr(jarvis_harness, "control_key", lambda: "k")
    monkeypatch.setattr(jarvis_harness, "endpoint", lambda: None)
    assert jarvis_harness.mcp_config_json() is None  # no base URL yet
    assert jarvis_harness.codex_config_args() == []
    assert jarvis_harness.agy_mcp_server_entry() is None

    monkeypatch.setattr(
        jarvis_harness, "endpoint", lambda: "http://127.0.0.1:47821/api/control/mcp/"
    )
    monkeypatch.setattr(jarvis_harness, "control_key", lambda: None)
    assert jarvis_harness.mcp_config_json() is None  # no key
    assert jarvis_harness.codex_config_args() == []
    assert jarvis_harness.agy_mcp_server_entry() is None


def test_the_key_travels_in_the_environment_never_in_argv(monkeypatch):
    """argv is readable by every process on the machine; the control key is the boundary."""
    monkeypatch.setattr(jarvis_harness, "control_key", lambda: "super-secret-key")
    runtime_refs.set_api_base_url("http://127.0.0.1:47821")

    config = jarvis_harness.mcp_config_json() or ""
    codex_args = " ".join(jarvis_harness.codex_config_args())
    assert "super-secret-key" not in config
    assert "super-secret-key" not in codex_args
    assert "${JARVIS_CONTROL_API_KEY}" in config
    assert "bearer_token_env_var" in codex_args

    env = jarvis_harness.apply_env({})
    assert env["JARVIS_CONTROL_API_KEY"] == "super-secret-key"


def test_both_cli_shapes_point_at_the_same_endpoint(monkeypatch):
    monkeypatch.setattr(jarvis_harness, "control_key", lambda: "k")
    runtime_refs.set_api_base_url("http://127.0.0.1:47921/")
    url = "http://127.0.0.1:47921/api/control/mcp"
    assert url in (jarvis_harness.mcp_config_json() or "")
    assert any(url in arg for arg in jarvis_harness.codex_config_args())
    agy = jarvis_harness.agy_mcp_server_entry("sess-9")
    assert agy is not None
    assert url in agy["serverUrl"]
    assert agy["headers"][jarvis_harness.HEADER_NAME] == "sess-9"


def test_agy_plugin_is_skipped_when_the_app_is_not_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(jarvis_harness, "control_key", lambda: None)
    assert jarvis_harness.install_agy_jarvis_plugin(tmp_path, "sess") is None
    assert not (tmp_path / ".agents").exists()


def test_the_preamble_names_the_prefix_the_tools_actually_get():
    """If the prefix drifts, the model is told to use tools it cannot see."""
    assert "mcp__jarvis__" in jarvis_harness.SYSTEM_PREAMBLE
    assert jarvis_harness._SERVER_NAME == "jarvis"


def test_claude_argv_carries_the_tools_and_the_identity(monkeypatch):
    from jarvis.agent_chat import runner_cli

    monkeypatch.setattr(runner_cli, "claude_argv_prefix", lambda: ["claude"])
    monkeypatch.setattr(runner_cli.jarvis_harness, "control_key", lambda: "k")
    runtime_refs.set_api_base_url("http://127.0.0.1:47821")

    from pathlib import Path

    plan = runner_cli.plan_claude(
        prompt="hi",
        cwd=Path.cwd(),
        model="claude-opus-5",
        effort="high",
        permission_mode="ask",
        resume=None,
    )
    assert "--mcp-config" in plan.argv
    assert "--append-system-prompt" in plan.argv
    assert plan.env["JARVIS_CONTROL_API_KEY"] == "k"


def test_a_chat_without_a_ready_app_spawns_exactly_as_before(monkeypatch):
    """No tools to offer must never cost the person their turn."""
    from pathlib import Path

    from jarvis.agent_chat import runner_cli

    monkeypatch.setattr(runner_cli, "claude_argv_prefix", lambda: ["claude"])
    monkeypatch.setattr(runner_cli.jarvis_harness, "control_key", lambda: None)

    plan = runner_cli.plan_claude(
        prompt="hi",
        cwd=Path.cwd(),
        model="",
        effort="",
        permission_mode="ask",
        resume=None,
    )
    assert "--mcp-config" not in plan.argv
    assert "--append-system-prompt" not in plan.argv
