"""Actual WASM rechecks, attribution, duplicate penalties and unknown fault handling."""

import asyncio
import threading
from dataclasses import replace

import pytest

from jarvis.control.cancel import CancelToken
from jarvis.core.swarm_types import TaskSpec
from jarvis.swarm.recheck import Rechecks, _begin, _finish
from jarvis.swarm.sandbox import WasmSandbox
from jarvis.swarm.store import SwarmConflictError
from jarvis.swarm.tools import SwarmToolkit
from tests.fakes.swarm_runtime import runtime
from tests.fakes.swarm_storage import accepted_result, running_team


async def accepted_fixture(tmp_path):
    original = TaskSpec(
        id="work",
        title="Arithmetic",
        description="Compute seven squared",
        acceptance="Return 49 after executing the calculation",
        domain="math",
        difficulty=3,
        verification="javascript",
        verification_script="function main(input) { return {accepted: input.result === 49}; }",
    )
    fixture = running_team(tmp_path, tasks=[original])
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    service = runtime(tmp_path, sandbox=WasmSandbox())
    cancel = CancelToken()
    toolkit = SwarmToolkit(service, fixture.store, actor, fixture.controller, cancel)
    computation = await toolkit.execute(
        "run_javascript",
        {
            "script": "function main(input) { return input.n ** 2; }",
            "inputs": {"n": 7},
        },
        "computation",
    )
    assert computation.success
    evidence = list(toolkit.evidence)
    task = fixture.store.read_task(actor, actor.task_id)
    verdict = await service._verify(
        fixture.store, fixture.controller, actor, task, "49", evidence, cancel, []
    )
    assert verdict["accepted"]
    fixture.store.finish(actor, "49", evidence, verdict, controller=fixture.controller)
    return fixture, service


@pytest.mark.asyncio
async def test_real_original_proof_recheck_is_persistent_and_grants_no_xp(tmp_path):
    fixture, service = await accepted_fixture(tmp_path)
    try:
        before = fixture.store.get_record("agents", fixture.member.agent_id)
        checked = await Rechecks.recheck(service, fixture.team["id"], "work", "check-once")
        assert checked["state"] == "passed", checked
        assert checked["rating_id"] is None
        assert await Rechecks.recheck(service, fixture.team["id"], "work", "check-once") == checked
        after = fixture.store.get_record("agents", fixture.member.agent_id)
        assert (after["level"], after["verified_tasks"]) == (
            before["level"],
            before["verified_tasks"],
        )
        skill = fixture.store.skill_profiles(fixture.member.agent_id)["profiles"][0]
        assert skill["acceptance"] == {"numerator": "1", "denominator": "1", "rate": 1.0}
        assert skill["regression"] == {"numerator": "0", "denominator": "1", "rate": 0.0}
        service.sandbox = ChangedReplay("regression")
        later = await Rechecks.recheck(service, fixture.team["id"], "work", "later-check")
        assert later["state"] == "regression"
        assert later["id"] != checked["id"]
        repeated = await Rechecks.recheck(service, fixture.team["id"], "work", "repeated-check")
        assert repeated["rating_id"] == later["rating_id"]
        assert (
            sum(
                item["reason"] == "regression"
                for item in fixture.store.skill_profiles(fixture.member.agent_id)["history"]
            )
            == 1
        )
    finally:
        await service.stop()
    recovered = runtime(tmp_path, sandbox=WasmSandbox())
    try:
        assert (
            await Rechecks.recheck(recovered, fixture.team["id"], "work", "check-once") == checked
        )
    finally:
        await recovered.stop()


