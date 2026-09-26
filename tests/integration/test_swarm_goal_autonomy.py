"""Goal-only planning expands useful batches without opening final delivery early."""

import asyncio
import json
from decimal import Decimal

import pytest

from jarvis.brain.swarm_factory import SwarmProvider
from jarvis.core.protocols import BrainDelta
from jarvis.core.swarm_types import BudgetLimits, TaskSpec, TeamCreate
from jarvis.swarm.autonomy import BARRIER, DELIVERY, GATE, PLAN, close_barrier, install
from jarvis.swarm.control_requests import acknowledge, drain, enqueue
from jarvis.swarm.store import SwarmAccessError, SwarmConflictError
from tests.fakes.swarm_runtime import SyntheticBrain, SyntheticFactory, runtime, terminal
from tests.fakes.swarm_storage import accepted_result, running_team


def work(task_id, *, domain="math", group="calculation"):
    return TaskSpec(
        id=task_id,
        title=task_id,
        description="42",
        acceptance="Return value 42",
        domain=domain,
        milestone=group,
    ).model_dump(mode="json")


class StagedFactory(SyntheticFactory):
    def __init__(self, *, wide=False, pause_stage=None, gate=None, inspect_evidence=False):
        super().__init__(gate)
        self.wide = wide
        self.pause_stage = pause_stage
        self.review_entered = asyncio.Event()
        self.release_review = asyncio.Event()
        self.frontiers = []
        self.inspect_evidence = inspect_evidence
        self.inspected = []
        self.offered_tools = []

    async def create(self):
        factory = self

        class Brain(SyntheticBrain):
            async def complete(self, request):
                if "persistent team lead" in request.system:
                    value = {
                        "tasks": [
                            work(f"first-{i}", domain="research", group="discover")
                            for i in range(8 if factory.wide else 1)
                        ],
                        "remaining_decomposition": factory.wide,
                    }
                elif "persistent goal planner" in request.system:
                    factory.offered_tools.append(tuple(tool["name"] for tool in request.tools))
                    prompt = json.loads(request.messages[0].content)
                    factory.frontiers.append(prompt)
                    if prompt["stage"] == factory.pause_stage:
                        factory.review_entered.set()
                        await factory.release_review.wait()
                    if prompt["stage"] == 1:
                        value = {
                            "decision": "continue",
                            "tasks": [
                                work(f"second-{i}") for i in range(32 if factory.wide else 1)
                            ],
                            "summary": "The first approach is recorded; calculate another way.",
                            "reason": "Complete the original calculation with another approach.",
                            "remaining_decomposition": False,
                        }
                    else:
                        assert prompt["unfinished_tasks"] == 0
                        if factory.inspect_evidence:
                            assert {tool["name"] for tool in request.tools} == {
                                "read_artifact",
                                "search_team",
                            }
                            if len(request.messages) == 1:
                                source = next(
                                    item
                                    for item in prompt["recent_tasks"]
                                    if item["id"] == "first-0"
                                )
                                yield BrainDelta(
                                    tool_call={
                                        "id": "inspect-old-proof",
                                        "name": "read_artifact",
                                        "input": {"artifact_id": source["evidence"][0]},
                                        "thought_signature": "fixture-signature",
                                    },
                                    finish_reason="tool_calls",
                                    usage={"input_tokens": 10, "output_tokens": 10},
                                )
                                return
                            read = json.loads(request.messages[-1].content)
                            assert read["success"]
                            assert (
                                request.messages[-2].content[0]["thought_signature"]
                                == "fixture-signature"
                            )
                            factory.inspected.append(read["output"]["task_id"])
                        value = {
                            "decision": "deliver",
                            "tasks": [],
                            "summary": "Both approaches are accepted.",
                            "reason": "The original calculation is complete.",
                            "remaining_decomposition": False,
                        }
                else:
                    async for delta in super().complete(request):
                        yield delta
                    return
                yield BrainDelta(
                    content=json.dumps(value),
                    finish_reason="stop",
                    usage={"input_tokens": 10, "output_tokens": 10},
                )

        return SwarmProvider(Brain(self), "synthetic", "synthetic", Decimal("1"))


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 3])
async def test_one_goal_completes_two_stages_before_delivery_without_admin_xp(
    tmp_path, concurrency
):
    service = runtime(tmp_path)
    factory = StagedFactory()
    service.brain_factory = factory
    try:
        team = await service.create_team(
            TeamCreate(
                name="Staged goal",
                goal="42",
                request_key="goal",
                limits=BudgetLimits(concurrency=concurrency),
            )
        )
        await service.control(team["id"], "start")
        finished = await terminal(service, team["id"], timeout=40)
        assert finished["state"] == "succeeded", finished
        tasks = await service.records(team["id"], "tasks")
        assert {item["id"] for item in tasks if not item["id"].startswith("__swarm_")} == {
            "first-0",
            "second-0",
        }
        assert [item["stage"] for item in factory.frontiers] == [1, 2]
        assert all(item["goal"] == item["acceptance"] == "42" for item in factory.frontiers)
        assert all(item["unfinished_tasks"] == 0 for item in factory.frontiers)
        lead = await service.record(team["id"], "agents", team["lead_id"])
        assert int(lead["verified_tasks"]) == 1  # Only the independently verified final delivery.
        assert finished["checkpoint"]["autonomy"]["decision"] == "deliver"
        assert (await service.record(team["id"], "tasks", DELIVERY))["dependencies"] == [
            BARRIER + "2",
            GATE + "2",
        ]
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_more_decomposition_expands_beyond_32_tasks_while_workers_are_running(tmp_path):
    gate = asyncio.Event()
    factory = StagedFactory(wide=True, gate=gate)
    service = runtime(tmp_path)
    service.brain_factory = factory
    try:
        team = await service.create_team(
            TeamCreate(
                name="Wide goal",
                goal="42",
                request_key="wide",
                limits=BudgetLimits(
                    concurrency=32,
                    worker_limit="1000",
                    token_budget="10000000",  # noqa: S106 - authorized quantity, not a credential
                    max_output_tokens=1024,
                ),
            )
        )
        await service.control(team["id"], "start")
        async with asyncio.timeout(20):
            while True:
                tasks = await service.records(team["id"], "tasks", limit=100)
                ordinary = [item for item in tasks if not item["id"].startswith("__swarm_")]
                if len(ordinary) == 40:
                    break
                await asyncio.sleep(0.05)
        assert factory.frontiers[0]["unfinished_tasks"] > 0
        assert len(factory.frontiers) == 1
        assert (await service.record(team["id"], "tasks", DELIVERY))["state"] == "ready"
        assert len(service._workers) <= 32
        gate.set()
        assert (await terminal(service, team["id"], timeout=60))["state"] == "succeeded"
        assert factory.frontiers[-1]["unfinished_tasks"] == 0
    finally:
        gate.set()
        await service.stop()


