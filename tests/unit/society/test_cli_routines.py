"""Subscription seats must be able to schedule and read back their own routines."""

from __future__ import annotations

import json
from types import SimpleNamespace

import mcp.types as types
import pytest

from jarvis.agent_chat.store import AgentChatStore
from jarvis.agent_chat.tool_context import register_turn, restore_turn, unregister_turn
from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.core import runtime_refs
from jarvis.core.protocols import ChatTurn, current_chat_turn
from jarvis.mcp.jarvis_tools_server import CHAT_SESSION_REF, build_server
from jarvis.society.runtime import SocietyRuntime
from jarvis.society.surface import tools_for_cli_session
from jarvis.tasks.context import client_timezone
from tests.fakes.fake_agent_chat import FakeChatService
from tests.unit.society.test_routines import FakeScheduler, FakeTaskStore


class ExecutingFake:
    """Record gateway routing, then exercise the actual owned tool."""

    def __init__(self):
        self.calls = []

    async def execute(self, tool, arguments, **kwargs):
        self.calls.append((tool.name, kwargs))
        return await tool.execute(arguments, SimpleNamespace(trace_id=kwargs["trace_id"]))


@pytest.fixture
async def world(tmp_path):
    chat_store = AgentChatStore(tmp_path / "chat.db")
    service = FakeChatService(chat_store)
    tasks = FakeTaskStore()
    scheduler = FakeScheduler(tasks)
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path)))
    rt = SocietyRuntime(
        tmp_path,
        seed_starter_team=False,
        chat_service=lambda: service,
        task_services=lambda: (tasks, scheduler),
        cfg=lambda: cfg,
    )
    await rt.ensure_started()
    agent, _ = await rt.roster.create(name="Scout")
    chat_store.create_session(
        session_id=agent.session_id,
        surface="society",
        provider="openai-codex",
        model="test-model",
        effort="medium",
        cwd=str(tmp_path / "workspace"),
        permission_mode="accept-edits",
    )
    executor = ExecutingFake()
    manager = SimpleNamespace(_tools={}, _tool_executor=executor, _config=cfg)

    async def scoped(session_id, tools):
        return await tools_for_cli_session(session_id, tools, manager)

    gateway = BrainSupervisorToolGateway(manager, session_tools=scoped)
    monkey_gateway = runtime_refs.get_supervisor_tool_gateway()
    runtime_refs.set_supervisor_tool_gateway(gateway)
    try:
        yield SimpleNamespace(
            rt=rt,
            store=chat_store,
            tasks=tasks,
            scheduler=scheduler,
            gateway=gateway,
            executor=executor,
            server=build_server(),
        )
    finally:
        runtime_refs.set_supervisor_tool_gateway(monkey_gateway)
        await rt.close()
        chat_store.close()


async def call(world, name, args=None, session="society:scout"):
    token = CHAT_SESSION_REF.set(session)
    try:
        result = await world.server.request_handlers[types.CallToolRequest](
            types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(name=name, arguments=args or {}),
            )
        )
        return result.root.content[0].text
    finally:
        CHAT_SESSION_REF.reset(token)


def args(quote):
    return {
        "kind": "routine",
        "mode": "apply",
        "request_quote": quote,
        "payload": {
            "title": "Hourly comment replies",
            "prompt": "Check my posts from the last five days and reply briefly to new comments.",
            "schedule": {"kind": "every", "interval_seconds": 3600},
        },
    }


async def test_cli_catalog_includes_routines_without_global_leak(world):
    catalog = await world.gateway.session_catalog("society:scout")
    assert {"society_routines", "society_propose_change"} <= {t.name for t in catalog}
    assert not any(t.name.startswith("society_") for t in world.gateway.catalog())
    assert await world.gateway.session_catalog("society:missing") == ()
    assert await world.gateway.session_catalog("ordinary-chat") == ()


@pytest.mark.parametrize("quote", ["Check comments every hour", "Yes", "Ja"])  # i18n-allow
async def test_explicit_cli_request_schedules_and_reads_back(world, quote):
    origin = ChatTurn("society:scout", "turn-1", quote, True, "trace-1")
    token = current_chat_turn.set(origin)
    zone = client_timezone.set("America/New_York")
    registered = register_turn(origin.session_id)
    current_chat_turn.reset(token)
    client_timezone.reset(zone)
    try:
        result = json.loads(await call(world, "society_propose_change", args(quote)))
        assert result["applied"] is True
        saved = json.loads(await call(world, "society_routines"))
        assert saved["timezone"] == "America/New_York"
        assert len(saved["routines"]) == 1
        assert saved["routines"][0]["trigger"]["interval_seconds"] == 3600
        assert saved["routines"][0]["state"] == "scheduled"
        assert world.scheduler.scheduled == ["task-1"]
        assert world.executor.calls[0][1]["user_utterance"] == quote
        assert current_chat_turn.get() is None
    finally:
        unregister_turn(origin.session_id, registered)
    stale = await call(world, "society_propose_change", args(quote))
    assert "exact current user request" in stale
    assert len(world.tasks.rows) == 1


