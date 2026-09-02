"""The Agent MCP contract: what any client connecting to Jarvis may rely on.

This is the promise the surface makes to the outside world, so it is pinned
here rather than left to drift: the tool set and its shape, the safety
properties (no spawn vehicle, dangerous work marked), and one end-to-end run
proving a client can find the team, talk to it, give it work and read the
result without ever touching the REST layer.

Headless by construction — no GPU, no audio, no model, no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from jarvis.core import runtime_refs
from jarvis.mcp.agents import SERVER_NAME, tool_specs
from jarvis.mcp.agents import tools as agent_tools
from jarvis.society.runtime import SocietyRuntime

#: The vocabulary a client is entitled to find. Adding a tool is a feature;
#: removing or renaming one breaks every connected client, so it fails here.
EXPECTED_TOOLS = {
    "ecosystem_status",
    "agents_list",
    "agent_get",
    "capabilities_list",
    "agent_chat",
    "agent_message",
    "agent_assign",
    "agent_inbox",
    "board_events",
    "quest_post",
    "quests_list",
    "quest_get",
    "quest_cancel",
    "quest_retry",
    "agent_create",
    "rooms_list",
    "room_open",
    "room_say",
    "room_settle",
    "approvals_list",
    "approval_resolve",
    "kill_switch",
    "ecosystem_export",
    "ecosystem_import",
}

#: Everything that starts spend, work, or stops the house. A tool that becomes
#: dangerous without being listed here is a client that stopped asking first.
EXPECTED_DANGEROUS = {
    "agent_chat",
    "quest_post",
    "quest_cancel",
    "quest_retry",
    "agent_message",
    "agent_assign",
    "room_say",
    "room_settle",
    "approval_resolve",
    "kill_switch",
    "ecosystem_import",
}

#: MCP's own rule for a tool name.
_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


class FakeManager:
    """A mission manager that accepts work without running any."""

    def __init__(self) -> None:
        self.dispatched: list[str] = []

    async def dispatch(self, *, prompt: str, **kwargs: Any) -> str:
        self.dispatched.append(prompt)
        return f"m-{len(self.dispatched)}"


@pytest.fixture
def ecosystem(tmp_path: Path) -> Any:
    """A live society reachable exactly the way the MCP tools reach it."""
    runtime = SocietyRuntime(
        tmp_path, seed_starter_team=False, mission_manager=lambda: FakeManager()
    )
    app = FastAPI()
    app.state.society = None
    app.state.society_factory = lambda: runtime
    runtime_refs.set_web_app(app)
    try:
        yield runtime
    finally:
        runtime_refs._reset_for_tests()


async def _call(tool: str, **args: Any) -> Any:
    """One tool call, decoded — the shape a client actually receives.

    The parameter is ``tool``, not ``name``: ``agent_create`` takes a ``name``
    argument of its own and a collision here would hide it.
    """
    text = await agent_tools.call(tool, args)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


# ------------------------------------------------------------ the contract


def test_tool_set_is_the_published_one() -> None:
    names = {spec["name"] for spec in tool_specs()}
    assert names == EXPECTED_TOOLS


def test_dangerous_tools_are_marked() -> None:
    marked = {spec["name"] for spec in tool_specs() if spec["dangerous"]}
    assert marked == EXPECTED_DANGEROUS


def test_every_tool_is_usable_by_a_client() -> None:
    """Name, description and schema — a client cannot call what it cannot read."""
    for spec in tool_specs():
        name = spec["name"]
        assert name and len(name) <= 128, name
        assert set(name) <= _NAME_CHARS, name
        assert len(spec["description"]) > 40, f"{name} has no usable description"
        schema = spec["inputSchema"]
        assert schema["type"] == "object", name
        assert "properties" in schema, name
        for prop, body in schema["properties"].items():
            assert "type" in body, f"{name}.{prop} has no type"


def test_no_spawn_vehicle_is_offered() -> None:
    """AP-5/AP-14: a client is a person with a keyboard, not a second router."""
    forbidden = ("spawn", "dispatch_to_harness", "multi_spawn", "sub_agent")
    for spec in tool_specs():
        blob = f"{spec['name']} {spec['description']}".lower()
        assert not any(f in spec["name"].lower() for f in forbidden), spec["name"]
        assert "spawn-worker" not in blob


def test_server_name_is_distinct_from_the_tools_surface() -> None:
    """Both surfaces may be connected at once, so their names must not collide."""
    from jarvis.mcp.jarvis_tools_server import build_server as tools_server

    assert SERVER_NAME == "jarvis-agents"
    assert tools_server().name != SERVER_NAME


def test_unknown_tool_answers_with_the_catalog() -> None:
    """A dead end for the model is a bug; a wrong name must teach the right ones."""
    import asyncio

    answer = asyncio.run(agent_tools.call("no_such_tool", {}))
    assert "No such tool" in answer
    assert "agents_list" in answer


# ------------------------------------------------------------- end to end


@pytest.mark.asyncio
async def test_a_client_runs_the_whole_loop_headless(ecosystem: Any) -> None:
    """Find the team, write to it, assign work, read the board — tools only."""
    # 1. The house, before anything exists.
    status = await _call("ecosystem_status")
    assert status["kill_switch"] is False
    assert status["agents_total"] == 1  # the lead seat is Jarvis'

    # 2. Hire a teammate.
    created = await _call(
        "agent_create",
        name="Scout",
        title="Research scout",
        description="Finds and checks facts on the open web.",
    )
    assert created["created"] is True
    scout_id = created["agent"]["agent_id"]

    # 3. It shows up on the roster, idle.
    roster = await _call("agents_list")
    rows = {a["name"]: a for a in roster["agents"]}
    assert "Scout" in rows
    assert rows["Scout"]["run_state"] == "idle"

    # 4. Leave it a note — by NAME, the way a person would.
    note = await _call("agent_message", agent="Scout", text="Welcome aboard.")
    assert note["delivered_to"] == "Scout"
    assert note["event"]["msg_type"] == "SAY"

    # 5. The note is in its inbox, and the board agrees.
    inbox = await _call("agent_inbox", agent="Scout")
    assert [e["msg_type"] for e in inbox["events"]] == ["SAY"]
    board = await _call("board_events")
    assert board["last_seq"] == inbox["last_seq"]

    # 6. Give it a task. The scheduler answers on the same trace.
    assigned = await _call("agent_assign", agent=scout_id, task="Check the changelog.")
    assert assigned["assigned_to"] == "Scout"
    assert assigned["trace_id"].startswith("task:")
    assert assigned["outcome"] is not None, "the scheduler must answer an ASSIGN"

    # 7. Reading one agent shows the trail.
    detail = await _call("agent_get", agent="Scout")
    assert detail["agent"]["agent_id"] == scout_id
    assert {e["msg_type"] for e in detail["recent_events"]} >= {"SAY", "ASSIGN"}


@pytest.mark.asyncio
async def test_unknown_agent_names_the_known_ones(ecosystem: Any) -> None:
    answer = await agent_tools.call("agent_message", {"agent": "Nobody", "text": "hi"})
    assert "Nobody" in answer
    assert "no agent by that id or name" in answer
    assert "Jarvis" in answer, "the refusal must list who DOES exist"


@pytest.mark.asyncio
async def test_kill_switch_stops_conversation(ecosystem: Any) -> None:
    """The emergency stop is real: with it engaged, nobody can be talked to."""
    await _call("agent_create", name="Scout", title="Scout", description="Finds things.")
    engaged = await _call("kill_switch", engage=True)
    assert engaged["engaged"] is True

    refused = await agent_tools.call("agent_chat", {"agent": "Scout", "text": "hello"})
    assert "kill switch" in refused.lower()

    released = await _call("kill_switch", engage=False)
    assert released.get("engaged") is False


@pytest.mark.asyncio
async def test_rooms_are_bounded_and_named_members_resolve(ecosystem: Any) -> None:
    await _call("agent_create", name="Scout", title="Scout", description="Finds things.")
    await _call("agent_create", name="Archivist", title="Curator", description="Keeps notes.")

    opened = await _call(
        "room_open", members=["Scout", "Archivist"], topic="Where do we store facts?"
    )
    room = opened["room"]
    assert len(room["members"]) == 2
    assert room["state"] in ("queued", "running")

    listed = await _call("rooms_list")
    assert [r["room_id"] for r in listed["rooms"]] == [room["room_id"]]

    # A single-member room is not a discussion.
    refused = await agent_tools.call("room_open", {"members": ["Scout"], "topic": "alone"})
    assert "at least 2 members" in refused


@pytest.mark.asyncio
async def test_a_broken_call_never_raises_out_of_the_surface(ecosystem: Any) -> None:
    """Every failure leaves as readable text — an MCP error ends the model's turn."""
    for name, args in (
        ("agent_get", {}),  # missing required argument
        ("agent_assign", {"agent": "Jarvis", "task": "  "}),  # empty task
        ("agent_message", {"agent": "Jarvis", "text": "x", "msg_type": "ASSIGN"}),
        ("approval_resolve", {"approval_id": "nope", "approve": True}),
    ):
        answer = await agent_tools.call(name, args)
        assert isinstance(answer, str) and answer.strip()