class ChangedReplay:
    def __init__(self, kind):
        self.actual = WasmSandbox()
        self.kind = kind
        self.calls = 0

    async def run(self, script, inputs, **kwargs):
        self.calls += 1
        output = await self.actual.run(script, inputs, **kwargs)
        if self.kind == "runtime_failure":
            return replace(output, exit_code=1, stderr="Runtime unavailable")
        if "input.n ** 2" in script:
            value = 50 if self.kind == "regression" else 49 + self.calls
            return replace(output, output=value)
        return output


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["regression", "noisy", "runtime_failure", "corruption"])
async def test_only_reproducible_regression_penalizes_once(tmp_path, kind):
    fixture, service = await accepted_fixture(tmp_path)
    try:
        before = fixture.store.get_record("agents", fixture.member.agent_id)
        if kind == "corruption":
            task = fixture.store.get_record("tasks", "work")
            metadata, _ = fixture.store.download_artifact(task["evidence"][0])
            (fixture.store.path.parent / "objects" / metadata["sha256"]).write_bytes(b"corrupt")
        else:
            service.sandbox = ChangedReplay(kind)
        checked = await Rechecks.recheck(service, fixture.team["id"], "work", "check-once")
        assert checked["state"] == ("regression" if kind == "regression" else "inconclusive")
        assert await Rechecks.recheck(service, fixture.team["id"], "work", "check-once") == checked
        after = fixture.store.get_record("agents", fixture.member.agent_id)
        assert after["verified_tasks"] == before["verified_tasks"]
        assert (after["reliability"] < before["reliability"]) is (kind == "regression")
        skill = fixture.store.skill_profiles(fixture.member.agent_id)["profiles"][0]
        assert skill["acceptance"]["denominator"] == "1"
        assert skill["regression"]["denominator"] == ("1" if kind == "regression" else "0")
        if kind == "regression":
            history = fixture.store.skill_profiles(fixture.member.agent_id)["history"]
            assert sum(row["reason"] == "regression" for row in history) == 1
            assert any(row["id"] == checked["rating_id"] for row in history)
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_review_only_acceptance_requires_new_review_budget(tmp_path):
    fixture = running_team(tmp_path)
    actor = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    accepted_result(fixture, actor)
    service = runtime(tmp_path)
    try:
        checked = await Rechecks.recheck(service, fixture.team["id"], "work", "check-once")
        assert checked["state"] == "unsupported"
        assert "budget" in checked["reason"]
        assert checked["rating_id"] is None
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_recheck_tool_executor_inspects_original_script_and_preserves_blacklist(tmp_path):
    from jarvis.core.bus import EventBus
    from jarvis.core.config import SafetyConfig
    from jarvis.safety.approval import ApprovalWorkflow
    from jarvis.safety.risk_tier import RiskTierEvaluator
    from jarvis.safety.tool_executor import ToolExecutor

    fixture, service = await accepted_fixture(tmp_path)
    try:
        bus = EventBus()
        policy = SafetyConfig()
        policy.blacklist.commands = ["*input.n*"]
        service.tool_executor = ToolExecutor(bus, RiskTierEvaluator(policy), ApprovalWorkflow(bus))
        replay = ChangedReplay("regression")
        service.sandbox = replay
        checked = await Rechecks.recheck(service, fixture.team["id"], "work", "check-once")
        assert checked["state"] == "inconclusive"
        assert checked["rating_id"] is None
        assert replay.calls == 0
        service._emergency_fence.trip("test-stop")
        from jarvis.swarm.store import SwarmConflictError

        with pytest.raises(SwarmConflictError):
            await Rechecks.recheck(service, fixture.team["id"], "work", "check-once")
    finally:
        await service.stop()