@pytest.mark.asyncio
async def test_pause_restart_replays_stages_without_duplicate_work(tmp_path):
    service = runtime(tmp_path)
    factory = StagedFactory(pause_stage=2)
    service.brain_factory = factory
    team = await service.create_team(
        TeamCreate(name="Recover staged goal", goal="42", request_key="recover")
    )
    try:
        await service.control(team["id"], "start")
        await asyncio.wait_for(factory.review_entered.wait(), 30)
        await service.control(team["id"], "pause")
        await service.stop()
    finally:
        factory.release_review.set()
        await service.stop()
    recovered = runtime(tmp_path)
    second_factory = StagedFactory()
    recovered.brain_factory = second_factory
    try:
        await recovered.control(team["id"], "resume")
        assert (await terminal(recovered, team["id"], timeout=40))["state"] == "succeeded"
        tasks = await recovered.records(team["id"], "tasks")
        assert {item["id"] for item in tasks if not item["id"].startswith("__swarm_")} == {
            "first-0",
            "second-0",
        }
        assert [item["stage"] for item in second_factory.frontiers] == [2]
    finally:
        await recovered.stop()


def planning_fixture(tmp_path):
    fixture = running_team(
        tmp_path,
        tasks=[
            TaskSpec(id=PLAN, title="Plan", description="Plan the goal", acceptance="A valid plan")
        ],
    )
    actor = fixture.store.claim(fixture.controller, fixture.team["lead_id"], PLAN)
    artifact = fixture.store.capture_result(fixture.controller, actor, "plan.json", "{}", "plan")
    install(
        fixture.store,
        fixture.controller,
        actor,
        [TaskSpec.model_validate(work("first"))],
        True,
        artifact["id"],
    )
    actor = fixture.store.claim(fixture.controller, fixture.team["lead_id"], GATE + "1")
    return fixture, actor


