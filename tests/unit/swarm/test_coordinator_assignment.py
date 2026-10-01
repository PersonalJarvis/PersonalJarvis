"""Production role creation, scoped coordination and atomic task assignment."""

import pytest

from jarvis.core.swarm_types import BudgetLimits
from jarvis.swarm.control_requests import acknowledge, assignment_for, drain, enqueue
from jarvis.swarm.store import SwarmAccessError, SwarmConflictError
from jarvis.swarm.supervision import PREFIX, complete_turn, queue_coordinators, ready_turn
from tests.fakes.swarm_storage import running_team, task


def request(fixture, actor, operation, key=None, **payload):
    return enqueue(
        fixture.store,
        actor,
        operation,
        {"reason": "Advance the authorized group work", **payload},
        key or operation,
    )


def apply(fixture):
    result = drain(fixture.store, fixture.controller)
    for record in result:
        acknowledge(fixture.store, fixture.controller, record["id"])
    return result


def test_spawn_coordinator_is_durable_group_bound_and_shares_worker_budget(tmp_path):
    fixture = running_team(tmp_path, limits=BudgetLimits(worker_limit="2"))
    lead = fixture.store.actor_for(fixture.controller, fixture.team["lead_id"])
    record = request(fixture, lead, "spawn_coordinator", name="Coordinator", group_id="research")
    result = apply(fixture)[0]
    coordinator_id = result["outcome"]["agent_id"]
    member = fixture.registry.open(fixture.team["id"]).get_record("agents", coordinator_id)
    assert member["role"] == "coordinator" and member["group_id"] == "research"
    assert (
        request(fixture, lead, "spawn_coordinator", name="Coordinator", group_id="research")["id"]
        == record["id"]
    )
    request(fixture, lead, "spawn_worker")
    assert apply(fixture)[0]["state"] == "rejected"
    assert fixture.store.get()["lead_id"] == lead.agent_id
    coordinator = fixture.store.actor_for(fixture.controller, coordinator_id)
    for actor in (fixture.member, coordinator):
        with pytest.raises(SwarmAccessError, match="role"):
            request(fixture, actor, "spawn_coordinator", group_id="other")
        with pytest.raises(SwarmAccessError, match="role"):
            request(fixture, actor, "assign_task", task_id="work", agent_id=fixture.member.agent_id)


def test_assignment_fences_old_attempt_replays_once_and_guards_claim(tmp_path):
    fixture = running_team(tmp_path)
    lead = fixture.store.actor_for(fixture.controller, fixture.team["lead_id"])
    old = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    replacement = fixture.store.add_agent(fixture.controller, "Replacement")
    record = request(fixture, lead, "assign_task", task_id="work", agent_id=replacement.agent_id)
    result = drain(fixture.store, fixture.controller)[0]
    assert result["state"] == "applied"
    assert result["outcome"]["affected_attempts"][0]["fence"] == old.task_fence
    assert drain(fixture.store, fixture.controller) == [result]
    acknowledge(fixture.store, fixture.controller, record["id"])
    with pytest.raises(SwarmAccessError):
        fixture.store.heartbeat(old)
    with pytest.raises(SwarmAccessError, match="assigned"):
        fixture.store.claim(fixture.controller, old.agent_id, "work")
    current = fixture.store.claim(fixture.controller, replacement.agent_id, "work")
    assert current.task_fence > old.task_fence
    assert assignment_for(fixture.store, "work")["agent_id"] == replacement.agent_id
    assert fixture.store.records("reputation") == []


def test_assignment_rejects_stale_task_changed_after_enqueue(tmp_path):
    fixture = running_team(tmp_path)
    lead = fixture.store.actor_for(fixture.controller, fixture.team["lead_id"])
    replacement = fixture.store.add_agent(fixture.controller, "Replacement")
    request(fixture, lead, "assign_task", task_id="work", agent_id=replacement.agent_id)
    current = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    assert apply(fixture)[0]["state"] == "rejected"
    assert fixture.store.heartbeat(current)


def test_coordinator_turn_requires_scoped_queued_summary_and_never_awards_xp(tmp_path):
    fixture = running_team(
        tmp_path, tasks=[task(milestone="research"), task("other", milestone="other")]
    )
    lead = fixture.store.actor_for(fixture.controller, fixture.team["lead_id"])
    request(fixture, lead, "spawn_coordinator", group_id="research")
    coordinator_id = apply(fixture)[0]["outcome"]["agent_id"]
    fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    fixture.clock.now += 6
    queue_coordinators(fixture.store, fixture.controller)
    turn = ready_turn(fixture.store)
    assert turn["milestone"] == "research"
    assert "other" not in turn["description"]
    with pytest.raises(SwarmAccessError, match="principal"):
        fixture.store.claim(fixture.controller, lead.agent_id, turn["id"])
    actor = fixture.store.claim(fixture.controller, coordinator_id, turn["id"])
    with pytest.raises(SwarmConflictError, match="report_summary"):
        complete_turn(fixture.store, fixture.controller, actor, "Unqueued self-report")
    with pytest.raises(SwarmAccessError, match="group"):
        request(fixture, actor, "report_summary", summary="Wrong group", task_ids=["other"])
    request(fixture, actor, "report_summary", summary="Worker is active", task_ids=["work"])
    assert apply(fixture)[0]["state"] == "applied"
    complete_turn(fixture.store, fixture.controller, actor, "Reported group status")
    fixture.clock.now += 6
    queue_coordinators(fixture.store, fixture.controller)
    assert len([t for t in fixture.store.records("tasks") if t["id"].startswith(PREFIX)]) == 1
    assert fixture.store.records("reputation") == []
    assert fixture.store.records("verifications") == []
