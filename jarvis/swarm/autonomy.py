"""Bounded goal planning stages, immutable batch barriers and gated delivery."""

from __future__ import annotations

import json
from typing import Any

from jarvis.core.swarm_types import SwarmActor, SwarmController, TaskSpec

from .store import SwarmAccessError, SwarmConflictError, TeamStore, _json, _validate_graph

PLAN = "__swarm_plan"
DELIVERY = "__swarm_delivery"
GATE = "__swarm_supervise_goal_"
BARRIER = "__swarm_supervise_barrier_"
MAX_STAGES = 256


def _state(team: dict[str, Any]) -> dict[str, Any]:
    return dict(team.get("checkpoint", {}).get("autonomy") or {})


def _save(store: TeamStore, connection: Any, state: dict[str, Any], source: str) -> None:
    team = store._team(connection)
    checkpoint = dict(team.get("checkpoint") or {})
    checkpoint["autonomy"] = state
    store._checkpoint(connection, team, checkpoint, source=source)


def _edges(store: TeamStore, connection: Any, task_id: str, dependencies: list[str]) -> None:
    task = store._task(connection, task_id)
    if task["state"] != "ready" or task["fence"]:
        raise SwarmConflictError("Delivery has already begun; its accepted scope cannot change")
    task["dependencies"] = list(dict.fromkeys(dependencies))
    connection.execute("DELETE FROM dependencies WHERE task_id=?", (task_id,))
    connection.executemany(
        "INSERT INTO dependencies VALUES (?,?)", [(task_id, item) for item in task["dependencies"]]
    )
    store._save_task(connection, task)


def _check_graph(connection: Any) -> None:
    _validate_graph(
        [],
        {
            row["id"]: json.loads(row["record"])["dependencies"]
            for row in connection.execute("SELECT id,record FROM tasks")
        },
    )


def validate_delivery(store: TeamStore, connection: Any) -> None:
    state = _state(store._team(connection))
    if (state and state.get("decision") != "deliver") or _open_work(connection):
        raise SwarmConflictError("The persistent lead has not accepted the final goal frontier")
    if connection.execute(
        "SELECT 1 FROM decisions WHERE json_extract(record,'$.kind')='control_request' "
        "AND json_extract(record,'$.state')='pending' AND json_extract(record,'$.operation') "
        "IN ('add_tasks','advance_goal','request_reconciliation') LIMIT 1"
    ).fetchone():
        raise SwarmConflictError("Pending planning changes must settle before delivery")


def _close_admin(
    store: TeamStore, connection: Any, task: dict[str, Any], summary: str, evidence: list[str]
) -> None:
    task.update(state="succeeded", result=summary[:4000], evidence=evidence[:30], reason="")
    store._save_task(connection, task)
    connection.execute(
        "UPDATE attempts SET state='succeeded',finished_at=? WHERE task_id=? "
        "AND fence=? AND state='running'",
        (store.clock(), task["id"], task["fence"]),
    )
    if task["owner_id"]:
        store._idle_agent(connection, task["owner_id"])
    store._event(
        connection,
        "planning.stage_completed",
        "Lead planning state advanced without task credit",
        agent_id=task["owner_id"],
        task_id=task["id"],
    )


def _insert_batch(
    store: TeamStore, connection: Any, state: dict[str, Any], specs: list[TaskSpec], source: str
) -> str:
    existing = {
        row["id"]: json.loads(row["record"])["dependencies"]
        for row in connection.execute("SELECT id,record FROM tasks")
    }
    for spec in specs:
        if not spec.dependencies:
            spec.dependencies = [source]
        elif source not in spec.dependencies:
            spec.dependencies.append(source)
    _validate_graph(specs, existing)
    store._insert_tasks(connection, specs)
    batch = int(state.get("batches", 0)) + 1
    barrier_id = BARRIER + str(batch)
    dependencies = [spec.id for spec in specs]
    if state.get("barrier_id"):
        dependencies.append(state["barrier_id"])
    store._insert_tasks(
        connection,
        [
            TaskSpec(
                id=barrier_id,
                title=f"Consolidate accepted batch {batch}",
                description="Runtime barrier: retain accepted references without awarding credit.",
                acceptance="Every batch dependency has been independently accepted.",
                dependencies=dependencies,
                domain="coordination",
                milestone="planning",
                difficulty=1,
            )
        ],
    )
    state.update(batches=batch, barrier_id=barrier_id)
    return barrier_id


def _insert_gate(
    store: TeamStore, connection: Any, state: dict[str, Any], source: str, expand: bool
) -> None:
    stage = int(state.get("stage", 0)) + 1
    if stage > MAX_STAGES:
        raise SwarmConflictError("The bounded planning stage limit is reached; review the goal")
    gate_id = GATE + str(stage)
    dependencies = [source] if expand else list(dict.fromkeys([source, state["barrier_id"]]))
    store._insert_tasks(
        connection,
        [
            TaskSpec(
                id=gate_id,
                title="Plan the next useful goal stage"
                if expand
                else "Review accepted goal progress",
                description="Persistent lead planning gate for the original authorized goal.",
                acceptance="Continue useful work, deliver the result, or state a concrete blocker.",
                dependencies=dependencies,
                domain="planning",
                milestone="planning",
                difficulty=1,
                priority=9,
            )
        ],
    )
    state.update(stage=stage, gate_id=gate_id, expanding=expand, decision="pending")
    _edges(store, connection, DELIVERY, [state["barrier_id"], gate_id])


