"""Real preparation authority, accounting, recovery and explicit owner launch."""

import asyncio
import json
from decimal import Decimal

import pytest

from jarvis.brain.swarm_factory import SwarmProvider
from jarvis.core.protocols import BrainDelta
from jarvis.core.swarm_preparation import (
    PreparationAnswers,
    PreparationBegin,
    PreparationLaunch,
    PreparationPlan,
)
from jarvis.core.swarm_types import BudgetLimits, TaskSpec, TeamCreate
from jarvis.swarm.preparation import _Accounting, _begin, _finish, _launch, read
from jarvis.swarm.store import SwarmAccessError, SwarmConflictError, TeamRegistry
from tests.fakes.swarm_runtime import SyntheticBrain, runtime, terminal

QUESTIONS = {
    "questions": [
        {
            "id": "deliverable",
            "prompt": "What should the arithmetic report contain?",
            "choices": ["The exact value", "Value and explanation"],
            "hint": "Choose the useful output.",
        }
    ]
}
PLAN = {
    "goal": "42",
    "acceptance": "Return value 42",
    "summary": "Calculate and independently verify 42.",
    "assumptions": ["Exact integer arithmetic"],
    "exclusions": ["No external publication"],
    "tasks": [
        {
            "id": "calculate",
            "title": "Calculate the exact value",
            "description": "42",
            "acceptance": "Return value 42",
        }
    ],
    "remaining_decomposition": False,
}


class PreparationBrain:
    def __init__(self, factory):
        self.factory = factory

    async def complete(self, request):
        if not request.system.startswith("You prepare"):
            async for delta in SyntheticBrain(self.factory).complete(request):
                yield delta
            return
        self.factory.requests.append(request)
        self.factory.started.set()
        assert not request.tools
        if self.factory.gate is not None:
            await self.factory.gate.wait()
        if self.factory.failure is not None:
            raise self.factory.failure
        output = QUESTIONS if '"questions"' in request.system else self.factory.plan
        if self.factory.tool_call:
            yield BrainDelta(tool_call={"id": "bad", "name": "run_javascript", "input": {}})
        yield BrainDelta(content=json.dumps(output))
        yield BrainDelta(
            finish_reason="stop",
            usage={"input_tokens": 30, "output_tokens": 20} if self.factory.usage else None,
        )


class PreparationFactory:
    def __init__(self):
        self.requests = []
        self.started = asyncio.Event()
        self.gate = None
        self.failure = None
        self.tool_call = False
        self.usage = True
        self.plan = PLAN
        self.closed = 0

    async def create(self):
        factory = self

        class Brain(PreparationBrain):
            async def aclose(self):
                factory.closed += 1

        return SwarmProvider(Brain(self), "synthetic", "synthetic", Decimal("1"))

    async def catalog(self):
        return []

    def failed(self, provider):
        raise AssertionError("Preparation does not delegate SDK errors")


def prep_runtime(path):
    service = runtime(path)
    service.brain_factory = PreparationFactory()
    return service


def spec(key="prepare"):
    return TeamCreate(name="Arithmetic", goal="Find the exact answer", request_key=key)


def answers(view, key="answers", **changes):
    return PreparationAnswers(
        expected_revision=view["revision"],
        expected_storage_generation=view["team"]["storage_generation"],
        answers={"deliverable": "The exact value"},
        request_key=key,
        **changes,
    )


def launch(view, key="launch"):
    return PreparationLaunch(
        expected_revision=view["revision"],
        expected_storage_generation=view["team"]["storage_generation"],
        digest=view["digest"],
        request_key=key,
    )


