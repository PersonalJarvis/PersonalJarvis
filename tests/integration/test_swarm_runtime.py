"""End-to-end local control, scheduling, tool authorization and durable recovery."""

import asyncio
import json

import pytest

from jarvis.core.swarm_types import TaskSpec, TeamCreate
from jarvis.swarm.sandbox import WasmSandbox
from tests.fakes.swarm_runtime import runtime, terminal


def spec(name, value):
    return TeamCreate(
        name=name,
        goal=str(value),
        request_key=name,
        tasks=[
            TaskSpec(id=key, title=key, description=str(value), acceptance=f"Return value {value}")
            for key in ("one", "two")
        ],
    )


@pytest.mark.asyncio
async def test_two_swarms_execute_tools_and_retain_independent_worlds(tmp_path):
    service = runtime(tmp_path / "swarm")
    try:
        a = await service.create_team(spec("A", 41))
        b = await service.create_team(spec("B", 82))
        await asyncio.gather(service.control(a["id"], "start"), service.control(b["id"], "start"))
        finished = await asyncio.gather(terminal(service, a["id"]), terminal(service, b["id"]))
        assert [item["state"] for item in finished] == ["succeeded", "succeeded"], finished
        for team, value in ((a, 41), (b, 82)):
            tasks = await service.records(team["id"], "tasks")
            assert all(json.loads(task["result"])["value"] == value for task in tasks)
            world = await service.world(team["id"])
            assert all(agent["team_id"] == team["id"] for agent in world["agents"])
            assert int(world["team"]["tokens_used"]) > 0
            assert world["team"]["tokens_reserved"] == "0"
        assert a["lead_id"] != b["lead_id"]
    finally:
        await service.stop()
    recovered = runtime(tmp_path / "swarm")
    try:
        assert (await recovered.world(a["id"]))["team"]["lead_id"] == a["lead_id"]
        assert (await recovered.world(b["id"]))["team"]["state"] == "succeeded"
    finally:
        await recovered.stop()


@pytest.mark.asyncio
async def test_pause_resume_cancel_fence_owned_work(tmp_path):
    gate = asyncio.Event()
    service = runtime(tmp_path / "swarm", gate=gate)
    try:
        team = await service.create_team(spec("Pause", 42))
        await service.control(team["id"], "start")
        await asyncio.wait_for(service.brain_factory.started.wait(), timeout=10)
        paused = await service.control(team["id"], "pause")
        assert paused["state"] == "paused"
        gate.set()
        await asyncio.sleep(0.1)
        assert all(
            task["state"] != "succeeded" for task in await service.records(team["id"], "tasks")
        )
        await service.control(team["id"], "resume")
        assert (await terminal(service, team["id"]))["state"] == "succeeded"
        stopped = await service.create_team(spec("Stop", 43))
        await service.control(stopped["id"], "stop")
        assert (await service.team(stopped["id"]))["state"] == "canceled"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_lead_decomposes_goal_and_recovers_without_an_open_map(tmp_path):
    service = runtime(tmp_path / "swarm")
    try:
        team = await service.create_team(TeamCreate(name="Planned", goal="42", request_key="plan"))
        await service.control(team["id"], "start")
        finished = await terminal(service, team["id"])
        assert finished["state"] == "succeeded", finished
        tasks = await service.records(team["id"], "tasks")
        assert {task["id"] for task in tasks} == {
            "__swarm_plan",
            "first",
            "second",
            "__swarm_delivery",
            "__swarm_supervise_barrier_1",
            "__swarm_supervise_goal_1",
        }
        assert (await service.team(team["id"]))["lead_id"] == team["lead_id"]
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_real_bundled_wasm_executes_task_inside_full_runtime(tmp_path):
    service = runtime(tmp_path / "swarm", sandbox=WasmSandbox())
    try:
        team = await service.create_team(spec("Wasm", 42))
        await service.control(team["id"], "start")
        assert (await terminal(service, team["id"], timeout=30))["state"] == "succeeded"
        artifacts = await service.records(team["id"], "artifacts")
        assert any(item["name"] == "execution.json" for item in artifacts)
    finally:
        await service.stop()
