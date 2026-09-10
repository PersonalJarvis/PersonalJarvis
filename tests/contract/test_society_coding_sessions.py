"""Society-to-IDE contracts, exercised through the real registry with fake PTYs."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import agent_transcript, control, fleet_actions
from jarvis.agentic_ide import session as sessions
from jarvis.agentic_ide.control import CodingSessionControl
from jarvis.agentic_ide.session import Registry, SessionError
from jarvis.society.coding_tool import CodingSessionTool
from jarvis.society.runtime import SocietyRuntime
from jarvis.society.surface import society_system_extra, society_tool_filter, society_tools
from jarvis.workspace import agents
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture
async def rig(tmp_path, monkeypatch):
    from jarvis import agent_accounts
    from jarvis.workspace import trust

    monkeypatch.setattr(trust, "ensure_trusted", lambda *args: [])
    monkeypatch.setattr(agent_accounts, "_store_path", lambda: tmp_path / "accounts.json")
    monkeypatch.setattr(agent_accounts, "_accounts_root", lambda: tmp_path / "accounts")
    for entry in agents.coding_agents():
        for variable, template in entry.account.env if entry.account else ():
            monkeypatch.setenv(variable, template.format(dir=tmp_path / entry.name))
    manager = FakePtyManager()
    registry = Registry(pty_manager=manager)
    monkeypatch.setattr(sessions, "agent_argv", lambda name: ("fake-" + name,))
    monkeypatch.setattr(control, "agent_argv", lambda name: ("fake-" + name,))
    monkeypatch.setattr(agents, "pty_available", lambda: True)
    monkeypatch.setattr(registry, "_prepare_spawn", lambda *args: None)

    async def ready(owner, names, **kwargs):
        return tuple(names)

    monkeypatch.setattr(fleet_actions, "wait_for_prompt_ready", ready)
    runtime = SocietyRuntime(tmp_path / "society")
    await runtime.ensure_started()
    gateway = CodingSessionControl(registry)
    runtime._coding_sessions = gateway
    agent, _ = await runtime.roster.create(name="New coding coordinator", grant_mode="all")
    tool = CodingSessionTool(runtime, agent.agent_id)
    try:
        yield registry, manager, gateway, runtime, tool, agent
    finally:
        await registry.close_all()
        await runtime.close()


async def open_one(rig, tmp_path, agent="claude"):
    return await rig[2].run({"action": "open", "cwd": str(tmp_path), "agent": agent})


@pytest.mark.parametrize("agent", ["claude", "codex", "opencode"])
async def test_headless_open_uses_project_and_registered_cli(rig, tmp_path, agent):
    opened = await open_one(rig, tmp_path, agent)
    assert opened["started"] is True
    assert opened["agent"] == agent
    assert rig[1].spawns[0]["cwd"] == str(tmp_path)
    assert rig[1].spawns[0]["argv"][0] == "fake-" + agent
    term = rig[0].find_terminal(opened["terminal_id"], opened["workspace_id"])[1]
    assert term.viewer_output is None and not term.watchers


async def test_duplicate_open_and_retries_are_durable(rig, tmp_path):
    args = {"action": "open", "cwd": str(tmp_path), "agent": "claude", "request_id": "work-1"}
    first, concurrent = await asyncio.gather(rig[4].execute(args, None), rig[4].execute(args, None))
    assert first.success and concurrent.success
    assert len(rig[1].spawns) == 1
    retry = await CodingSessionTool(rig[3], rig[5].agent_id).execute(args, None)
    assert retry.output == first.output
    collision = await rig[4].execute({**args, "cwd": str(tmp_path / "other")}, None)
    assert not collision.success
    assert len(rig[1].spawns) == 1


async def test_new_society_agent_gets_grantable_gated_tool(rig, tmp_path):
    runtime, agent = rig[3], rig[5]
    session = SimpleNamespace(session_id=agent.session_id)
    cfg = SimpleNamespace(wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")))
    await society_system_extra(cfg, None, session)
    selected = society_tool_filter(session)(society_tools(cfg, None, session))
    assert selected["coding-session"].risk_tier_for_args({"action": "open"}) == "ask"
    assert selected["coding-session"].risk_tier_for_args({"action": "context"}) == "monitor"
    await runtime.roster.update(agent.agent_id, {"denies": ["core:coding-session"]})
    denied = await rig[4].execute({"action": "discover"}, None)
    assert not denied.success
    await society_system_extra(cfg, None, session)
    assert "coding-session" not in society_tool_filter(session)(society_tools(cfg, None, session))


async def test_tab_switch_and_rename_during_slow_readiness(rig, tmp_path, monkeypatch):
    first = await open_one(rig, tmp_path)
    other_path = tmp_path / "second"
    other_path.mkdir()
    second = await open_one(rig, other_path)
    registry, manager = rig[:2]
    target = registry.find_terminal(first["terminal_id"], first["workspace_id"])[1]
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait(owner, names, **kwargs):
        assert owner.id == first["workspace_id"]
        entered.set()
        await release.wait()
        assert owner.find(names[0]) is target
        return tuple(names)

    async def confirm(term, payload, manager, multiline):
        manager.write(term.pty_id, payload)
        return True

    monkeypatch.setattr(fleet_actions, "wait_for_prompt_ready", wait)
    monkeypatch.setattr(registry, "_write_and_confirm", confirm)
    task = asyncio.create_task(
        rig[2].run({"action": "send", **first, "prompt": "Check this project"})
    )
    await asyncio.wait_for(entered.wait(), 5)
    await registry.activate(first["workspace_id"])
    await registry.rename_terminal("T1", "Renamed")
    await registry.activate(second["workspace_id"])
    release.set()
    result = await task
    assert result["delivery"] == "accepted" and result["completed"] is False
    assert manager.writes[-1] == (target.pty_id, "Check this project")


async def test_closed_identity_never_falls_back_to_replacement_name(rig, tmp_path):
    first = await open_one(rig, tmp_path)
    await rig[0].close_terminal(first["terminal_id"], workspace_id=first["workspace_id"])
    await open_one(rig, tmp_path)
    with pytest.raises(SessionError, match="closed"):
        await rig[2].run({"action": "context", **first})


async def test_closing_during_readiness_prevents_write(rig, tmp_path, monkeypatch):
    opened = await open_one(rig, tmp_path)

    async def wait(owner, names, **kwargs):
        await rig[0].close_terminal(opened["terminal_id"], workspace_id=owner.id)
        return tuple(names)

    monkeypatch.setattr(fleet_actions, "wait_for_prompt_ready", wait)
    before = list(rig[1].writes)
    with pytest.raises(SessionError, match="changed"):
        await rig[2].run({"action": "send", **opened, "prompt": "Never deliver"})
    assert rig[1].writes == before


@pytest.mark.parametrize(
    "submitted,delivery", [(True, "accepted"), (False, "not_accepted"), (None, "uncertain")]
)
async def test_send_receipt_prevents_duplicate_delivery(
    rig, tmp_path, monkeypatch, submitted, delivery
):
    opened = await open_one(rig, tmp_path)
    calls = []

    async def ready(owner, names, **kwargs):
        return tuple(names)

    async def confirm(term, payload, manager, multiline):
        calls.append(payload)
        await asyncio.sleep(0)
        return submitted

    monkeypatch.setattr(fleet_actions, "wait_for_prompt_ready", ready)
    monkeypatch.setattr(rig[0], "_write_and_confirm", confirm)
    args = {"action": "send", **opened, "prompt": "One assignment", "request_id": "one-send"}
    a, b = await asyncio.gather(rig[4].execute(args, None), rig[4].execute(args, None))
    assert a.output["delivery"] == delivery
    assert (await rig[4].execute(args, None)).output == a.output
    assert calls == ["One assignment"]


async def test_missing_transcript_and_bounded_recorded_context(rig, tmp_path, monkeypatch):
    opened = await open_one(rig, tmp_path)
    term = rig[0].find_terminal(opened["terminal_id"], opened["workspace_id"])[1]
    monkeypatch.setattr(agent_transcript, "can_read", lambda agent: False)
    empty = await rig[2].run({"action": "context", **opened})
    assert not empty["readable"] and not empty["available"] and empty["events"] == []
    monkeypatch.setattr(agent_transcript, "can_read", lambda agent: True)
    term.resume = SimpleNamespace(id="recorded-session")
    events = [
        {"kind": kind, "text": "recorded"}
        for kind in ("message", "tool_call", "tool_result", "reasoning")
    ]
    seen = []

    def read(agent, handle, **kwargs):
        seen.append((agent, handle, kwargs))
        return SimpleNamespace(events=events)

    monkeypatch.setattr(agent_transcript, "read_timeline", read)
    page = await rig[2].run({"action": "context", **opened, "limit": 2})
    tail = await rig[2].run({"action": "context", **opened, "cursor": page["cursor"]})
    assert page["events"] + tail["events"] == events
    assert seen[0][1] == "recorded-session"
    term.resume = None  # avoid serializing the deliberately minimal transcript fake at teardown


async def test_unavailable_pty_and_invalid_project_do_not_spawn(rig, monkeypatch):
    monkeypatch.setattr(agents, "pty_available", lambda: False)
    with pytest.raises(SessionError, match="unavailable"):
        await rig[2].run({"action": "open", "agent": "claude", "cwd": "relative"})
    monkeypatch.setattr(agents, "pty_available", lambda: True)
    with pytest.raises(SessionError, match="absolute"):
        await rig[2].run({"action": "open", "agent": "claude", "cwd": "relative"})
    assert not rig[1].spawns


async def test_subscription_seat_gets_scoped_catalog_and_executor_gate(rig):
    from uuid import uuid4

    from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
    from jarvis.core.protocols import SupervisorToolRequest, ToolResult
    from jarvis.society.surface import coding_tool_for_session

    calls = []

    class Executor:
        async def execute(self, tool, arguments, **kwargs):
            calls.append((tool, arguments, kwargs))
            return ToolResult(False, {}, "Approval required")

    gateway = BrainSupervisorToolGateway(
        SimpleNamespace(_tools={}, _tool_executor=Executor()),
        session_tool=coding_tool_for_session,
    )
    assert gateway.catalog() == ()  # no controller in the recursive worker catalog
    assert await gateway.session_catalog("ordinary-chat") == ()
    catalog = await gateway.session_catalog(rig[5].session_id)
    assert [d.name for d in catalog] == ["coding-session"]
    request = SupervisorToolRequest(
        trace_id=uuid4(),
        origin="agent-chat",
        user_utterance="",
        config_snapshot={"approval_ref": "agent-chat:" + rig[5].session_id},
    )
    result = await gateway.execute("coding-session", {"action": "open"}, request)
    assert not result.success and result.error == "Approval required"
    assert calls[0][0].risk_tier_for_args({"action": "open"}) == "ask"
    denied = await gateway.execute(
        "coding-session",
        {"action": "open"},
        SupervisorToolRequest(
            trace_id=uuid4(),
            origin="mission-worker",
            user_utterance="",
            config_snapshot=request.config_snapshot,
        ),
    )
    assert not denied.success and len(calls) == 1


async def test_interrupted_request_is_not_replayed(rig):
    entered = asyncio.Event()
    calls = []

    class SlowGateway:
        async def run(self, args):
            calls.append(args)
            entered.set()
            await asyncio.Event().wait()

    rig[3]._coding_sessions = SlowGateway()
    args = {"action": "open", "request_id": "interrupted"}
    task = asyncio.create_task(rig[4].execute(args, None))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    replay = await CodingSessionTool(rig[3], rig[5].agent_id).execute(args, None)
    assert replay.output["delivery"] == "uncertain" and len(calls) == 1


async def test_concurrent_distinct_prompts_are_serialized(rig, tmp_path, monkeypatch):
    opened = await open_one(rig, tmp_path)
    active = 0
    peak = 0

    async def confirm(term, payload, manager, multiline):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        await asyncio.sleep(0.01)
        active -= 1
        return True

    monkeypatch.setattr(rig[0], "_write_and_confirm", confirm)
    await asyncio.gather(
        *[
            rig[0].send_prompt(opened["terminal_id"], text, workspace_id=opened["workspace_id"])
            for text in ("first", "second")
        ]
    )
    assert peak == 1


async def test_slow_start_does_not_type_before_readiness(rig, tmp_path, monkeypatch):
    opened = await open_one(rig, tmp_path)

    async def not_ready(owner, names, **kwargs):
        return ()

    monkeypatch.setattr(fleet_actions, "wait_for_prompt_ready", not_ready)
    before = list(rig[1].writes)
    with pytest.raises(SessionError, match="still starting"):
        await rig[2].run({"action": "send", **opened, "prompt": "Wait for input"})
    assert rig[1].writes == before


async def test_invalid_account_cannot_silently_switch_subscription(rig, tmp_path):
    with pytest.raises(SessionError, match="account"):
        await rig[2].run(
            {
                "action": "open",
                "cwd": str(tmp_path),
                "agent": "claude",
                "account": "missing-seat",
            }
        )
    assert not rig[1].spawns


async def test_restore_preserves_workspace_and_pane_identity(rig, tmp_path):
    opened = await open_one(rig, tmp_path)
    snapshot = rig[0].snapshot()
    await rig[0].close_all()
    await rig[0].restore(snapshot)
    context = await rig[2].run({"action": "context", **opened})
    assert context["workspace_id"] == opened["workspace_id"]
    assert context["terminal_id"] == opened["terminal_id"]
    assert context["cwd"] == str(tmp_path)


async def test_rest_composition_pins_workspace_and_persistent_pane(rig, tmp_path, monkeypatch):
    from jarvis.ui.web import agentic_ide_routes as routes

    first = await open_one(rig, tmp_path)
    second_path = tmp_path / "second"
    second_path.mkdir()
    second = await open_one(rig, second_path)
    target = rig[0].find_terminal(first["terminal_id"], first["workspace_id"])[1]

    async def compose(prompt, **kwargs):
        assert kwargs["session"].folder == str(tmp_path)
        await rig[0].activate(first["workspace_id"])
        await rig[0].rename_terminal("T1", "Renamed")
        await rig[0].activate(second["workspace_id"])
        return SimpleNamespace(text="Composed assignment", composed_by="test", files=[])

    async def confirm(term, payload, manager, multiline):
        manager.write(term.pty_id, payload)
        return True

    monkeypatch.setattr(routes, "get_registry", lambda: rig[0])
    from jarvis.agentic_ide import prompt_composer

    monkeypatch.setattr(prompt_composer, "compose", compose)
    monkeypatch.setattr(rig[0], "_write_and_confirm", confirm)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    result = await routes.terminal_prompt(
        request,
        "T1",
        routes.PromptRequest(prompt="Check this project", compose=True),
        workspace=first["workspace_id"],
    )
    assert result["submitted"] is True
    assert rig[1].writes[-1] == (target.pty_id, "Composed assignment")


async def test_new_registered_cli_is_discovered_and_can_open(rig, tmp_path, monkeypatch):
    from jarvis.workspace import launch_picks

    entry = agents.WorkspaceAgent(name="future-cli", display_name="Future CLI", needs_trust=False)
    monkeypatch.setitem(agents._AGENTS, entry.name, entry)

    async def detect():
        return [SimpleNamespace(name=entry.name, installed=True)]

    async def models():
        return {}

    monkeypatch.setattr(agents, "detect_agents", detect)
    monkeypatch.setattr(launch_picks, "live_models", models)
    discovered = await rig[2].run({"action": "discover"})
    assert discovered["agents"][0]["agent"] == entry.name
    opened = await open_one(rig, tmp_path, entry.name)
    assert opened["started"] and opened["agent"] == entry.name
    assert rig[1].spawns[0]["argv"][0] == "fake-future-cli"


async def test_plan_mode_cannot_use_subscription_gate(rig):
    from jarvis.society.surface import coding_tool_for_session

    rig[3]._get_chat = lambda: SimpleNamespace(
        store=SimpleNamespace(
            get_session=lambda session_id: SimpleNamespace(permission_mode="plan"),
        )
    )
    assert await coding_tool_for_session(rig[5].session_id) is None
    assert not (await rig[4].execute({"action": "open", "request_id": "plan"}, None)).success


async def test_busy_terminal_is_not_given_another_assignment(rig, tmp_path, monkeypatch):
    from jarvis.agentic_ide.activity import Reading

    opened = await open_one(rig, tmp_path)
    monkeypatch.setattr(sessions.Terminal, "reading", lambda self: Reading("working", 1.0))
    before = list(rig[1].writes)
    with pytest.raises(SessionError, match="busy"):
        await rig[2].run({"action": "send", **opened, "prompt": "Do not interrupt"})
    assert rig[1].writes == before


async def test_mcp_lists_the_tool_only_for_society_sessions(rig, monkeypatch):
    import mcp.types as types

    from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
    from jarvis.mcp import jarvis_tools_server
    from jarvis.society.surface import coding_tool_for_session

    gateway = BrainSupervisorToolGateway(
        SimpleNamespace(_tools={}),
        session_tool=coding_tool_for_session,
    )
    monkeypatch.setattr(jarvis_tools_server, "_gateway", lambda: gateway)
    server = jarvis_tools_server.build_server()
    handler = server.request_handlers[types.ListToolsRequest]
    token = jarvis_tools_server.CHAT_SESSION_REF.set(rig[5].session_id)
    try:
        result = await handler(types.ListToolsRequest(method="tools/list"))
        assert [tool.name for tool in result.root.tools] == ["coding-session"]
    finally:
        jarvis_tools_server.CHAT_SESSION_REF.reset(token)
    result = await handler(types.ListToolsRequest(method="tools/list"))
    assert result.root.tools == []


def test_account_discovery_reports_connection_without_credentials(monkeypatch):
    from jarvis import agent_accounts

    monkeypatch.setattr(agent_accounts, "platforms", lambda: ("codex",))
    monkeypatch.setattr(agent_accounts, "active_ids", lambda: {"codex": "seat"})
    monkeypatch.setattr(
        agent_accounts,
        "snapshots",
        lambda platform: [
            SimpleNamespace(
                account=SimpleNamespace(id="seat", label="Work account"),
                connected=False,
                mode="expired",
                email="private@example.invalid",
            )
        ],
    )
    assert CodingSessionControl._accounts() == [
        {
            "id": "seat",
            "agent": "codex",
            "name": "Work account",
            "connected": False,
            "mode": "expired",
            "active": True,
        }
    ]


async def test_jarvis_chat_receives_controller_without_becoming_a_coding_cli(rig, tmp_path):
    from jarvis.agent_chat.runner_brain import kit_payload

    session = SimpleNamespace(
        session_id="lead-chat", surface="jarvis", permission_mode="ask", cwd=str(tmp_path),
    )
    rig[3]._get_chat = lambda: SimpleNamespace(store=SimpleNamespace(
        get_session=lambda session_id: session if session_id == session.session_id else None,
    ))
    tools, _ = await kit_payload(session, SimpleNamespace(_config=None))
    assert "Read" in tools and "coding-session" in tools
    result = await tools["coding-session"].execute({
        "action": "open", "cwd": str(tmp_path), "agent": "codex", "request_id": "lead-request",
    }, None)
    assert result.success and result.output["agent"] == "codex"
    session.permission_mode = "plan"
    tools, _ = await kit_payload(session, SimpleNamespace(_config=None))
    assert tools is None or "coding-session" not in tools