@pytest.mark.asyncio
async def test_clarify_plan_then_exact_launch_and_no_execution_before_approval(tmp_path):
    service = prep_runtime(tmp_path / "swarm")
    try:
        questions = await service.create_preparation(spec())
        team_id = questions["team"]["id"]
        assert questions["state"] == "clarifying" and not questions["busy"]
        assert questions["team"]["state"] == "created"
        assert questions["team"]["started_at"] is None
        assert len(service.brain_factory.requests) == 1
        assert questions["team"]["tokens_used"] == "50"
        assert questions["team"]["tokens_reserved"] == "0"
        for action in ("start", "resume"):
            with pytest.raises(SwarmConflictError, match="explicitly launch"):
                await service.control(team_id, action)
        assert await service.records(team_id, "tasks") == []
        ready = await service.answer_preparation(team_id, answers(questions))
        assert ready["state"] == "ready" and ready["team"]["state"] == "created"
        assert ready["plan"] == PreparationPlan.model_validate(PLAN).model_dump(mode="json")
        assert ready["team"]["goal"] == spec().goal
        assert await service.records(team_id, "tasks") == []
        repeated = await service.answer_preparation(team_id, answers(questions))
        assert repeated["digest"] == ready["digest"]
        assert len(service.brain_factory.requests) == 2
        assert len(json.dumps(ready["team"]["checkpoint"]).encode()) < 16384
        result = await service.launch_preparation(team_id, launch(ready))
        assert result["state"] == "launched" and result["team"]["state"] == "running"
        assert result["team"]["goal"] == "42"
        assert (await service.launch_preparation(team_id, launch(ready)))["state"] == "launched"
        assert (await terminal(service, team_id))["state"] == "succeeded"
        records = await service.records(team_id, "decisions")
        approvals = [item for item in records if item["kind"] == "preparation_approval"]
        assert len(approvals) == 1
        operation = next(
            item
            for item in records
            if item["kind"] == "preparation_operation" and item["phase"] == "plan"
        )
        assert operation["original_goal"] == spec().goal
        assert operation["answers"] == {"deliverable": "The exact value"}
        store = service.registry.open(team_id)
        with store._tx() as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM ratings WHERE task_id='__swarm_plan'"
                ).fetchone()[0]
                == 0
            )
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_saved_questions_and_plan_survive_restart_without_inference(tmp_path):
    service = prep_runtime(tmp_path)
    questions = await service.create_preparation(spec())
    await service.stop()
    recovered = prep_runtime(tmp_path)
    try:
        saved = await recovered.preparation(questions["team"]["id"])
        assert saved["questions"] == questions["questions"]
        assert recovered.brain_factory.requests == []
        ready = await recovered.answer_preparation(saved["team"]["id"], answers(saved))
        assert ready["state"] == "ready"
    finally:
        await recovered.stop()


@pytest.mark.asyncio
async def test_overlapping_analysis_is_joined_and_foreign_revision_is_rejected(tmp_path):
    service = prep_runtime(tmp_path)
    service.brain_factory.gate = asyncio.Event()
    work = asyncio.create_task(service.create_preparation(spec()))
    try:
        await asyncio.wait_for(service.brain_factory.started.wait(), 5)
        team = (await service.list_teams())[0]
        current = await service.preparation(team["id"])
        assert current["busy"] and current["team"]["state"] == "created"
        replay = asyncio.create_task(service.create_preparation(spec()))
        with pytest.raises(SwarmConflictError, match="already in progress"):
            await service.begin_preparation(
                team["id"],
                PreparationBegin(expected_storage_generation="", request_key="different"),
            )
        service.brain_factory.gate.set()
        first, second = await asyncio.gather(work, replay)
        assert first == second
        assert len(service.brain_factory.requests) == 1
        bad = answers(first).model_copy(update={"expected_revision": 999})
        with pytest.raises(SwarmConflictError):
            await service.answer_preparation(team["id"], bad)
        assert len(service.brain_factory.requests) == 1
    finally:
        await service.stop()
        await asyncio.gather(work, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["shutdown", "kill", "delete"])
