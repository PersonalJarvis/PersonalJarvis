"""Empirical proof that a dispatched mission surfaces on the Sub-Agents board.

Locks the post-Welle-4 wiring: ``spawn_openclaw`` dispatches a Mission, the
MissionManager publishes Phase-6 ``EventEnvelope``s on its ``MissionBus``, and
the ``SubAgentRegistry`` (bridged via :meth:`attach_mission_bus`) must translate
them into ``AgentNode``s that ``GET /api/sub-agents/tree`` serves.

This is the regression guard for the question "does a spawned sub-agent get
listed on the operations board?" — answered at the backend/REST layer, which is
the source of truth for the board snapshot. ``MissionBus.subscribe_all`` invokes
wildcard handlers directly inside ``publish`` (no queue/start step), so after an
awaited ``publish`` the node already exists.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.agents.registry import SubAgentRegistry
from jarvis.core.bus import EventBus
from jarvis.missions.event_bus import MissionBus
from jarvis.missions.events import (
    EventEnvelope,
    MissionApproved,
    MissionDispatched,
    MissionFailed,
    WorkerSpawned,
    now_ms,
)
from jarvis.missions.ids import uuid7_str


async def _settle() -> None:
    """Yield once so any TTL-removal task scheduling settles before asserts."""
    await asyncio.sleep(0)


def _openclaw_node(registry: SubAgentRegistry) -> dict:
    nodes = [n for n in registry.to_json()["all"].values() if n["kind"] == "openclaw"]
    assert len(nodes) == 1, registry.to_json()
    return nodes[0]


@pytest.mark.asyncio
async def test_dispatched_mission_appears_in_tree() -> None:
    bus = MissionBus()
    registry = SubAgentRegistry(bus=EventBus())
    registry.attach_mission_bus(bus)
    try:
        mission_id = uuid7_str()
        await bus.publish(
            EventEnvelope(
                mission_id=mission_id,
                source_actor="hauptjarvis",
                ts_ms=now_ms(),
                payload=MissionDispatched(prompt="eine schwere Cloud-Task"),
            )
        )
        await _settle()

        tree = registry.to_json()
        assert tree["count"] == 1, tree
        node = _openclaw_node(registry)
        assert node["status"] == "running"
    finally:
        registry.clear()


@pytest.mark.asyncio
async def test_worker_spawn_and_approval_update_node() -> None:
    bus = MissionBus()
    registry = SubAgentRegistry(bus=EventBus())
    registry.attach_mission_bus(bus)
    try:
        mission_id = uuid7_str()
        worker_id = uuid7_str()
        await bus.publish(
            EventEnvelope(
                mission_id=mission_id,
                source_actor="hauptjarvis",
                ts_ms=now_ms(),
                payload=MissionDispatched(prompt="x"),
            )
        )
        await bus.publish(
            EventEnvelope(
                mission_id=mission_id,
                source_actor="kontrollierer",
                ts_ms=now_ms(),
                payload=WorkerSpawned(
                    worker_id=worker_id,
                    pid=4321,
                    cli="claude",
                    model="opus-4.8",
                    worktree="/tmp/agent-x",
                ),
            )
        )
        await _settle()

        tree = registry.to_json()
        # one mission (openclaw) node + one worker (harness) node
        assert tree["count"] == 2, tree

        await bus.publish(
            EventEnvelope(
                mission_id=mission_id,
                source_actor="kontrollierer",
                ts_ms=now_ms(),
                payload=MissionApproved(
                    result_uri="file://result",
                    tokens_used=123,
                    cost_usd=0.02,
                    wall_ms=1500,
                    summary_de="fertig",
                    summary_en="done",
                ),
            )
        )
        await _settle()

        node = _openclaw_node(registry)
        assert node["status"] == "completed"
    finally:
        registry.clear()


@pytest.mark.asyncio
async def test_failed_mission_marks_node_failed() -> None:
    bus = MissionBus()
    registry = SubAgentRegistry(bus=EventBus())
    registry.attach_mission_bus(bus)
    try:
        mission_id = uuid7_str()
        await bus.publish(
            EventEnvelope(
                mission_id=mission_id,
                source_actor="hauptjarvis",
                ts_ms=now_ms(),
                payload=MissionDispatched(prompt="x"),
            )
        )
        await bus.publish(
            EventEnvelope(
                mission_id=mission_id,
                source_actor="kontrollierer",
                ts_ms=now_ms(),
                payload=MissionFailed(reason="boom", last_state="EXECUTING"),
            )
        )
        await _settle()

        node = _openclaw_node(registry)
        assert node["status"] == "failed"
        assert node["error"] == "boom"
    finally:
        registry.clear()
