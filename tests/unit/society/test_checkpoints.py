"""Where an agent is on the island: the rule order, the memory hold, the push."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.society.checkpoints import CheckpointEngine, Facts, derive, family_of_tool
from jarvis.society.events import Checkpoint, MsgType, SocietyEnvelope
from jarvis.society.runtime import SocietyRuntime


def test_rule_order():
    assert derive(Facts()) is Checkpoint.IDLE
    assert derive(Facts(running=True)) is Checkpoint.DESK
    assert derive(Facts(running=True, memory_active=True)) is Checkpoint.ARCHIVE
    assert derive(Facts(memory_active=True, in_room=True)) is Checkpoint.MEETING
    assert derive(Facts(in_room=True, open_approval=True)) is Checkpoint.GATE
    assert derive(Facts(open_approval=True, paused=True)) is Checkpoint.IDLE


@pytest.fixture
async def rt(tmp_path: Path):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    await runtime.ensure_started()
    await runtime.roster.create(name="Scout")
    await runtime.roster.create(name="Archivist")
    try:
        yield runtime
    finally:
        await runtime.close()


async def test_memory_touch_walks_to_the_house_and_back(rt: SocietyRuntime, tmp_path: Path):
    now = [100.0]
    pushed: list = []
    engine = CheckpointEngine(rt, hold_s=0.2, publish=pushed.append, clock=lambda: now[0])
    rt.checkpoints.detach()
    rt.checkpoints = engine
    rt.memory._on_activity = engine.note_memory_activity  # noqa: SLF001 — rewire for the fake clock
    engine.attach()
    scout = await rt.roster.get("scout")
    await rt.memory.remember(scout, "A fact.", root=tmp_path / "vault")
    assert (await rt.roster.get("scout")).checkpoint is Checkpoint.ARCHIVE
    assert pushed[-1].agent_id == "scout" and pushed[-1].checkpoint == "archive"
    assert pushed[-1].previous == "idle"
    # The hold expires: the timer re-derives and the figure leaves.
    now[0] += 1.0
    await asyncio.sleep(0.35)
    assert (await rt.roster.get("scout")).checkpoint is Checkpoint.IDLE
    assert pushed[-1].checkpoint == "idle"
    engine.detach()


async def test_board_envelopes_move_agents(rt: SocietyRuntime):
    pushed: list = []
    rt.checkpoints._publish = pushed.append  # noqa: SLF001
    # An approval parks the agent at the gate; resolving it frees the agent.
    item = await rt.approvals.enqueue(
        agent_id="scout", trace_id="t1", capability="plugin:gmail:send", action={}, summary="send"
    )
    assert (await rt.roster.get("scout")).checkpoint is Checkpoint.GATE
    await rt.approvals.resolve(item.id, approve=False)
    assert (await rt.roster.get("scout")).checkpoint is Checkpoint.IDLE
    # A room puts its members at the table.
    room = await rt.rooms.open(members=["scout", "archivist"], topic="plan", opened_by="user")
    assert (await rt.roster.get("archivist")).checkpoint is Checkpoint.MEETING
    await rt.rooms.settle(room.room_id, reason="done")
    assert (await rt.roster.get("archivist")).checkpoint is Checkpoint.IDLE
    # A running slot is the desk; a RESULT frees it.
    rt.scheduler.note_run_started("run-1", "scout")
    await rt.store.append_and_publish(
        SocietyEnvelope(
            msg_type=MsgType.CLAIM, from_agent="scout", to_agent="user", trace_id="t2", payload={}
        )
    )
    assert (await rt.roster.get("scout")).checkpoint is Checkpoint.DESK
    rt.scheduler.note_run_ended("run-1")
    await rt.store.append_and_publish(
        SocietyEnvelope(
            msg_type=MsgType.RESULT, from_agent="scout", to_agent="user", trace_id="t2", payload={}
        )
    )
    assert (await rt.roster.get("scout")).checkpoint is Checkpoint.IDLE
    by_agent = {}
    for p in pushed:
        by_agent.setdefault(p.agent_id, []).append(p.checkpoint)
    assert by_agent["scout"] == ["gate", "idle", "meeting", "idle", "desk", "idle"]
    assert by_agent["archivist"] == ["meeting", "idle"]


async def test_paused_agent_stays_home(rt: SocietyRuntime):
    await rt.roster.update("scout", {"state": "paused"})
    await rt.approvals.enqueue(
        agent_id="scout", trace_id="t1", capability="x", action={}, summary="x"
    )
    assert (await rt.roster.get("scout")).checkpoint is Checkpoint.IDLE
    assert await rt.checkpoints.refresh("ghost") is None


# ------------------------------------------------------------- the hub shops


def test_running_agents_stand_at_the_shop_of_their_tools():
    assert derive(Facts(running=True, family="plugin")) is Checkpoint.HUB_PLUGINS
    assert derive(Facts(running=True, family="skill")) is Checkpoint.HUB_SKILLS
    assert derive(Facts(running=True, family="mcp")) is Checkpoint.HUB_MCP
    assert derive(Facts(running=True, family="cli")) is Checkpoint.HUB_CLI
    assert derive(Facts(running=True, family="core")) is Checkpoint.DESK
    # A CLI seat sits in the Cantina until its calls say otherwise; memory outranks the shop.
    assert derive(Facts(running=True, cli_seat=True)) is Checkpoint.HUB_CLI
    assert derive(Facts(running=True, cli_seat=True, family="cli")) is Checkpoint.HUB_CLI
    assert derive(Facts(running=True, cli_seat=True, family="plugin")) is Checkpoint.HUB_PLUGINS
    assert derive(Facts(running=True, family="plugin", memory_active=True)) is Checkpoint.ARCHIVE
    # Nobody stands in a shop without a turn.
    assert derive(Facts(family="plugin")) is Checkpoint.IDLE


def test_family_of_tool():
    assert family_of_tool("gmail") == "plugin"
    assert family_of_tool("cli_claude") == "cli"
    assert family_of_tool("github/search_issues") == "mcp"
    assert family_of_tool("society_run_skill") == "skill"
    assert family_of_tool("society_shell") == "core"
    assert family_of_tool("society_browser") == "core"
    assert family_of_tool("spawn-worker") == "core"
    # A coding CLI: native tools are CLI work, Jarvis' MCP server hands out the plugins.
    assert family_of_tool("Bash", cli_seat=True) == "cli"
    assert family_of_tool("Read", cli_seat=True) == "cli"
    assert family_of_tool("mcp__jarvis__gmail", cli_seat=True) == "plugin"
    assert family_of_tool("mcp__jarvis__society_run_skill", cli_seat=True) == "skill"
    assert family_of_tool("mcp__github__search_issues", cli_seat=True) == "mcp"
    assert family_of_tool("mcp__github__search_issues") == "mcp"


class FakeChatService:
    """The slice of the agent-chat service the engine touches."""

    def __init__(self) -> None:
        self.queues: dict[str, list[asyncio.Queue]] = {}
        self.running: set[str] = set()

    def is_running(self, session_id: str) -> bool:
        return session_id in self.running

    def subscribe(self, session_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.queues.setdefault(session_id, []).append(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue) -> None:
        self.queues.get(session_id, []).remove(q)

    def emit(self, session_id: str, kind: str, **payload) -> None:
        for q in self.queues.get(session_id, []):
            q.put_nowait({"kind": kind, "payload": payload})


async def _settle() -> None:
    for _ in range(6):
        await asyncio.sleep(0.01)


async def test_a_typed_turn_walks_to_the_docks_and_back(rt: SocietyRuntime):
    """A message typed into the card never passes the scheduler — the turn watcher
    still sends the figure to the shop of the tools it uses."""
    svc = FakeChatService()
    rt._get_chat = lambda: svc  # noqa: SLF001
    pushed: list = []
    rt.checkpoints._publish = pushed.append  # noqa: SLF001
    session = "society:scout"
    svc.running.add(session)
    rt.checkpoints.note_turn_started("scout", session)
    await _settle()
    assert (await rt.roster.get("scout")).checkpoint is Checkpoint.DESK
    assert rt.checkpoints.is_busy("scout")
    svc.emit(session, "tool_call", name="gmail", input={})
    await _settle()
    assert (await rt.roster.get("scout")).checkpoint is Checkpoint.HUB_PLUGINS
    # A second watcher for the same agent is not started while one runs.
    rt.checkpoints.note_turn_started("scout", session)
    assert len(svc.queues[session]) == 1
    svc.running.discard(session)
    svc.emit(session, "turn_finished", status="ok")
    await _settle()
    assert (await rt.roster.get("scout")).checkpoint is Checkpoint.IDLE
    assert svc.queues[session] == []
    assert [p.checkpoint for p in pushed] == ["desk", "hub:plugins", "idle"]


async def test_family_hysteresis(rt: SocietyRuntime):
    """The first family wins at once; a different one must dominate 20 s before the
    figure changes shops, and the timer makes the switch without another call."""
    now = [1000.0]
    engine = CheckpointEngine(rt, publish=lambda _e: None, clock=lambda: now[0])
    rt.checkpoints.detach()
    rt.checkpoints = engine
    engine.attach()
    rt.scheduler.note_run_started("run-1", "scout")
    await engine.note_tool_call("scout", "gmail")
    assert (await rt.roster.get("scout")).checkpoint is Checkpoint.HUB_PLUGINS
    for _ in range(3):
        await engine.note_tool_call("scout", "github/list_prs")
    # MCP dominates the window (3 of 4) but has not for 20 s: still at the Docks.
    assert (await rt.roster.get("scout")).checkpoint is Checkpoint.HUB_PLUGINS
    now[0] += 21.0
    await engine.note_tool_call("scout", "github/list_prs")
    assert (await rt.roster.get("scout")).checkpoint is Checkpoint.HUB_MCP
    # The run ends: the window is cleared and the figure goes home.
    engine.clear_tool_calls("scout")
    rt.scheduler.note_run_ended("run-1")
    assert await engine.refresh("scout") is Checkpoint.IDLE
    assert engine.family_for("scout") is None
    engine.detach()