async def test_cancel_joins_preparation_and_keeps_unknown_exposure(tmp_path, action):
    service = prep_runtime(tmp_path)
    service.brain_factory.gate = asyncio.Event()
    work = asyncio.create_task(service.create_preparation(spec()))
    try:
        await asyncio.wait_for(service.brain_factory.started.wait(), 5)
        team_id = (await service.list_teams())[0]["id"]
        store = service.registry.open(team_id)
        assert int(store.get()["tokens_reserved"]) > 0
        if action == "shutdown":
            await service.stop()
        elif action == "kill":
            await service._kill_switch.trip()
            await asyncio.wait_for(asyncio.gather(work, return_exceptions=True), 5)
            assert store.get()["state"] == "created"
        else:
            await service.delete_team(team_id, "delete")
            with pytest.raises(SwarmAccessError):
                service.registry.open(team_id)
        await asyncio.gather(work, return_exceptions=True)
        assert not service._preparation_jobs
        if action != "delete":
            saved = read(store)
            assert not saved["busy"] and saved["state"] == "failed"
            assert int(saved["team"]["tokens_reserved"]) > 0
            assert store.records("tasks") == []
    finally:
        await service.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["tools", "schema", "capability", "unknown_usage", "provider_error"]
)
async def test_preparation_bounds_tools_and_keeps_provider_errors_private(
    tmp_path, caplog, invalid
):
    service = prep_runtime(tmp_path)
    try:
        questions = await service.create_preparation(spec())
        if invalid == "tools":
            service.brain_factory.tool_call = True
        elif invalid == "schema":
            service.brain_factory.plan = {**PLAN, "budget": 1000000}
        elif invalid == "capability":
            service.brain_factory.plan = {
                **PLAN,
                "tasks": [{**PLAN["tasks"][0], "required_tools": ["host_shell"]}],
            }
        elif invalid == "provider_error":
            service.brain_factory.failure = RuntimeError("PRIVATE_PROVIDER_BODY")
        else:
            service.brain_factory.usage = False
        ready = await service.answer_preparation(questions["team"]["id"], answers(questions))
        assert ready["team"]["state"] == "created"
        assert not ready["busy"]
        assert ready["state"] == ("ready" if invalid == "unknown_usage" else "failed")
        if invalid in {"unknown_usage", "provider_error"}:
            assert int(ready["team"]["tokens_reserved"]) > 0
        assert "PRIVATE_PROVIDER_BODY" not in json.dumps(ready) + caplog.text
        assert "PRIVATE_PROVIDER_BODY" not in json.dumps(
            await service.records(ready["team"]["id"], "decisions")
        )
    finally:
        await service.stop()


def test_private_accounting_does_not_grant_worker_authority_and_fences_old_generation(tmp_path):
    registry = TeamRegistry(tmp_path)
    team = registry.create(spec().model_copy(update={"preparation_required": True}))
    store = registry.open(team["id"])
    controller = store.acquire_controller("fixture")
    operation = _begin(store, controller, "questions", "", None)
    accounting = _Accounting(store, controller, operation)
    with pytest.raises(SwarmAccessError):
        store.reserve(accounting.actor, "forbidden", "100")
    reservation = accounting.reserve(accounting.actor, "usage", "100", "10")
    accounting.reconcile(controller, reservation["id"], "30", "3")
    accounting.reconcile(controller, reservation["id"], "30", "3")
    assert store.get()["tokens_used"] == "30"
    with store._tx(write=True) as connection:
        changed = store._team(connection)
        changed["storage_generation"] = "restored"
        store._save_team(connection, changed)
    with pytest.raises(SwarmConflictError):
        accounting.reserve(accounting.actor, "stale", "100", "10")
    with pytest.raises(SwarmConflictError):
        _finish(store, controller, operation, QUESTIONS)
    assert read(store)["state"] == "failed"


def test_approval_binds_exact_plan_and_installs_atomically(tmp_path):
    registry = TeamRegistry(tmp_path)
    team = registry.create(spec().model_copy(update={"preparation_required": True}))
    store = registry.open(team["id"])
    controller = store.acquire_controller("fixture")
    questions = _finish(store, controller, _begin(store, controller, "q", "", None), QUESTIONS)
    ready = _finish(store, controller, _begin(store, controller, "a", "", answers(questions)), PLAN)
    for change in (
        {"digest": "f" * 64},
        {"expected_revision": 1},
        {"expected_storage_generation": "stale"},
    ):
        with pytest.raises(SwarmConflictError):
            _launch(store, controller, launch(ready).model_copy(update=change), 32)
        assert store.records("tasks") == []
        assert store.get()["state"] == "created"
    assert _launch(store, controller, launch(ready), 32)["state"] == "launched"
    assert len(store.records("tasks")) == 5
    assert _launch(store, controller, launch(ready), 32)["state"] == "launched"
    assert (
        registry.create(spec().model_copy(update={"preparation_required": True}))["id"]
        == team["id"]
    )


