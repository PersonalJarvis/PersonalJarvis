"""Sandbox identity, policy selection and persisted execution placement."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.society.capabilities import capability_id_for_tool, tool_name_for_capability
from jarvis.society.roster import RosterError
from jarvis.society.sandbox import Sandbox, SandboxTool, for_agent
from jarvis.society.surface import society_system_extra, society_tool_filter, society_tools
from tests.unit.society.test_surface import _live_chat, _tool

pytest_plugins = ["tests.unit.society.test_surface"]


def test_pydantic_execution_environment_parity():
    from typing import get_args

    from jarvis.society.events import ExecutionEnvironment
    from jarvis.ui.web.society_routes import CreateAgentBody, PatchAgentBody

    assert CreateAgentBody.model_fields["execution_environment"].annotation is ExecutionEnvironment
    assert ExecutionEnvironment in get_args(
        PatchAgentBody.model_fields["execution_environment"].annotation
    )


async def test_environment_round_trip_and_refused_runners(rt):
    agent, _ = await rt.roster.create(
        name="Sandbox",
        provider="openai",
        execution_environment="sandbox",
    )
    assert (await rt.roster.get(agent.agent_id)).to_dict()["execution_environment"] == "sandbox"
    for fields in (
        {"provider": "codex"},
        {"runtime": "hermes"},
        {"execution_environment": "unknown"},
    ):
        with pytest.raises(RosterError):
            await rt.roster.update(agent.agent_id, fields)
    assert (await rt.roster.get(agent.agent_id)).execution_environment == "sandbox"
    assert (
        await rt.roster.update(agent.agent_id, {"execution_environment": "local"})
    ).execution_environment == "local"


@pytest.mark.parametrize("provider", ["codex", "grok-build"])
async def test_cli_sandbox_cannot_be_created(rt, provider):
    with pytest.raises(RosterError):
        await rt.roster.create(name="Native", provider=provider, execution_environment="sandbox")


async def test_only_bound_sandbox_tool_and_question_survive(rt, tmp_path):
    agent, _ = await rt.roster.create(
        name="Box", provider="openai", execution_environment="sandbox"
    )
    session = SimpleNamespace(
        session_id=agent.session_id, permission_mode="bypass", cwd=str(tmp_path)
    )
    _live_chat(rt, session)
    briefing = await society_system_extra(None, None, session)
    assert "No host tools" in briefing
    from jarvis.society.surface import browser_tool_for_session, coding_tool_for_session

    assert await browser_tool_for_session(session.session_id) is None
    assert await coding_tool_for_session(session.session_id) is None
    tools = society_tools(None, None, session)
    assert set(tools) == {"society_sandbox", "society_ask_user"}
    tools.update(
        {
            name: _tool(name)
            for name in (
                "Write",
                "RunCommand",
                "society_shell",
                "society_browser",
                "society_run_skill",
                "coding-session",
                "gmail",
                "mcp/shell",
            )
        }
    )
    selected = society_tool_filter(session)(tools)
    assert set(selected) == {"society_sandbox", "society_ask_user"}
    tools["society_sandbox"] = _tool("society_sandbox")
    assert "society_sandbox" not in society_tool_filter(session)(tools)


async def test_denied_sandbox_and_stale_local_tools_fail_closed(rt, tmp_path):
    agent, _ = await rt.roster.create(name="Moving", provider="openai")
    session = SimpleNamespace(
        session_id=agent.session_id, permission_mode="bypass", cwd=str(tmp_path)
    )
    _live_chat(rt, session)
    await society_system_extra(None, None, session)
    selected = society_tool_filter(session)(society_tools(None, None, session))
    await rt.roster.update(agent.agent_id, {"execution_environment": "sandbox"})
    result = await selected["Write"].execute(
        {"file_path": "escape.txt", "content": "no"}, SimpleNamespace()
    )
    assert not result.success
    assert not (tmp_path / "escape.txt").exists()
    await rt.roster.update(agent.agent_id, {"denies": ["core:sandbox"]})
    await society_system_extra(None, None, session)
    assert "society_sandbox" not in society_tool_filter(session)(society_tools(None, None, session))


def test_storage_is_per_identity_and_runtime(tmp_path):
    runtime = SimpleNamespace(data_dir=tmp_path)
    assert for_agent(runtime, "one").volume != for_agent(runtime, "two").volume
    assert for_agent(runtime, "one").volume == for_agent(runtime, "one").volume
    assert capability_id_for_tool("society_sandbox") == "core:sandbox"
    assert tool_name_for_capability("core:sandbox") == "society_sandbox"


async def test_cache_miss_cannot_execute_a_host_reporting_tool(rt):
    agent, _ = await rt.roster.create(
        name="Uncached", provider="openai", execution_environment="sandbox",
    )
    session = SimpleNamespace(session_id=agent.session_id)
    leaked = _tool("society_conversation_recall")
    leaked.risk_tier = "safe"
    selected = society_tool_filter(session)({leaked.name: leaked})
    result = await selected[leaked.name].execute({}, None)
    assert not result.success and "sandbox" in result.error


def test_launch_has_no_host_mount_or_network(tmp_path):
    args = Sandbox(tmp_path)._arguments("test")
    assert args[args.index("--network") + 1] == "none"
    assert args[args.index("--user") + 1] == "65532:65532"
    assert args[args.index("--mount") + 1].startswith("type=volume,")
    assert "--read-only" in args
    assert "--cap-drop" in args
    assert not any(str(tmp_path) in a or "docker.sock" in a for a in args)


async def test_tool_unavailable_never_uses_host(rt, tmp_path, monkeypatch):
    from jarvis.society import sandbox

    agent, _ = await rt.roster.create(
        name="Offline", provider="openai", execution_environment="sandbox"
    )
    monkeypatch.setattr(sandbox.shutil, "which", lambda _: None)
    result = await SandboxTool(rt, agent.agent_id, tmp_path).execute(
        {"action": "run", "command": "echo forbidden > escaped.txt"},
        None,
    )
    assert not result.success and "Docker" in result.error
    assert not (tmp_path / "escaped.txt").exists()
