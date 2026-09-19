"""Concurrent exact accounting across lead/coordinator/worker identities."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from jarvis.core.swarm_types import BudgetLimits, TeamRecord
from jarvis.swarm.store import SwarmAccessError, SwarmBudgetError
from tests.fakes.swarm_storage import running_team, task


def test_ten_billion_reservations_reconcile_retry_and_fence_without_overflow(tmp_path):
    fixture = running_team(
        tmp_path,
        tasks=[task(f"task-{index}") for index in range(16)],
        limits=BudgetLimits(token_budget="10000000000", concurrency=16, worker_limit="32"),
    )
    store, controller = fixture.store, fixture.controller
    members = [
        store.actor_for(controller, fixture.team["lead_id"]),
        store.add_agent(controller, "Coordinator", role="coordinator"),
    ]
    members.extend(store.add_agent(controller, f"Worker {index}") for index in range(14))
    actors = [
        store.claim(controller, member.agent_id, f"task-{index}")
        for index, member in enumerate(members)
    ]
    barrier = Barrier(16)

    def reserve(actor):
        barrier.wait(timeout=15)
        try:
            return actor, store.reserve(actor, "parallel", "650000000", "0")
        except SwarmBudgetError:
            return actor, None

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(reserve, actors))
    admitted = [(actor, record) for actor, record in outcomes if record]
    refused = next(actor for actor, record in outcomes if record is None)
    assert len(admitted) == 15
    assert store.get()["tokens_reserved"] == "9750000000"
    for actor, record in admitted:
        assert store.reserve(actor, "parallel", "650000000", "0")["id"] == record["id"]
    assert store.get()["tokens_reserved"] == "9750000000"

    first_actor, first = admitted[0]
    unknown_actor, unknown = admitted[1]
    store.reconcile(controller, first["id"], "300000000", "0")
    store.reconcile(controller, unknown["id"], None, None)
    store.reserve(refused, "retry", "600000000", "0")
    team = TeamRecord.model_validate(store.get()).model_dump(mode="json")
    assert team["tokens_used"] == "300000000"
    assert team["tokens_reserved"] == "9700000000"
    with pytest.raises(SwarmBudgetError):
        store.reserve(first_actor, "one-too-many", "1", "0")

    store.fail(unknown_actor, "Synthetic worker crash", controller=controller)
    replacement = store.claim(controller, unknown_actor.agent_id, unknown_actor.task_id)
    with pytest.raises(SwarmAccessError):
        store.reserve(unknown_actor, "stale", "1", "0")
    assert replacement.task_fence > unknown_actor.task_fence
    assert store.get()["tokens_reserved"] == "9700000000"
    store.reconcile(controller, unknown["id"], "100000000", "0")
    store.reconcile(controller, unknown["id"], "100000000", "0")
    team = TeamRecord.model_validate(store.get()).model_dump(mode="json")
    assert team["tokens_used"] == "400000000"
    assert team["tokens_reserved"] == "9050000000"
    assert int(team["tokens_used"]) + int(team["tokens_reserved"]) <= 10_000_000_000