@pytest.mark.asyncio
async def test_preparation_budget_is_enforced_before_provider_request(tmp_path):
    service = prep_runtime(tmp_path)
    try:
        view = await service.create_preparation(
            spec().model_copy(
                update={"limits": BudgetLimits(token_budget="100", max_output_tokens=128)}  # noqa: S106 - quantity
            )
        )
        assert view["state"] == "failed"
        assert "budget" in view["error"]
        assert view["team"]["tokens_used"] == "0"
        assert view["team"]["tokens_reserved"] == "0"
        assert service.brain_factory.requests == []
        assert view["team"]["state"] == "created"
    finally:
        await service.stop()


def test_abandoned_operation_nonce_and_replay_never_double_spend(tmp_path):
    registry = TeamRegistry(tmp_path)
    team = registry.create(spec().model_copy(update={"preparation_required": True}))
    store = registry.open(team["id"])
    first_controller = store.acquire_controller("first")
    first = _begin(store, first_controller, "first-call", "", None)
    account = _Accounting(store, first_controller, first)
    account.reserve(account.actor, "network-request", "100", "20")
    store.release_controller(first_controller)
    controller = store.acquire_controller("second")
    assert read(store)["state"] == "failed"
    assert _begin(store, controller, "first-call", "", None) is None
    second = _begin(store, controller, "new-explicit-retry", "", None)
    assert second["revision"] > first["revision"]
    with pytest.raises((SwarmAccessError, SwarmConflictError)):
        _finish(store, first_controller, first, QUESTIONS)
    view = _finish(store, controller, second, QUESTIONS)
    assert view["state"] == "clarifying"
    assert view["team"]["tokens_reserved"] == "100"
    assert store.records("tasks") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["launch", "prepare"])
async def test_only_explicit_owner_action_rearms_after_emergency(tmp_path, action):
    service = prep_runtime(tmp_path)
    try:
        questions = await service.create_preparation(spec())
        ready = await service.answer_preparation(questions["team"]["id"], answers(questions))
        another = await service.create_team(
            TeamCreate(
                name="Other",
                goal="42",
                request_key="other",
                tasks=[
                    TaskSpec(
                        id="other",
                        title="Other task",
                        description="42",
                        acceptance="Return value 42",
                    )
                ],
            )
        )
        service.brain_factory.gate = asyncio.Event()
        await service.control(another["id"], "start")
        await service._kill_switch.trip()
        async with asyncio.timeout(5):
            while (await service.team(another["id"]))["state"] != "paused":  # noqa: ASYNC110 - durable cross-thread state
                await asyncio.sleep(0.01)
        assert service._is_suspended()
        saved = await service.preparation(ready["team"]["id"])
        assert saved["state"] == "ready" and service._is_suspended()
        if action == "launch":
            result = await service.launch_preparation(ready["team"]["id"], launch(saved))
            assert result["team"]["state"] == "running"
        else:
            service.brain_factory.gate.set()
            result = await service.answer_preparation(
                ready["team"]["id"], answers(saved, key="revise")
            )
            assert result["state"] == "ready" and result["team"]["state"] == "created"
        assert not service._is_suspended()
        assert (await service.team(another["id"]))["state"] == "paused"
    finally:
        await service.stop()