@pytest.mark.asyncio
async def test_goal_stage_retrieves_older_accepted_proof_through_scoped_tools(tmp_path):
    service = runtime(tmp_path)
    factory = StagedFactory(inspect_evidence=True)
    service.brain_factory = factory
    try:
        team = await service.create_team(
            TeamCreate(name="Inspect old finding", goal="42", request_key="inspect")
        )
        await service.control(team["id"], "start")
        assert (await terminal(service, team["id"], timeout=40))["state"] == "succeeded"
        assert factory.inspected == ["first-0"]
    finally:
        await service.stop()


def proposal(actor, decision="continue", **changes):
    return {
        "stage_task_id": actor.task_id,
        "decision": decision,
        "tasks": [work("second")] if decision == "continue" else [],
        "summary": "Continue the original goal",
        "reason": "Complete its remaining calculation",
        "remaining_decomposition": False,
        **changes,
    }


def test_stage_request_replay_is_atomic_and_preserves_final_delivery_fence(tmp_path):
    fixture, actor = planning_fixture(tmp_path)
    request = enqueue(fixture.store, actor, "advance_goal", proposal(actor), "stage-once")
    first = drain(fixture.store, fixture.controller)[0]
    second = drain(fixture.store, fixture.controller)[0]
    assert first == second
    assert first["state"] == "applied"
    assert first["id"] == request["id"]
    assert fixture.store.get_record("tasks", DELIVERY)["dependencies"] == [
        BARRIER + "2",
        GATE + "2",
    ]
    acknowledge(fixture.store, fixture.controller, request["id"])
    assert drain(fixture.store, fixture.controller) == []
    with pytest.raises(SwarmConflictError):
        fixture.store.claim(fixture.controller, fixture.team["lead_id"], DELIVERY)


def test_old_stage_request_cannot_complete_a_reclaimed_attempt(tmp_path):
    fixture, actor = planning_fixture(tmp_path)
    enqueue(fixture.store, actor, "advance_goal", proposal(actor), "old-attempt")
    fixture.store.fail(actor, "Retry the planning attempt", controller=fixture.controller)
    replacement = fixture.store.claim(fixture.controller, actor.agent_id, actor.task_id)
    assert replacement.task_fence > actor.task_fence
    assert drain(fixture.store, fixture.controller)[0]["state"] == "rejected"
    assert fixture.store.read_task(replacement, replacement.task_id)["state"] == "running"
    assert not any(task["id"] == "second" for task in fixture.store.records("tasks"))


