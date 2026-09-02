"""Where an agent is on the island: the rule order, the memory hold, the push."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.society.checkpoints import CheckpointEngine, Facts, derive
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