@pytest.mark.parametrize(
    "direct_user,session,quote",
    [
        (False, "society:scout", "Check comments every hour"),
        (True, "society:other", "Check comments every hour"),
        (True, "society:scout", "fabricated request"),
        (True, "society:scout", "e"),
    ],
)
async def test_untrusted_or_cross_session_calls_cannot_apply(world, direct_user, session, quote):
    origin = ChatTurn(session, "turn-1", "Check comments every hour", direct_user, "trace")
    token = current_chat_turn.set(origin)
    registered = register_turn(session)
    current_chat_turn.reset(token)
    try:
        result = await call(world, "society_propose_change", args(quote))
        assert "exact current user request" in result
        assert world.tasks.rows == []
    finally:
        unregister_turn(session, registered)


async def test_read_only_session_can_inspect_but_not_schedule(world):
    world.store.update_session("society:scout", permission_mode="plan")
    catalog = {t.name for t in await world.gateway.session_catalog("society:scout")}
    assert "society_routines" in catalog
    assert "society_propose_change" not in catalog
    assert "no longer available" in await call(world, "society_propose_change", args("yes"))
    assert world.tasks.rows == []


async def test_grants_are_checked_again_at_call_time(world):
    tool = SimpleNamespace(name="gmail", schema={}, description="Mail", risk_tier="ask")

    async def execute(_args, _ctx):
        raise AssertionError("A denied tool must not execute")

    tool.execute = execute
    world.gateway._manager._tools["gmail"] = tool
    assert "gmail" in {t.name for t in await world.gateway.session_catalog("society:scout")}
    await world.rt.roster.update("scout", {"denies": ["plugin:gmail"]})
    assert "no longer available" in await call(world, "gmail")
    assert world.executor.calls == []


def test_context_cleanup_and_isolation():
    first = ChatTurn("society:first", "one", "yes", True, "trace")
    token = current_chat_turn.set(first)
    registered = register_turn(first.session_id)
    current_chat_turn.reset(token)
    try:
        with restore_turn("society:second"):
            assert current_chat_turn.get() is None
        with pytest.raises(RuntimeError), restore_turn(first.session_id):
            assert current_chat_turn.get() == first
            raise RuntimeError("tool failed")
        assert current_chat_turn.get() is None
    finally:
        unregister_turn(first.session_id, registered)
    with restore_turn(first.session_id):
        assert current_chat_turn.get() is None


def test_resumed_society_seat_refreshes_routine_contract(monkeypatch, tmp_path):
    from jarvis.agent_chat import jarvis_harness, runner_cli

    monkeypatch.setattr(runner_cli, "codex_argv_prefix", lambda: ["codex"])
    monkeypatch.setattr(jarvis_harness, "control_key", lambda: "test-key")
    monkeypatch.setattr(
        jarvis_harness, "endpoint", lambda: "http://localhost:47821/api/control/mcp/"
    )
    identity = jarvis_harness.Identity(
        session_id="society:scout", text="LARGE OLD IDENTITY", compact="old", path=None
    )
    plan = runner_cli.plan_codex(
        prompt="Check comments every hour",
        cwd=tmp_path,
        model="",
        effort="",
        permission_mode="accept-edits",
        resume="existing-thread",
        identity=identity,
    )
    assert "mcp_servers.jarvis.required=true" in plan.argv
    assert 'mcp_servers.jarvis.tools.society_propose_change.approval_mode="approve"' in plan.argv
    assert not any("default_tools_approval_mode" in arg for arg in plan.argv)
    assert "society_propose_change" in plan.stdin_text
    assert "LARGE OLD IDENTITY" not in plan.stdin_text
    assert plan.stdin_text.endswith("Check comments every hour")
    assert "test-key" not in " ".join(plan.argv)


def test_routine_approval_delegation_is_only_for_society(monkeypatch):
    from jarvis.agent_chat import jarvis_harness

    monkeypatch.setattr(jarvis_harness, "control_key", lambda: "test-key")
    monkeypatch.setattr(
        jarvis_harness, "endpoint", lambda: "http://localhost:47821/api/control/mcp/"
    )
    for session_id in (None, "ordinary-chat"):
        assert not any(
            "approval_mode" in arg for arg in jarvis_harness.codex_config_args(session_id)
        )