def test_legacy_creation_key_still_matches_without_preparation_flag(tmp_path):
    import hashlib

    from jarvis.swarm.store import creation_fingerprint

    original = spec()
    legacy = original.model_dump(mode="json", exclude={"preparation_required"})
    expected = hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()
    assert creation_fingerprint(original) == expected
    assert (
        creation_fingerprint(original.model_copy(update={"preparation_required": True})) != expected
    )
    registry = TeamRegistry(tmp_path)
    created = registry.create(original)
    assert registry.create(original)["id"] == created["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["clarifying", "planning"])
@pytest.mark.parametrize(
    "condition", ["current", "stale_version", "stale_generation", "invalid_action"]
)
async def test_owner_control_validates_before_canceling_preparation(tmp_path, phase, condition):
    service = prep_runtime(tmp_path)
    work = None
    try:
        if phase == "planning":
            questions = await service.create_preparation(spec())
            service.brain_factory.started.clear()
            service.brain_factory.gate = asyncio.Event()
            work = asyncio.create_task(
                service.answer_preparation(questions["team"]["id"], answers(questions))
            )
        else:
            service.brain_factory.gate = asyncio.Event()
            work = asyncio.create_task(service.create_preparation(spec()))
        await asyncio.wait_for(service.brain_factory.started.wait(), 5)
        team = (await service.list_teams())[0]
        before = await service.preparation(team["id"])
        request_count = len(service.brain_factory.requests)
        assert before["busy"] and before["state"] == phase
        version = team["version"] - 1 if condition == "stale_version" else team["version"]
        generation = (
            "previous-generation" if condition == "stale_generation" else team["storage_generation"]
        )
        action = "pause" if condition == "invalid_action" else "cancel"
        if condition == "current":
            result = await service.control(
                team["id"], action, expected_version=version, expected_storage_generation=generation
            )
            assert result["state"] == "canceled"
            assert result["version"] == team["version"] + 1
            after = await service.preparation(team["id"])
            assert after["team"]["state"] == "canceled" and not after["busy"]
            assert after["team"]["version"] == result["version"]
            outcome = await asyncio.gather(work, return_exceptions=True)
            assert isinstance(outcome[0], asyncio.CancelledError)
            assert not service._preparation_jobs
            assert after["team"]["tokens_reserved"] == before["team"]["tokens_reserved"]
        else:
            with pytest.raises(SwarmConflictError):
                await service.control(
                    team["id"],
                    action,
                    expected_version=version,
                    expected_storage_generation=generation,
                )
            after = await service.preparation(team["id"])
            assert after == before
            assert not work.done() and team["id"] in service._preparation_jobs
            assert len(service.brain_factory.requests) == request_count
            service.brain_factory.gate.set()
            completed = await work
            assert completed["state"] == ("ready" if phase == "planning" else "clarifying")
            assert completed["team"]["state"] == "created"
    finally:
        await service.stop()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)


@pytest.mark.asyncio
async def test_owner_thinking_time_can_outlive_both_preparation_leases(tmp_path):
    from tests.fakes.swarm_storage import StorageClock

    service = prep_runtime(tmp_path)
    clock = StorageClock()
    service.clock = clock
    service.registry.local.clock = clock
    try:
        questions = await service.create_preparation(spec())
        team_id = questions["team"]["id"]
        original = service._controllers[team_id]
        clock.now += 600
        assert (await service.preparation(team_id))["questions"] == questions["questions"]
        ready = await service.answer_preparation(team_id, answers(questions))
        assert ready["state"] == "ready" and ready["team"]["state"] == "created"
        after_answers = service._controllers[team_id]
        assert after_answers.fence > original.fence
        assert len(service.brain_factory.requests) == 2
        clock.now += 600
        launched = await service.launch_preparation(team_id, launch(ready))
        assert launched["state"] == "launched" and launched["team"]["state"] == "running"
        assert service._controllers[team_id].fence > after_answers.fence
        assert launched["team"]["started_at"] == clock.now
        assert len(service.brain_factory.requests) == 2
        assert launched["team"]["tokens_used"] == "100"
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_expired_preparation_controller_never_steals_another_live_lease(tmp_path):
    from tests.fakes.swarm_storage import StorageClock

    service = prep_runtime(tmp_path)
    clock = StorageClock()
    service.clock = clock
    service.registry.local.clock = clock
    try:
        questions = await service.create_preparation(spec())
        team_id = questions["team"]["id"]
        clock.now += 600
        store = service.registry.open(team_id)
        competing = store.acquire_controller("another-live-controller")
        assert competing is not None
        with pytest.raises(SwarmConflictError, match="Another controller"):
            await service.answer_preparation(team_id, answers(questions))
        assert len(service.brain_factory.requests) == 1
        assert (await service.preparation(team_id))["revision"] == questions["revision"]
        with store._tx() as connection:
            store._controller(connection, competing)
        assert team_id not in service._controllers
        store.release_controller(competing)
        ready = await service.answer_preparation(team_id, answers(questions))
        assert ready["state"] == "ready"
        assert service._controllers[team_id].fence > competing.fence
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_unexpired_preparation_controller_renews_without_changing_fence(tmp_path):
    from tests.fakes.swarm_storage import StorageClock

    service = prep_runtime(tmp_path)
    clock = StorageClock()
    service.clock = clock
    service.registry.local.clock = clock
    try:
        questions = await service.create_preparation(spec())
        team_id = questions["team"]["id"]
        original = service._controllers[team_id]
        clock.now += 35
        ready = await service.answer_preparation(team_id, answers(questions))
        assert ready["state"] == "ready"
        assert service._controllers[team_id] == original
    finally:
        await service.stop()
