"""Emergency stop, whole-attempt deadlines and active lead control regressions."""

import asyncio
import json
import threading
from decimal import Decimal

import pytest

from jarvis.brain.swarm_factory import SwarmProvider
from jarvis.control.cancel import KillSwitch
from jarvis.core.bus import EventBus
from jarvis.core.events import KillRequested
from jarvis.core.protocols import BrainDelta
from jarvis.core.swarm_types import BudgetLimits, TaskSpec, TeamCreate
from tests.fakes.swarm_runtime import SyntheticBrain, SyntheticFactory, runtime, terminal


def one_task(name, *, runtime_seconds=30, concurrency=2):
    return TeamCreate(
        name=name,
        goal="42",
        request_key=name,
        limits=BudgetLimits(runtime_seconds=runtime_seconds, concurrency=concurrency),
        tasks=[TaskSpec(id="work", title="Compute", description="42", acceptance="Return 42")],
    )


@pytest.mark.asyncio
async def test_process_kill_fences_all_teams_and_prevents_redispatch(tmp_path):
    gate = asyncio.Event()
    service = runtime(tmp_path, gate=gate)
    switch = KillSwitch()
    bus = EventBus()
    switch.bind(bus)
    service._kill_switch = switch
    try:
        teams = [await service.create_team(one_task(name)) for name in ("first", "second")]
        for team in teams:
            await service.control(team["id"], "start")
        await asyncio.wait_for(service.brain_factory.started.wait(), 5)
        await bus.publish(KillRequested(source="tray"))
        assert service._is_suspended()
        async with asyncio.timeout(5):
            while True:
                states = [(await service.team(team["id"]))["state"] for team in teams]
                if states == ["paused", "paused"] and not service._workers:
                    break
                await asyncio.sleep(0.01)
        gate.set()
        await service._tick()
        assert not service._workers
        assert all(token.is_cancelled() for token, _ in switch.active_tokens())
        await service.control(teams[0]["id"], "resume")
        assert (await terminal(service, teams[0]["id"]))["state"] == "succeeded"
        assert (await service.team(teams[1]["id"]))["state"] == "paused"
    finally:
        await service.stop()
    assert not list(switch.active_tokens())


class RemoteSettings:
    def load(self):
        return {"enabled": True, "generation": "replacement", "max_concurrency": 25}

    def resolved(self):
        return None, None


class RemoteCandidate:
    def __init__(self, fail=False):
        self.fail = fail
        self.closed = False

    def provision(self):
        if self.fail:
            raise OSError("Transient database failure")

    def close(self):
        self.closed = True

    def check_connection(self):
        return {"postgresql": True, "redis": True, "s3": True}


@pytest.mark.asyncio
async def test_failed_remote_replacement_detaches_old_backend_and_retries(tmp_path, monkeypatch):
    from jarvis.swarm import distributed

    service = runtime(tmp_path)
    old = RemoteCandidate()
    candidates = []

    def create(config, secrets):
        candidate = RemoteCandidate(fail=not candidates)
        candidates.append(candidate)
        return candidate

    monkeypatch.setattr(distributed, "create_distributed_registry", create)
    service.registry.set_remote(old)
    service.settings = RemoteSettings()
    service._settings_generation = "previous"
    service._remote_capacity = 10
    try:
        service._refresh_distributed()
        assert service.registry.remote is None
        assert service._remote_capacity == 0
        assert candidates[0].closed
        assert service._settings_generation == "previous"
        service._refresh_distributed()
        assert len(candidates) == 1
        service._remote_retry_at = 0
        service._refresh_distributed()
        assert len(candidates) == 2
        assert service.registry.remote is candidates[1]
        assert service._remote_capacity == 25
        assert service._settings_generation == "replacement"
        assert not service._distributed_error
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_corrupt_team_does_not_block_healthy_team_or_pagination(tmp_path):
    service = runtime(tmp_path)
    try:
        good = await service.create_team(one_task("healthy"))
        damaged = await service.create_team(one_task("damaged"))
        store = service.registry.open(damaged["id"])
        store.path.write_bytes(b"Corrupt synthetic team database")
        first = await service.list_teams(limit=1)
        second = await service.list_teams(limit=1, offset=1)
        assert first[0]["id"] == damaged["id"]
        assert first[0]["available"] is False
        assert "state" not in first[0]
        assert second[0]["id"] == good["id"]
        await service.control(good["id"], "start")
        assert (await terminal(service, good["id"]))["state"] == "succeeded"
    finally:
        await service.stop()


