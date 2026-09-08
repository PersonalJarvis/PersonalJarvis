"""Headless voice management uses the same persisted roster as the Agents UI.

No provider, microphone, GPU, native API or background worker is needed to
create/reconfigure/read a persistent agent. Assignment remains scheduler-owned.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from jarvis.commands.registry import get_command
from jarvis.plugins.tool.app_command import AppCommandTool
from jarvis.plugins.tool.delegate_to_agent import DelegateToAgentTool, SocietyStatusTool
from jarvis.society.runtime import SocietyRuntime
from jarvis.ui.web.society_routes import router


@pytest.fixture
async def society(tmp_path):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    app = FastAPI()
    app.include_router(router)
    app.state.society_factory = lambda: runtime
    loader = AppCommandTool(transport=httpx.ASGITransport(app=app))
    commands = {tool.name: tool for tool in loader.expand()}
    try:
        yield runtime, commands
    finally:
        await runtime.close()


def context():
    return SimpleNamespace(config={"output_language": "en"}, trace_id=uuid4())


async def test_management_persists_and_echoes_same_roster_without_starting_work(society):
    runtime, commands = society
    created = await commands["society-create-agent"].execute(
        {
            "name": "Scout",
            "title": "Supplier researcher",
            "description": "Compare hosting suppliers and cite sources.",
        },
        context(),
    )
    assert created.success, created.error
    body = created.output["response"]
    assert body["created"] is True
    assert body["agent"]["agent_id"] == "scout"
    adopted = await commands["society-create-agent"].execute({"name": "Scout"}, context())
    assert adopted.output["response"]["created"] is False
    updated = await commands["society-update-agent"].execute(
        {
            "agent_id": "scout",
            "title": "Invoice reviewer",
            "state": "paused",
            "daily_budget_usd": 2,
        },
        context(),
    )
    assert updated.success, updated.error
    stored = await runtime.roster.get("scout")
    assert updated.output["response"]["agent"] == stored.to_dict()
    assert stored.title == "Invoice reviewer" and stored.state == "paused"
    assert stored.daily_budget_usd == 2
    status = await SocietyStatusTool(runtime_resolver=lambda: runtime).execute(
        {
            "agent": "scout",
            "details": True,
        },
        context(),
    )
    assert status.output["agents"][0]["agent"]["title"] == "Invoice reviewer"
    assert runtime.scheduler.active_runs("scout") == 0
    assert not await runtime.store.events_since(0)


async def test_management_preserves_learned_focus_and_explicit_approval_rules(society):
    runtime, commands = society
    await runtime.ensure_started()
    await runtime.roster.create(
        name="Scout",
        focus=["core:search-web"],
        approval_rules={"require_approval": ["send"], "always_allow": []},
    )
    result = await commands["society-update-agent"].execute(
        {
            "agent_id": "scout",
            "description": "Research new suppliers.",
        },
        context(),
    )
    assert result.success
    row = result.output["response"]["agent"]
    assert "core:search-web" in row["focus"]
    assert row["approval_rules"]["require_approval"] == ["send"]


async def test_management_rejects_missing_agent_invalid_model_and_unexposed_fields(society):
    runtime, commands = society
    missing = await commands["society-update-agent"].execute(
        {
            "agent_id": "unknown",
            "title": "Researcher",
        },
        context(),
    )
    assert not missing.success and "404" in missing.error
    invalid = await commands["society-create-agent"].execute(
        {
            "name": "Bad/Name",
        },
        context(),
    )
    assert not invalid.success
    await runtime.roster.create(name="Scout")
    model = await commands["society-switch-agent-model"].execute(
        {
            "agent_id": "scout",
            "provider": "nonexistent-provider",
        },
        context(),
    )
    assert not model.success and "422" in model.error
    escalation = await commands["society-update-agent"].execute(
        {
            "agent_id": "scout",
            "permission_ceiling": "unrestricted",
        },
        context(),
    )
    assert not escalation.success and "unknown argument" in escalation.error


def test_management_is_supervisor_only_and_degrades_without_server():
    for name in (
        "society-create-agent",
        "society-update-agent",
        "society-agent-catalog",
        "society-capability-catalog",
        "society-switch-agent-model",
    ):
        command = get_command(name)
        assert command is not None and not command.worker_allowed


async def test_management_unavailable_server_returns_honest_error():
    commands = {tool.name: tool for tool in AppCommandTool().expand()}
    result = await commands["society-create-agent"].execute({"name": "Scout"}, context())
    assert not result.success and "server is not available" in result.error


async def test_capability_catalog_reads_the_live_shared_catalog(society):
    runtime, commands = society
    before = await commands["society-capability-catalog"].execute({}, context())
    assert before.success, before.error
    assert before.output["response"]["capabilities"] == [row.to_dict() for row in runtime.catalog()]
    assert not await runtime.store.events_since(0)


@pytest.mark.parametrize(
    "terminal_event,expected_state",
    [
        ("MissionApproved", "done"),
        ("MissionFailed", "blocked"),
    ],
)
async def test_voice_assignment_tracks_fallback_mission_bridge(
    tmp_path, terminal_event, expected_state
):
    from jarvis.society.events import MsgType

    class FakeManager:
        async def dispatch(self, **kwargs):
            return "mission-1"

    runtime = SocietyRuntime(tmp_path, mission_manager=lambda: FakeManager())
    try:
        await runtime.ensure_started()
        await runtime.roster.create(name="Scout")
        result = await DelegateToAgentTool(runtime_resolver=lambda: runtime).execute(
            {"agent": "Scout", "task": "Compare suppliers."},
            context(),
        )
        assert result.success, result.error
        assignment_id, trace_id = result.output["assignment_id"], result.output["trace_id"]
        # Same mission trace, wrong agent/run: neither is proof for this assignment.
        await runtime.say(
            from_agent="other-agent",
            to_agent="jarvis",
            text="Unrelated result.",
            trace_id="mission:mission-1",
            msg_type=MsgType.RESULT,
            payload={"run_id": "mission-1", "status": "done"},
        )
        await runtime.say(
            from_agent="scout",
            to_agent="jarvis",
            text="Another run.",
            trace_id="mission:mission-1",
            msg_type=MsgType.RESULT,
            payload={"run_id": "mission-2", "status": "done"},
        )
        status_tool = SocietyStatusTool(runtime_resolver=lambda: runtime)
        pending = await status_tool.execute(
            {
                "assignment_id": assignment_id,
                "trace_id": trace_id,
            },
            context(),
        )
        assert pending.output["state"] == "running"
        await runtime.bridge.on_mission_envelope(
            SimpleNamespace(
                mission_id="mission-1",
                payload=SimpleNamespace(event_type=terminal_event, summary="Supplier comparison."),
            )
        )
        status = await status_tool.execute(
            {
                "assignment_id": assignment_id,
                "trace_id": trace_id,
            },
            context(),
        )
        assert status.output["state"] == expected_state
        final = status.output["events"][-1]
        assert final["trace_id"] == "mission:mission-1"
        assert final["parent_event_id"] is None
        assert final["payload"]["output"] == ["mission:mission-1"]
        assert final["payload"]["done"] == "Supplier comparison."
    finally:
        await runtime.close()