def test_late_reconciliation_cannot_change_or_bypass_final_delivery(tmp_path):
    fixture, gate = planning_fixture(tmp_path)
    worker = fixture.store.claim(fixture.controller, fixture.member.agent_id, "first")
    accepted_result(fixture, worker)
    intent = enqueue(fixture.store, gate, "advance_goal", proposal(gate, "deliver"), "deliver")
    assert drain(fixture.store, fixture.controller)[0]["state"] == "applied"
    acknowledge(fixture.store, fixture.controller, intent["id"])
    barrier = fixture.store.claim(fixture.controller, gate.agent_id, BARRIER + "1")
    close_barrier(fixture.store, fixture.controller, barrier)
    delivery = fixture.store.claim(fixture.controller, gate.agent_id, DELIVERY)
    request = enqueue(
        fixture.store,
        delivery,
        "request_reconciliation",
        {
            "task_ids": ["first"],
            "summary": "Check a conflicting claim",
            "reason": "Reconcile before delivery",
            "evidence": [],
        },
        "late-reconciliation",
    )
    with pytest.raises(SwarmConflictError, match="Pending planning"):
        accepted_result(fixture, delivery, "Final result with accepted proof")
    assert fixture.store.read_task(delivery, DELIVERY)["state"] == "running"
    assert drain(fixture.store, fixture.controller)[0]["state"] == "rejected"
    assert not any(
        task["id"] == "reconcile_" + request["id"] for task in fixture.store.records("tasks")
    )
    assert (
        accepted_result(fixture, delivery, "Final result with accepted proof")["state"]
        == "succeeded"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("maximum", [1, 2])
async def test_planning_reserves_a_tool_slot_for_its_control_decision(tmp_path, maximum):
    service = runtime(tmp_path)
    factory = StagedFactory(inspect_evidence=maximum == 2)
    service.brain_factory = factory
    try:
        team = await service.create_team(
            TeamCreate(
                name="Bounded planning",
                goal="42",
                request_key="bounded",
                limits=BudgetLimits(max_tool_calls=maximum),
            )
        )
        await service.control(team["id"], "start")
        assert (await terminal(service, team["id"], timeout=40))["state"] == "succeeded"
        if maximum == 1:
            assert factory.offered_tools and all(not tools for tools in factory.offered_tools)
            assert factory.inspected == []
        else:
            assert factory.inspected == ["first-0"]
    finally:
        await service.stop()


def test_premature_delivery_and_cycle_are_rejected_without_partial_graph_changes(tmp_path):
    fixture, actor = planning_fixture(tmp_path)
    for key, payload in (
        ("premature", proposal(actor, "deliver")),
        ("cycle", proposal(actor, tasks=[work("cycle") | {"dependencies": [DELIVERY]}])),
    ):
        request = enqueue(fixture.store, actor, "advance_goal", payload, key)
        outcome = drain(fixture.store, fixture.controller)[0]
        assert outcome["state"] == "rejected"
        acknowledge(fixture.store, fixture.controller, request["id"])
    assert not any(item["id"] in {"second", "cycle"} for item in fixture.store.records("tasks"))
    assert fixture.store.get()["checkpoint"]["autonomy"]["stage"] == 1
    worker = fixture.store.claim(fixture.controller, fixture.member.agent_id, "first")
    with pytest.raises(SwarmAccessError):
        enqueue(fixture.store, worker, "advance_goal", proposal(actor), "worker-grant")
    with pytest.raises(ValueError):
        enqueue(
            fixture.store, actor, "advance_goal", proposal(actor, goal="Changed goal"), "goal-grant"
        )


@pytest.mark.asyncio
async def test_stop_fences_inflight_goal_planning_without_adding_another_batch(tmp_path):
    service = runtime(tmp_path)
    factory = StagedFactory(pause_stage=1)
    service.brain_factory = factory
    try:
        team = await service.create_team(
            TeamCreate(name="Stop planning", goal="42", request_key="stop-plan")
        )
        await service.control(team["id"], "start")
        await asyncio.wait_for(factory.review_entered.wait(), 30)
        await service.control(team["id"], "stop")
        factory.release_review.set()
        await asyncio.sleep(0.05)
        assert (await service.team(team["id"]))["state"] == "canceled"
        tasks = await service.records(team["id"], "tasks")
        assert not any(item["id"] == "second-0" for item in tasks)
        assert int((await service.team(team["id"]))["limits"]["token_budget"]) == 250000
    finally:
        factory.release_review.set()
        await service.stop()


def test_legacy_pending_delivery_is_extended_with_new_control_tasks(tmp_path):
    fixture = running_team(
        tmp_path,
        tasks=[
            TaskSpec.model_validate(work("first")),
            TaskSpec(
                id=DELIVERY,
                title="Deliver",
                description="42",
                acceptance="Return42",
                dependencies=["first"],
            ),
        ],
    )
    lead = fixture.store.actor_for(fixture.controller, fixture.team["lead_id"])
    request = enqueue(
        fixture.store,
        lead,
        "add_tasks",
        {
            "tasks": [work("second")],
            "source_task_id": "first",
            "reason": "Resolve another part of the authorized goal",
        },
        "extend",
    )
    decision = drain(fixture.store, fixture.controller)[0]
    assert decision["id"] == request["id"] and decision["state"] == "applied"
    assert fixture.store.get_record("tasks", "second")["dependencies"] == ["first"]
    assert fixture.store.get_record("tasks", DELIVERY)["dependencies"] == ["first", "second"]
