"""Real PostgreSQL planning barriers and useful distributed queue expansion."""

# ruff: noqa: F811 - imported pytest fixture is injected by parameter name

import json
import os

import pytest

from jarvis.core.swarm_types import BudgetLimits, TaskSpec, TeamCreate
from jarvis.swarm.autonomy import DELIVERY, GATE, PLAN, context, install
from jarvis.swarm.control_requests import acknowledge, drain, enqueue
from jarvis.swarm.store import SwarmConflictError
from jarvis.swarm.supervision import ready_turn
from tests.contract.test_swarm_distributed import registries  # noqa: F401

pytestmark = pytest.mark.skipif(
    os.environ.get("SWARM_DISTRIBUTED_TEST") != "1",
    reason="Explicit disposable PostgreSQL/Redis services are unavailable",
)


def tasks(prefix):
    return [
        TaskSpec(
            id=f"{prefix}-{index}",
            title="Inspect one distinct case",
            description=f"Inspect authorized case {index}",
            acceptance="Report independently checked evidence",
        )
        for index in range(32)
    ]


def test_distributed_lead_plans_another_useful_batch_before_the_first_finishes(registries):
    make, _ = registries
    registry = make()
    team = registry.create(
        TeamCreate(
            name="Distributed goal",
            goal="Compare distinct cases",
            request_key="goal",
            mode="distributed",
            limits=BudgetLimits(concurrency=1000, worker_limit="1000", token_budget=str(10**10)),
            tasks=[
                TaskSpec(
                    id=PLAN,
                    title="Plan",
                    description="Plan the goal",
                    acceptance="Bounded valid tasks",
                )
            ],
        )
    )
    store = registry.open(team["id"])
    controller = store.acquire_controller("planner")
    store.transition(controller, "running")
    actor = store.claim(controller, team["lead_id"], PLAN)
    artifact = store.capture_result(controller, actor, "plan.json", "{}", "plan")
    install(store, controller, actor, tasks("first"), True, artifact["id"], 1000)
    gate = ready_turn(store)
    assert gate["id"] == GATE + "1"
    actor = store.claim(controller, team["lead_id"], gate["id"])
    before = store.get()
    prompt = context(store, controller, actor, 1000)
    assert prompt["unfinished_tasks"] == 32
    assert prompt["effective_capacity"] == 1000
    payload = {
        "stage_task_id": actor.task_id,
        "decision": "continue",
        "tasks": [item.model_dump(mode="json") for item in tasks("second")],
        "summary": "Continue comparing additional distinct cases",
        "reason": "The authorized set has more cases",
        "remaining_decomposition": True,
    }
    stale = enqueue(store, actor, "advance_goal", payload, "obsolete-attempt")
    store.fail(actor, "Retry planning", controller=controller)
    actor = store.claim(controller, team["lead_id"], gate["id"])
    assert drain(store, controller)[0]["state"] == "rejected"
    assert store.read_task(actor, gate["id"])["state"] == "running"
    acknowledge(store, controller, stale["id"])
    request = enqueue(
        store,
        actor,
        "advance_goal",
        payload,
        "second-batch",
    )
    result = drain(store, controller)[0]
    assert result["state"] == "applied"
    assert drain(store, controller)[0] == result
    acknowledge(store, controller, request["id"])
    assert (
        len(
            [
                item
                for item in store.records("tasks", limit=100)
                if not item["id"].startswith("__swarm_")
            ]
        )
        == 64
    )
    assert ready_turn(store)["id"] == GATE + "2"
    assert len(store.records("agents")) == 1  # Planning creates work, never empty filler workers.
    after = store.get()
    assert all(before[key] == after[key] for key in ("goal", "acceptance", "limits", "policy"))
    assert len(json.dumps(prompt).encode("utf-8")) < 65536
    with pytest.raises(SwarmConflictError):
        store.claim(controller, team["lead_id"], DELIVERY)
