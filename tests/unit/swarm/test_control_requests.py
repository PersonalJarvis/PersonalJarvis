"""Lead control authority, durable replay and per-worker execution fencing."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from jarvis.core.swarm_types import BudgetLimits
from jarvis.swarm.control_requests import acknowledge, drain, enqueue
from jarvis.swarm.store import SwarmAccessError, SwarmConflictError
from tests.fakes.swarm_storage import accepted_result, running_team, task


def lead(fixture):
    return fixture.store.actor_for(fixture.controller, fixture.team["lead_id"])


def submit(fixture, actor, operation, **payload):
    return enqueue(
        fixture.store,
        actor,
        operation,
        {"reason": "Continue the authorized work", **payload},
        operation,
    )


def apply(fixture):
    outcomes = drain(fixture.store, fixture.controller)
    for outcome in outcomes:
        acknowledge(fixture.store, fixture.controller, outcome["id"])
    return outcomes


def test_worker_and_forged_role_cannot_enqueue_control(tmp_path):
    fixture = running_team(tmp_path)
    with pytest.raises(SwarmAccessError, match="role"):
        submit(fixture, fixture.member, "spawn_worker")
    with pytest.raises(SwarmAccessError, match="membership"):
        submit(fixture, replace(fixture.member, agent_id=fixture.team["lead_id"]), "spawn_worker")
    assert fixture.store.records("decisions") == []


def test_replay_and_racing_delivery_spawn_exactly_one_logical_worker(tmp_path):
    fixture = running_team(tmp_path)
    actor = lead(fixture)
    with ThreadPoolExecutor(max_workers=6) as pool:
        queued = list(pool.map(lambda _: submit(fixture, actor, "spawn_worker"), range(6)))
    assert len({record["id"] for record in queued}) == 1
    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(lambda _: drain(fixture.store, fixture.controller), range(6)))
    assert len({result[0]["outcome"]["agent_id"] for result in outcomes}) == 1
    assert len(fixture.store.records("agents")) == 3
    record = outcomes[0][0]
    assert record["state"] == "applied"
    assert fixture.controller.token not in json.dumps(record)
    assert actor.token not in json.dumps(record)
    acknowledge(fixture.store, fixture.controller, record["id"])
    assert drain(fixture.store, fixture.controller) == []
    assert submit(fixture, actor, "spawn_worker")["id"] == record["id"]
    with pytest.raises(SwarmConflictError, match="different control"):
        submit(fixture, actor, "spawn_worker", name="Different content")


def test_pause_fences_execution_and_keeps_task_blocked_until_resume(tmp_path):
    fixture = running_team(tmp_path)
    actor = lead(fixture)
    worker = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    evidence = fixture.store.capture_result(
        fixture.controller, worker, "Progress", "Partial", "partial"
    )
    submit(fixture, actor, "pause_worker", agent_id=worker.agent_id)
    result = apply(fixture)[0]
    affected = result["outcome"]["affected_attempts"]
    assert len(affected) == 1
    assert affected[0]["agent_id"] == worker.agent_id
    assert affected[0]["fence"] == worker.task_fence
    assert fixture.store.ready_tasks() == []
    assert fixture.store.get_record("tasks", "work")["state"] == "blocked"
    assert fixture.store.get_record("agents", worker.agent_id)["state"] == "waiting"
    with pytest.raises(SwarmAccessError):
        fixture.store.heartbeat(worker)
    with pytest.raises(SwarmAccessError):
        accepted_result(fixture, worker, evidence=[evidence["id"]])
    assert fixture.store.records("artifacts")[0]["id"] == evidence["id"]
    submit(fixture, actor, "replan_blocked", task_ids=["work"])
    assert apply(fixture)[0]["state"] == "rejected"
    assert fixture.store.ready_tasks() == []
    submit(fixture, actor, "resume_worker", agent_id=worker.agent_id)
    assert apply(fixture)[0]["state"] == "applied"
    assert fixture.store.ready_tasks()[0]["id"] == "work"
    new_attempt = fixture.store.claim(fixture.controller, worker.agent_id, "work")
    assert new_attempt.task_fence > worker.task_fence
    assert fixture.store.get()["lead_id"] == actor.agent_id


@pytest.mark.parametrize("operation", ["replace_worker", "terminate_worker"])
def test_replace_and_terminate_preserve_history_and_fence_late_results(tmp_path, operation):
    fixture = running_team(tmp_path)
    actor = lead(fixture)
    worker = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    fixture.store.checkpoint(fixture.controller, {"plan": "Keep original objective"})
    submit(fixture, actor, operation, agent_id=worker.agent_id)
    result = apply(fixture)[0]
    assert result["state"] == "applied"
    with pytest.raises(SwarmAccessError):
        fixture.store.heartbeat(worker)
    agent = fixture.store.get_record("agents", worker.agent_id)
    assert agent["state"] == ("idle" if operation == "replace_worker" else "stopped")
    assert agent["generation"] == (2 if operation == "replace_worker" else 1)
    assert fixture.store.get()["checkpoint"]["plan"] == "Keep original objective"
    assert fixture.store.get()["checkpoint"]["control_requests"]["last_request_id"] == result["id"]
    assert fixture.store.get_record("tasks", "work")["state"] == "ready"
    with fixture.store._tx() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM attempts WHERE state='interrupted'"
            ).fetchone()[0]
            == 1
        )


def test_queued_worker_control_cannot_cancel_a_new_attempt(tmp_path):
    fixture = running_team(tmp_path)
    actor = lead(fixture)
    first = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    submit(fixture, actor, "terminate_worker", agent_id=first.agent_id)
    fixture.store.fail(first, "Retry", controller=fixture.controller)
    second = fixture.store.claim(fixture.controller, first.agent_id, "work")
    result = apply(fixture)[0]
    assert result["state"] == "rejected"
    assert result["outcome"]["affected_attempts"] == []
    assert fixture.store.heartbeat(second)


def test_controller_crash_replays_committed_outcome_without_duplicate_mutation(tmp_path):
    fixture = running_team(tmp_path)
    actor = lead(fixture)
    worker = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    submit(fixture, actor, "pause_worker", agent_id=worker.agent_id)
    first = drain(fixture.store, fixture.controller)[0]
    fixture.clock.now += 91
    replacement = fixture.store.acquire_controller("replacement")
    with pytest.raises(SwarmAccessError):
        drain(fixture.store, fixture.controller)
    with pytest.raises(SwarmAccessError):
        acknowledge(fixture.store, fixture.controller, first["id"])
    recovered = fixture.registry.open(fixture.team["id"])
    assert drain(recovered, replacement) == [first]
    acknowledge(recovered, replacement, first["id"])
    assert drain(recovered, replacement) == []
    assert recovered.ready_tasks() == []


def test_paused_team_cannot_enqueue_and_does_not_apply_pending_requests(tmp_path):
    fixture = running_team(tmp_path)
    actor = lead(fixture)
    submit(fixture, actor, "spawn_worker")
    fixture.store.transition(fixture.controller, "paused")
    assert drain(fixture.store, fixture.controller) == []
    with pytest.raises(SwarmAccessError):
        submit(fixture, actor, "spawn_worker")
    fixture.store.transition(fixture.controller, "running")
    assert apply(fixture)[0]["state"] == "applied"


def test_worker_limit_is_checked_when_request_is_applied(tmp_path):
    fixture = running_team(tmp_path, limits=BudgetLimits(worker_limit="1"))
    submit(fixture, lead(fixture), "spawn_worker")
    outcome = apply(fixture)[0]
    assert outcome["state"] == "rejected"
    assert "worker limit" in outcome["outcome"]["reason"]
    assert len(fixture.store.records("agents")) == 2


def test_task_addition_preserves_contract_records_source_and_rejects_scope_expansion(tmp_path):
    fixture = running_team(tmp_path)
    actor = lead(fixture)
    before = fixture.store.get()
    original_task = fixture.store.get_record("tasks", "work")
    submit(
        fixture,
        actor,
        "add_tasks",
        tasks=[task("subtask").model_dump(mode="json")],
        source_task_id="work",
    )
    result = apply(fixture)[0]
    assert result["outcome"]["source_task_id"] == "work"
    assert result["outcome"]["accepted_reason"] == "Continue the authorized work"
    assert fixture.store.get_record("tasks", "work") == original_task
    after = fixture.store.get()
    assert all(before[key] == after[key] for key in ("goal", "acceptance", "limits", "policy"))
    with pytest.raises(SwarmAccessError, match="capability"):
        submit(
            fixture,
            actor,
            "add_tasks",
            tasks=[task("unsafe", required_tools=["spawn_worker"]).model_dump(mode="json")],
            source_task_id="work",
        )
    with pytest.raises(ValueError, match="unsupported"):
        submit(
            fixture, actor, "add_tasks", tasks=[], source_task_id="work", goal="Replace objective"
        )


def test_invalid_dag_is_rejected_without_partial_task_insert(tmp_path):
    fixture = running_team(tmp_path)
    submit(
        fixture,
        lead(fixture),
        "add_tasks",
        tasks=[task("first", dependencies=["missing"]).model_dump(mode="json")],
        source_task_id="work",
    )
    assert apply(fixture)[0]["state"] == "rejected"
    assert [item["id"] for item in fixture.store.records("tasks")] == ["work"]


def test_coordinator_summary_and_reconciliation_are_group_scoped(tmp_path):
    fixture = running_team(tmp_path, tasks=[task("own", milestone="group"), task("other")])
    coordinator = fixture.store.add_agent(
        fixture.controller, "Coordinator", role="coordinator", group_id="group"
    )
    submit(
        fixture, coordinator, "report_summary", task_ids=["own"], summary="Evidence still missing"
    )
    assert apply(fixture)[0]["outcome"]["summary"] == "Evidence still missing"
    submit(
        fixture,
        coordinator,
        "request_reconciliation",
        task_ids=["own"],
        summary="Conflicting claims require verification",
    )
    result = apply(fixture)[0]
    reconciliation = fixture.store.get_record("tasks", result["outcome"]["reconciliation_task_id"])
    assert reconciliation["milestone"] == "group"
    assert reconciliation["independent_verification"]
    assert fixture.store.get_record("tasks", "own")["state"] == "ready"
    with pytest.raises(SwarmAccessError, match="group"):
        submit(fixture, coordinator, "report_summary", task_ids=["other"], summary="Out of scope")
    with pytest.raises(SwarmAccessError, match="role"):
        submit(fixture, coordinator, "spawn_worker")


def test_coordinator_evidence_cannot_cross_groups_or_invent_authority(tmp_path):
    fixture = running_team(tmp_path, tasks=[task("own", milestone="group"), task("other")])
    coordinator = fixture.store.add_agent(
        fixture.controller, "Coordinator", role="coordinator", group_id="group"
    )
    worker = fixture.store.claim(fixture.controller, fixture.member.agent_id, "other")
    evidence = fixture.store.capture_result(
        fixture.controller, worker, "Source", "Finding", "source"
    )
    with pytest.raises(SwarmAccessError, match="Evidence"):
        submit(
            fixture,
            coordinator,
            "request_reconciliation",
            task_ids=["own"],
            summary="Claim",
            evidence=[evidence["id"]],
        )
    with pytest.raises(ValueError, match="unsupported"):
        submit(
            fixture, coordinator, "report_summary", task_ids=["own"], summary="Claim", role="lead"
        )


def test_revoked_coordinator_request_is_durably_rejected(tmp_path):
    fixture = running_team(tmp_path)
    coordinator = fixture.store.add_agent(fixture.controller, "Coordinator", role="coordinator")
    submit(fixture, coordinator, "report_summary", task_ids=["work"], summary="Pending report")
    fixture.store.revoke_member(fixture.controller, coordinator.agent_id)
    result = apply(fixture)[0]
    assert result["state"] == "rejected"
    assert "revoked" in result["outcome"]["reason"]


def test_replan_preserves_unresolved_blocked_branch_and_retry_ceiling(tmp_path):
    fixture = running_team(tmp_path, tasks=[task("work"), task("dependent", dependencies=["work"])])
    worker = fixture.store.claim(fixture.controller, fixture.member.agent_id, "work")
    fixture.store.fail(worker, "Cannot finish", retry=False, controller=fixture.controller)
    submit(fixture, lead(fixture), "replan_blocked", task_ids=["dependent"])
    result = apply(fixture)[0]
    assert result["state"] == "applied"
    assert result["outcome"]["needs_input"]
    assert result["outcome"]["blocked_task_ids"] == ["dependent"]
    assert fixture.store.get_record("tasks", "dependent")["state"] == "blocked"


def test_rate_limit_and_payload_bounds_prevent_unbounded_control_work(tmp_path):
    fixture = running_team(tmp_path)
    actor = lead(fixture)
    for index in range(30):
        enqueue(
            fixture.store,
            actor,
            "report_summary",
            {"reason": "Report", "summary": "Short report", "task_ids": ["work"]},
            str(index),
        )
    with pytest.raises(SwarmConflictError, match="rate limit"):
        enqueue(
            fixture.store,
            actor,
            "report_summary",
            {"reason": "Report", "summary": "Short report", "task_ids": ["work"]},
            "extra",
        )
    with pytest.raises(ValueError):
        submit(fixture, actor, "report_summary", summary="x" * 2001, task_ids=["work"])
    with pytest.raises(ValueError):
        drain(fixture.store, fixture.controller, limit=33)
