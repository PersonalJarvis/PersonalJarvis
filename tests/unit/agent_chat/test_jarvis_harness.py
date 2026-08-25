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


def test_a_tool_result_becomes_text_the_model_can_read():
    assert server._render(_Result(success=True, output="plain")) == "plain"
    assert server._render(_Result(success=True, output={"a": 1})) == '{"a": 1}'
    assert server._render(_Result(success=True, output=None)) == "Done."
    assert "no reason given" in server._render(_Result(success=False))
    assert "went wrong" in server._render(_Result(success=False, error="went wrong"))


def test_the_config_is_withheld_until_the_app_can_actually_serve_it(monkeypatch):
    """Half a config is worse than none: the CLI would fail to connect mid-turn."""
    monkeypatch.setattr(jarvis_harness, "control_key", lambda: "k")
    assert jarvis_harness.mcp_config_json() is None  # no base URL yet
    assert jarvis_harness.codex_config_args() == []

    runtime_refs.set_api_base_url("http://127.0.0.1:47821")
    monkeypatch.setattr(jarvis_harness, "control_key", lambda: None)
    assert jarvis_harness.mcp_config_json() is None  # no key
    assert jarvis_harness.codex_config_args() == []


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