def _open_work(connection: Any) -> int:
    return int(
        connection.execute(
            "SELECT count(*) FROM tasks WHERE substr(id,1,8)<>'__swarm_' AND state<>'succeeded'"
        ).fetchone()[0]
    )


def _can_expand(team: dict[str, Any], connection: Any, remaining: bool, capacity: int) -> bool:
    capacity = min(int(team["limits"]["concurrency"]), capacity)
    return remaining and _open_work(connection) < max(1, capacity - 1)


def install(
    store: TeamStore,
    controller: SwarmController,
    actor: SwarmActor,
    specs: list[TaskSpec],
    remaining: bool,
    artifact_id: str,
    effective_capacity: int = 32,
) -> None:
    """Initial work, persistent stage state and the delivery fence commit together."""
    with store._tx(write=True) as connection:
        store._controller(connection, controller)
        store._actor(connection, actor, writing=True)
        team = store._team(connection)
        if actor.agent_id != team["lead_id"] or actor.task_id != PLAN or _state(team):
            raise SwarmConflictError("The original planning gate is no longer current")
        install_in(store, connection, specs, remaining, [artifact_id], effective_capacity)


def install_in(
    store: TeamStore,
    connection: Any,
    specs: list[TaskSpec],
    remaining: bool,
    evidence: list[str],
    effective_capacity: int = 32,
) -> None:
    """Install a validated plan in the trusted caller's existing transaction."""
    team = store._team(connection)
    if _state(team):
        raise SwarmConflictError("The initial plan has already been installed")
    state: dict[str, Any] = {
        "stage": 0,
        "batches": 0,
        "summary": "",
        "capacity": effective_capacity,
    }
    _insert_batch(store, connection, state, specs, PLAN)
    store._insert_tasks(
        connection,
        [
            TaskSpec(
                id=DELIVERY,
                title="Verify and deliver the complete result",
                description=team["goal"],
                acceptance=team.get("acceptance") or team["goal"],
                domain="planning",
                milestone="delivery",
                difficulty=4,
            )
        ],
    )
    _insert_gate(
        store,
        connection,
        state,
        PLAN,
        _can_expand(team, connection, remaining, effective_capacity),
    )
    _check_graph(connection)
    _close_admin(
        store,
        connection,
        store._task(connection, PLAN),
        "A validated initial batch is ready",
        evidence,
    )
    _save(store, connection, state, "planning.initial")


def principal(store: TeamStore, connection: Any, task: dict[str, Any]) -> str:
    team = store._team(connection)
    state = _state(team)
    if task["id"] == state.get("gate_id"):
        return team["lead_id"]
    if task["id"].startswith(BARRIER):
        suffix = task["id"][len(BARRIER) :]
        if suffix.isdecimal() and 1 <= int(suffix) <= state.get("batches", 0):
            return team["lead_id"]
    raise SwarmAccessError("The administrative goal stage is not bound to this lead")


def close_barrier(store: TeamStore, controller: SwarmController, actor: SwarmActor) -> None:
    with store._tx(write=True) as connection:
        store._controller(connection, controller)
        store._actor(connection, actor, writing=True)
        task = store._task(connection, actor.task_id)
        if (
            not actor.task_id.startswith(BARRIER)
            or principal(store, connection, task) != actor.agent_id
        ):
            raise SwarmAccessError("Only the bound lead can close a batch barrier")
        records = [store._task(connection, item) for item in task["dependencies"]]
        if any(item["state"] != "succeeded" for item in records):
            raise SwarmConflictError("A batch still has unfinished work")
        refs = list(dict.fromkeys(ref for item in records for ref in item["evidence"]))
        summary = _json(
            {
                "accepted_batch": [
                    item["id"] for item in records if not item["id"].startswith(BARRIER)
                ],
                "prior_batch": next(
                    (item["id"] for item in records if item["id"].startswith(BARRIER)), None
                ),
            }
        )
        _close_admin(store, connection, task, summary, refs[-30:])


