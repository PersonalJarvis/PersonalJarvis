"""SQLite/PostgreSQL batch admission, assignment and stop-fence parity."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from jarvis.core.swarm_types import BudgetLimits, TeamCreate
from jarvis.swarm.control_requests import drain, enqueue
from jarvis.swarm.specialists import assign
from jarvis.swarm.store import SwarmAccessError, SwarmConflictError, TeamRegistry
from tests.contract.test_swarm_distributed import registries  # noqa: F401 - shared fixture
from tests.fakes.swarm_storage import StorageClock, accepted_result, task


@pytest.fixture(params=["local", "postgres"])
def dispatch_team(request, tmp_path):
    if request.param == "postgres":
        if os.environ.get("SWARM_DISTRIBUTED_TEST") != "1":
            pytest.skip("Explicit disposable PostgreSQL services are unavailable")
        registry = request.getfixturevalue("registries")[0]()
        mode = "distributed"
    else:
        registry = TeamRegistry(tmp_path, clock=StorageClock())
        mode = "local"
    team = registry.create(
        TeamCreate(
            name="Dispatch contract",
            goal="Verify bounded dispatch",
            mode=mode,
            request_key="dispatch",
            limits=BudgetLimits(concurrency=32, worker_limit="64"),
            tasks=[task(f"work-{i:02}") for i in range(40)],
        )
    )
    store = registry.open(team["id"])
    controller = store.acquire_controller("contract")
    store.transition(controller, "running")
    return store, controller


def test_competing_rounds_preserve_cap_reserve_lead_and_claim_once(dispatch_team):
    store, controller = dispatch_team

    def dispatch(_):
        return store.claim_batch(controller, limit=8, worker_slots=8)

    with ThreadPoolExecutor(max_workers=6) as pool:
        batches = list(pool.map(dispatch, range(6)))
    claims = [claim for batch in batches for claim in batch.claims]
    assert len(claims) == 31
    assert all(len(batch.claims) <= 8 for batch in batches)
    assert len({claim.actor.task_id for claim in claims}) == len(claims)
    assert len({claim.actor.agent_id for claim in claims}) == len(claims)
    assert all(store.heartbeat(claim.actor) for claim in claims)
    assert store.get_record("agents", store.get()["lead_id"])["state"] == "idle"
    assert len(store.ready_tasks()) == 9


def test_pausing_after_commit_fences_every_returned_descriptor(dispatch_team):
    store, controller = dispatch_team
    batch = store.claim_batch(controller, limit=8, worker_slots=8)
    assert len(batch.claims) == 8
    store.user_transition("paused")
    for claim in batch.claims:
        with pytest.raises(SwarmAccessError):
            store.heartbeat(claim.actor)
        with pytest.raises(SwarmAccessError):
            store.tool_context(claim.actor)
    with pytest.raises((SwarmAccessError, SwarmConflictError)):
        store.claim_batch(controller, limit=8, worker_slots=8)


def test_pending_async_cleanup_cannot_be_replaced_by_a_new_claim(dispatch_team):
    store, controller = dispatch_team
    batch = store.claim_batch(
        controller, limit=1, worker_slots=1, excluded_tasks=frozenset({"work-00"})
    )
    assert batch.claims[0].actor.task_id == "work-01"
    assert store.get_record("tasks", "work-00")["attempt_count"] == 0


@pytest.mark.asyncio
async def test_cached_claim_limits_never_allow_inference_after_pause(dispatch_team):
    from decimal import Decimal

    from jarvis.brain.swarm_factory import SwarmProvider
    from jarvis.control.cancel import CancelToken
    from jarvis.swarm.worker import WorkerEngine
    from tests.unit.swarm.test_worker import ScriptedBrain

    store, controller = dispatch_team
    batch = store.claim_batch(controller, limit=1, worker_slots=1)
    brain = ScriptedBrain()
    engine = WorkerEngine(
        store,
        batch.claims[0].actor,
        controller,
        SwarmProvider(brain, "synthetic", "synthetic", Decimal("1")),
        CancelToken(),
    )
    store.user_transition("paused")
    with pytest.raises(SwarmAccessError):
        await engine.completion([], "work", initial_team=json.loads(batch.team_json))
    assert not brain.requests


def test_specialist_is_unclaimed_until_source_check_then_assignment_is_revalidated(dispatch_team):
    store, controller = dispatch_team
    specialist = assign(
        store,
        controller,
        {
            "id": "ordinary-source",
            "name": "Specialist",
            "title": "Researcher",
            "focus": ["Verification"],
            "state": "active",
        },
        "Approved input only",
        "member",
    )
    lead = store.actor_for(controller, store.get()["lead_id"])
    enqueue(
        store,
        lead,
        "assign_task",
        {
            "task_id": "work-00",
            "agent_id": specialist["id"],
            "reason": "Use approved expertise",
        },
        "assignment",
    )
    assert drain(store, controller)[0]["state"] == "applied"
    batch = store.claim_batch(controller, limit=1, worker_slots=1)
    assert batch.claims == ()
    assert batch.sources == ((specialist["id"], "ordinary-source"),)
    assert store.get_record("tasks", "work-00")["attempt_count"] == 0
    # A source check cannot grant authority to a subsequently revoked member.
    store.revoke_member(controller, specialist["id"])
    next_batch = store.claim_batch(
        controller,
        limit=1,
        worker_slots=1,
        source_ids=frozenset({"ordinary-source"}),
    )
    assert all(claim.actor.agent_id != specialist["id"] for claim in next_batch.claims)
    assert store.get_record("tasks", "work-00")["state"] == "blocked"


def test_reserved_slot_claims_persistent_lead_without_admitting_another_worker(dispatch_team):
    store, controller = dispatch_team
    store.add_tasks(controller, [task("__swarm_plan", priority=9)])
    batch = store.claim_batch(controller, limit=1, worker_slots=0)
    assert len(batch.claims) == 1
    assert batch.claims[0].actor.task_id == "__swarm_plan"
    assert batch.claims[0].actor.agent_id == store.get()["lead_id"]
    assert len(store.ready_tasks()) == 32  # bounded page; all ordinary work remains ready


def test_coordinator_cannot_consume_the_slot_reserved_for_the_persistent_lead(dispatch_team):
    from jarvis.swarm.supervision import queue_coordinators, ready_turn

    base, _ = dispatch_team
    registry = getattr(base, "registry", None) or base._registry
    team = registry.create(
        TeamCreate(
            name="Reserved lead",
            goal="Keep lead responsive",
            mode=base.get()["mode"],
            request_key="reserved-lead",
            limits=BudgetLimits(concurrency=3, worker_limit="8"),
            tasks=[task(f"work-{i}") for i in range(3)],
        )
    )
    store = registry.open(team["id"])
    controller = store.acquire_controller("reserved-lead")
    store.transition(controller, "running")
    first = store.claim_batch(controller, limit=2, worker_slots=2)
    assert len(first.claims) == 2
    store.add_agent(controller, "Group coordinator", role="coordinator")
    store.clock.now += 6
    queue_coordinators(store, controller)
    assert ready_turn(store) is not None
    blocked = store.claim_batch(controller, limit=1, worker_slots=0)
    assert blocked.claims == ()
    # Durable team reservation also applies when another scheduler has process capacity.
    blocked = store.claim_batch(controller, limit=1, worker_slots=1)
    assert blocked.claims == ()
    store.add_tasks(controller, [task("__swarm_plan", priority=9)])
    lead = store.claim_batch(controller, limit=1, worker_slots=0)
    assert len(lead.claims) == 1
    assert lead.claims[0].actor.agent_id == team["lead_id"]


def test_batch_rejects_unbounded_round_before_any_claim(dispatch_team):
    store, controller = dispatch_team
    with pytest.raises(ValueError, match="eight"):
        store.claim_batch(controller, limit=9, worker_slots=9)
    assert all(row["state"] == "ready" for row in store.records("tasks"))


def identity_limited_team(dispatch_team, tasks):
    base, _ = dispatch_team
    registry = getattr(base, "registry", None) or base._registry
    team = registry.create(
        TeamCreate(
            name="Reusable identity capacity",
            goal="Complete work with one worker",
            mode=base.get()["mode"],
            request_key="identity-capacity",
            limits=BudgetLimits(concurrency=4, worker_limit="1"),
            tasks=tasks,
        )
    )
    store = registry.open(team["id"])
    controller = store.acquire_controller("identity-capacity")
    store.transition(controller, "running")
    return SimpleNamespace(store=store, controller=controller)


def test_identity_limit_commits_partial_batch_waits_then_reuses_busy_worker(dispatch_team):
    fixture = identity_limited_team(dispatch_team, [task(f"work-{i}") for i in range(3)])
    store, controller = fixture.store, fixture.controller

    def complete(actor):
        evidence = store.capture_result(
            controller,
            actor,
            "Result",
            "Verified result",
            f"result:{actor.task_id}:{actor.task_fence}",
        )
        return accepted_result(fixture, actor, evidence=[evidence["id"]])

    initial = store.claim_batch(controller, limit=3, worker_slots=3)
    assert len(initial.claims) == 1
    first = initial.claims[0].actor
    assert store.get()["state"] == "running"
    assert len(store.ready_tasks()) == 2
    assert store.claim_batch(controller, limit=3, worker_slots=3).claims == ()
    assert store.heartbeat(first)
    complete(first)
    for remaining in (1, 0):
        batch = store.claim_batch(controller, limit=3, worker_slots=3)
        assert len(batch.claims) == 1
        assert batch.claims[0].actor.agent_id == first.agent_id
        assert len(store.ready_tasks()) == remaining
        complete(batch.claims[0].actor)
    assert len(store.records("agents")) == 2  # Persistent lead plus the reused worker.
    assert all(record["state"] == "succeeded" for record in store.records("tasks"))
    assert not any(record["kind"] == "task.blocked" for record in store.events_after())


def test_unavailable_domain_capacity_blocks_only_that_task_preserving_prior_claim(dispatch_team):
    fixture = identity_limited_team(
        dispatch_team,
        [
            task("work-0", domain="arithmetic"),
            task("work-1", domain="research"),
        ],
    )
    store, controller = fixture.store, fixture.controller
    batch = store.claim_batch(controller, limit=3, worker_slots=3)
    assert len(batch.claims) == 1
    assert store.heartbeat(batch.claims[0].actor)
    unavailable = store.get_record("tasks", "work-1")
    assert unavailable["state"] == "blocked"
    assert "no eligible worker can be reused" in unavailable["reason"]
    assert store.get()["state"] == "running"
    accepted_result(fixture, batch.claims[0].actor)
    assert store.get_record("tasks", "work-0")["state"] == "succeeded"