class SlowSandbox:
    def __init__(self):
        self.started = asyncio.Event()
        self.canceled = asyncio.Event()
        self.cancel_probe = None

    async def run(self, script, inputs, *, timeout_s=10, cancel=None):
        self.cancel_probe = cancel
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.canceled.set()


@pytest.mark.asyncio
async def test_team_deadline_cancels_slow_tool_and_denies_result(tmp_path):
    sandbox = SlowSandbox()
    service = runtime(tmp_path, sandbox=sandbox)
    try:
        team = await service.create_team(one_task("deadline", runtime_seconds=1))
        await service.control(team["id"], "start")
        await asyncio.wait_for(sandbox.started.wait(), 5)
        final = await terminal(service, team["id"], timeout=5)
        await asyncio.wait_for(sandbox.canceled.wait(), 5)
        assert final["state"] == "blocked"
        assert "runtime" in final["reason"].lower()
        assert sandbox.cancel_probe()
        assert not any(
            t["state"] == "succeeded" for t in await service.records(team["id"], "tasks")
        )
    finally:
        await service.stop()


class SupervisingBrain(SyntheticBrain):
    async def complete(self, request):
        payload = json.loads(request.messages[0].content)
        task = payload.get("task", {})
        if not task.get("id", "").startswith("__swarm_supervise_"):
            async for delta in super().complete(request):
                yield delta
            return
        self.factory.supervising.set()
        if request.messages[-1].role == "tool":
            assert json.loads(request.messages[-1].content)["success"]
            yield BrainDelta(content="Paused the worker pending reconciliation.")
            yield BrainDelta(finish_reason="stop", usage={"input_tokens": 20, "output_tokens": 10})
            return
        assert "request_control" in {tool["name"] for tool in request.tools}
        events = json.loads(task["description"].rsplit("\n", 1)[1])
        target = next(event["agent_id"] for event in events if event["kind"] == "task.claimed")
        yield BrainDelta(
            tool_call={
                "id": "pause",
                "name": "request_control",
                "input": {
                    "operation": "pause_worker",
                    "payload": {
                        "agent_id": target,
                        "reason": "Inspect a reported conflict before continuing",
                    },
                },
            }
        )
        yield BrainDelta(
            finish_reason="tool_calls", usage={"input_tokens": 20, "output_tokens": 10}
        )


class SupervisingFactory(SyntheticFactory):
    def __init__(self):
        super().__init__(asyncio.Event())
        self.supervising = asyncio.Event()

    async def create(self):
        return SwarmProvider(SupervisingBrain(self), "synthetic", "synthetic", Decimal("1"))


@pytest.mark.asyncio
async def test_lead_can_pause_held_worker_without_awarding_coordination_xp(tmp_path):
    service = runtime(tmp_path)
    service.brain_factory = SupervisingFactory()
    try:
        spec = one_task("supervision")
        spec.tasks[0].priority = 9
        spec.tasks.append(
            TaskSpec(
                id="zz-waiting",
                title="Next work",
                description="42",
                acceptance="Return 42",
                priority=9,
            )
        )
        team = await service.create_team(spec)
        await service.control(team["id"], "start")
        await asyncio.wait_for(service.brain_factory.started.wait(), 5)
        await asyncio.wait_for(service.brain_factory.supervising.wait(), 10)
        async with asyncio.timeout(5):
            while True:
                agents = await service.records(team["id"], "agents")
                if any(
                    agent["role"] == "worker" and agent["state"] == "waiting" for agent in agents
                ):
                    break
                await asyncio.sleep(0.01)
        assert not service.brain_factory.gate.is_set()
        tasks = await service.records(team["id"], "tasks")
        assert next(t for t in tasks if t["id"] == "work")["state"] == "blocked"
        assert not await service.records(team["id"], "reputation")
        assert not await service.records(team["id"], "verifications")
        assert int((await service.team(team["id"]))["tokens_used"]) > 0
        assert len(service._workers) <= 2
    finally:
        await service.stop()


class UnavailableCatalog:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def list(self, **kwargs):
        self.started.set()
        self.release.wait(10)
        return []

    def close(self):
        self.release.set()


@pytest.mark.asyncio
async def test_slow_remote_catalog_does_not_delay_local_dispatch(tmp_path):
    service = runtime(tmp_path)
    remote = UnavailableCatalog()
    service._settings_generation = ""
    service._remote_capacity = 1
    service.registry.set_remote(remote)
    try:
        team = await service.create_team(one_task("independent-local"))
        assert await asyncio.to_thread(remote.started.wait, 3)
        await service.control(team["id"], "start")
        assert (await terminal(service, team["id"], timeout=5))["state"] == "succeeded"
        assert not remote.release.is_set()
    finally:
        remote.release.set()
        await service.stop()