def context(
    store: TeamStore, controller: SwarmController, actor: SwarmActor, effective_capacity: int = 32
) -> dict[str, Any]:
    with store._tx(write=True) as connection:
        store._controller(connection, controller)
        store._actor(connection, actor, writing=True)
        team = store._team(connection)
        task = store._task(connection, actor.task_id)
        if principal(store, connection, task) != actor.agent_id or not actor.task_id.startswith(
            GATE
        ):
            raise SwarmAccessError("Only the current lead stage can plan")
        state = _state(team)
        rows = connection.execute(
            "SELECT record FROM tasks WHERE substr(id,1,8)<>'__swarm_' "
            "ORDER BY json_extract(record,'$.updated_at') DESC,id LIMIT 32"
        ).fetchall()
        work = [json.loads(row[0]) for row in rows]
        capacity = min(int(team["limits"]["concurrency"]), effective_capacity)
        if state.get("capacity") != capacity:
            state["capacity"] = capacity
            _save(store, connection, state, "planning.capacity")
        return {
            "goal": team["goal"],
            "acceptance": team.get("acceptance") or team["goal"],
            "authorized_policy": team["policy"],
            "limits": team["limits"],
            "stage": state["stage"],
            "stage_task_id": task["id"],
            "prior_summary": state.get("summary", ""),
            "unfinished_tasks": _open_work(connection),
            "effective_capacity": capacity,
            "remaining_tokens": str(
                max(
                    0,
                    int(team["limits"]["token_budget"])
                    - int(team["tokens_used"])
                    - int(team["tokens_reserved"]),
                )
            ),
            "recent_tasks": [
                {key: item[key] for key in ("id", "state", "title", "domain", "milestone")}
                | {
                    "result_excerpt": item["result"][:300],
                    "acceptance_excerpt": item["acceptance"][:300],
                    "evidence": item["evidence"][:4],
                }
                for item in work
            ],
            "task_schema": TaskSpec.model_json_schema(),
        }


def advance(store: TeamStore, connection: Any, record: dict[str, Any]) -> dict[str, Any]:
    """Apply a lead stage intent within the controller's idempotent transaction."""
    team = store._team(connection)
    state = _state(team)
    payload = record["payload"]
    task_id = payload["stage_task_id"]
    if (
        task_id != state.get("gate_id")
        or task_id != record["actor_task_id"]
        or record["actor_id"] != team["lead_id"]
    ):
        raise SwarmConflictError("This planning decision belongs to an obsolete stage")
    gate = store._task(connection, task_id)
    if (
        gate["state"] != "running"
        or gate["fence"] != record.get("actor_task_fence")
        or gate["owner_id"] != record["actor_id"]
    ):
        raise SwarmConflictError("This planning decision belongs to an obsolete attempt")
    decision = payload["decision"]
    if decision == "deliver" and _open_work(connection):
        raise SwarmConflictError("Delivery cannot bypass unfinished or failed work")
    state["summary"] = payload["summary"]
    if decision == "continue":
        specs = [TaskSpec.model_validate(item) for item in payload["tasks"]]
        _insert_batch(store, connection, state, specs, task_id)
        _insert_gate(
            store,
            connection,
            state,
            task_id,
            _can_expand(team, connection, payload["remaining_decomposition"], state["capacity"]),
        )
    elif decision == "blocked":
        _insert_gate(store, connection, state, task_id, False)
    else:
        state.update(decision="deliver", remaining_decomposition=False)
    _check_graph(connection)
    _close_admin(store, connection, gate, payload["summary"], [])
    _save(store, connection, state, "planning.advance")
    if decision == "blocked":
        store._transition(connection, "blocked", None, payload["reason"])
    return {
        "affected_attempts": [],
        "decision": decision,
        "stage_task_id": task_id,
        "task_ids": [item["id"] for item in payload["tasks"]],
        "next_gate": state.get("gate_id"),
    }


def extend_delivery(store: TeamStore, connection: Any, specs: list[TaskSpec], source: str) -> bool:
    """Attach incremental lead work to the goal's pending completion chain."""
    team = store._team(connection)
    state = _state(team)
    delivery_row = connection.execute("SELECT record FROM tasks WHERE id=?", (DELIVERY,)).fetchone()
    if delivery_row is not None:
        delivery_task = json.loads(delivery_row[0])
        if delivery_task["state"] != "ready" or delivery_task["fence"]:
            raise SwarmConflictError("Delivery has already begun; its accepted scope cannot change")
    if not state:
        delivery = connection.execute("SELECT record FROM tasks WHERE id=?", (DELIVERY,)).fetchone()
        if delivery is None:
            return False
        # Preserve earlier plans while closing their frozen final-dependency gap.
        # New goal plans use the bounded barrier chain instead.
        for spec in specs:
            if source not in spec.dependencies:
                spec.dependencies.append(source)
        existing = {
            row["id"]: json.loads(row["record"])["dependencies"]
            for row in connection.execute("SELECT id,record FROM tasks")
        }
        _validate_graph(specs, existing)
        store._insert_tasks(connection, specs)
        _edges(
            store,
            connection,
            DELIVERY,
            [*json.loads(delivery[0])["dependencies"], *(spec.id for spec in specs)],
        )
        _check_graph(connection)
        return True
    gate = store._task(connection, state["gate_id"])
    if gate["state"] != "ready" or gate["fence"]:
        raise SwarmConflictError("Wait for the active planning stage before extending its scope")
    _insert_batch(store, connection, state, specs, source)
    # The next frontier review must observe the new accepted batch as well.
    _edges(
        store,
        connection,
        gate["id"],
        list(dict.fromkeys([*gate["dependencies"], state["barrier_id"]])),
    )
    _edges(store, connection, DELIVERY, [state["barrier_id"], gate["id"]])
    _check_graph(connection)
    _save(store, connection, state, "planning.incremental")
    return True