class GatedReplay:
    def __init__(self, actual):
        self.actual = actual
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.canceled = False

    async def run(self, script, inputs, **kwargs):
        self.entered.set()
        try:
            await self.release.wait()
            output = await self.actual.run(script, inputs, **kwargs)
            return replace(output, output=50) if "input.n ** 2" in script else output
        except asyncio.CancelledError:
            self.canceled = True
            raise


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["stop", "team-stop", "restore", "delete"])
async def test_recheck_is_joined_before_shutdown_or_storage_replacement(tmp_path, action):
    fixture, service = await accepted_fixture(tmp_path)
    replay = GatedReplay(service.sandbox)
    service.sandbox = replay
    job = asyncio.create_task(service.recheck(fixture.team["id"], "work", "lifecycle"))
    try:
        await asyncio.wait_for(replay.entered.wait(), 10)
        if action == "stop":
            await service.stop()
        elif action == "team-stop":
            await service.control(fixture.team["id"], "stop")
        elif action == "restore":
            backup = await service.backup(fixture.team["id"], "live-recheck-backup")
            path = await service.backup_file(fixture.team["id"], backup["backup_id"])
            await service.restore_backup(path, "replace-recheck", fixture.team["id"])
        else:
            await service.delete_team(fixture.team["id"], "delete-recheck")
        assert job.done() and job.cancelled()
        assert replay.canceled
        assert not service._rechecks_active
        replay.release.set()
        if action != "delete":
            current = service.registry.open(fixture.team["id"])
            history = current.skill_profiles(fixture.member.agent_id)["history"]
            assert not any(row["reason"] == "regression" for row in history)
    finally:
        replay.release.set()
        if not job.done():
            job.cancel()
        await asyncio.gather(job, return_exceptions=True)
        await service.stop()


@pytest.mark.asyncio
async def test_recheck_finish_rejects_a_copied_running_record_in_another_generation(tmp_path):
    fixture, service = await accepted_fixture(tmp_path)
    try:
        record, _ = _begin(fixture.store, "work", "cross-process")
        backup = await service.backup(fixture.team["id"], "cross-process-backup")
        path = await service.backup_file(fixture.team["id"], backup["backup_id"])
        await service.restore_backup(path, "replace-cross-process", fixture.team["id"])
        current = service.registry.open(fixture.team["id"])
        assert current.get()["storage_generation"] != record["storage_generation"]
        with pytest.raises(SwarmConflictError, match="storage changed"):
            _finish(current, record, "regression", "A stale process result", [])
        assert not any(
            row["reason"] == "regression"
            for row in current.skill_profiles(fixture.member.agent_id)["history"]
        )
        # A restored in-flight lease must not stop the owner from checking again.
        resumed, task = _begin(current, "work", "cross-process")
        assert task is not None
        assert resumed["run_id"] != record["run_id"]
        assert resumed["storage_generation"] == current.get()["storage_generation"]
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_service_shutdown_is_shared_idempotent_and_survives_a_canceled_waiter(tmp_path):
    service = runtime(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    class GatedEgress:
        calls = 0

        async def aclose(self):
            self.calls += 1
            entered.set()
            await release.wait()

    egress = GatedEgress()
    service.egress = egress
    first = asyncio.create_task(service.stop())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(service.stop())
        release.set()
        await asyncio.wait_for(second, 10)
        await service.stop()
        assert egress.calls == 1
        with pytest.raises(SwarmConflictError, match="stopped Swarm service"):
            await service.start()
    finally:
        release.set()
        await service.stop()


@pytest.mark.asyncio
async def test_repeated_cancel_joins_recheck_creation_before_shutdown(tmp_path, monkeypatch):
    from jarvis.swarm import recheck as module

    fixture, service = await accepted_fixture(tmp_path)
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    actual_begin = module._begin

    def delayed_begin(*args):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(10), "The test did not release its durable operation"
        return actual_begin(*args)

    monkeypatch.setattr(module, "_begin", delayed_begin)
    job = asyncio.create_task(service.recheck(fixture.team["id"], "work", "cancel-creation"))
    shutdown = None
    try:
        await asyncio.wait_for(entered.wait(), 10)
        job.cancel()
        await asyncio.sleep(0)
        job.cancel()
        shutdown = asyncio.create_task(service.stop())
        await asyncio.sleep(0.02)
        assert not job.done()
        assert not shutdown.done()
        release.set()
        await asyncio.wait_for(shutdown, 10)
        assert job.cancelled()
        records = fixture.store.records("decisions")
        rechecks = [row for row in records if row.get("kind") == "contribution_recheck"]
        assert len(rechecks) == 1
        assert rechecks[0]["state"] == "inconclusive"
    finally:
        release.set()
        await asyncio.gather(job, return_exceptions=True)
        if shutdown is not None:
            await shutdown
        await service.stop()
