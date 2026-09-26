"""Bounded transactional dispatch shared by SQLite and PostgreSQL stores."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from jarvis.core.swarm_types import SwarmActor, SwarmController

if TYPE_CHECKING:
    from .store import TeamStore


@dataclass(frozen=True)
class DispatchClaim:
    actor: SwarmActor
    task_json: str
    deadline: float
    mode: str
    source_bound: bool


@dataclass(frozen=True)
class DispatchBatch:
    claims: tuple[DispatchClaim, ...]
    sources: tuple[tuple[str, str], ...]
    ready_offset: int
    has_ready: bool
    team_json: str


def claim_batch(
    store: TeamStore,
    controller: SwarmController,
    *,
    limit: int,
    worker_slots: int,
    ready_offset: int,
    source_ids: frozenset[str],
    excluded_tasks: frozenset[str],
) -> DispatchBatch:
    """Commit a small fair round before any inference or source-profile lookup.

    Unknown specialist sources are returned unclaimed for host validation. Their
    credentials never cross into another team; a subsequent round revalidates the
    current assignment, membership generation, capability policy and all fences.
    """
    from .control_requests import (
        _assignment_for,
        _block_unavailable_assignment,
        _ready_work,
    )
    from .store import SwarmBudgetError, SwarmConflictError, _json
    from .supervision import _ready_turn, principal_for

    if not 1 <= limit <= 8 or not 0 <= worker_slots <= limit or ready_offset < 0:
        raise ValueError("A dispatch round must contain one to eight bounded slots")
    claims: list[DispatchClaim] = []
    sources: dict[str, str] = {}
    with store._tx(write=True) as connection:
        store._controller(connection, controller)
        team = store._team(connection)
        if team["state"] != "running":
            raise SwarmConflictError("Only a running team can dispatch")
        running = connection.execute(
            "SELECT count(*),coalesce(sum(CASE WHEN agent_id<>? THEN 1 ELSE 0 END),0) "
            "FROM attempts WHERE state='running'",
            (team["lead_id"],),
        ).fetchone()
        slots = min(limit, max(0, team["limits"]["concurrency"] - running[0]))
        nonlead = running[1]
        if team["limits"]["concurrency"] > 1:
            worker_slots = min(worker_slots, max(0, team["limits"]["concurrency"] - 1 - nonlead))
        tasks = _ready_work(store, connection, ready_offset)
        attention = _ready_turn(store, connection)
        if attention is not None:
            tasks = [attention, *[task for task in tasks if task["id"] != attention["id"]]]
        has_ready = bool(tasks)
        next_offset = ready_offset + 32 if len(tasks) >= 32 else 0
        for task in tasks:
            if task["id"] in excluded_tasks:
                continue
            if len(claims) >= slots:
                break
            source_bound = False
            administrative = task["id"].startswith("__swarm_")
            if administrative:
                agent_id = principal_for(store, connection, task)
                if agent_id != team["lead_id"] and worker_slots == 0:
                    continue
                if (
                    connection.execute(
                        "SELECT 1 FROM agents WHERE id=? AND active=1 AND state='idle'", (agent_id,)
                    ).fetchone()
                    is None
                ):
                    continue
            else:
                if worker_slots == 0:
                    continue
                assignment = _assignment_for(store, connection, task["id"])
                if assignment is not None:
                    if not assignment["eligible"]:
                        _block_unavailable_assignment(
                            store, connection, task["id"], assignment["agent_id"]
                        )
                    if not (assignment["eligible"] and assignment["idle"]):
                        continue
                    agents = [assignment["agent"]]
                else:
                    agents = store._agents_for(
                        connection, task["domain"], task["required_tools"], 32
                    )
                agent = None
                for candidate in agents:
                    source_id = candidate.get("source_agent_id")
                    if source_id and source_id not in source_ids:
                        if len(sources) < 32:
                            sources[candidate["id"]] = source_id
                        continue
                    agent = candidate
                    break
                if agent is None and agents:
                    # Validate existing specialists before replacing their useful capacity.
                    continue
                if agent is not None:
                    agent_id = agent["id"]
                    source_bound = bool(agent.get("source_agent_id"))
                else:
                    try:
                        actor = store._add_agent(
                            connection,
                            f"{task['domain'].title()} worker",
                            domain=task["domain"],
                            group_id=task["milestone"],
                        )
                    except SwarmBudgetError:
                        # Identity capacity is reusable execution capacity, not
                        # exhausted inference spend. Preserve earlier claims and
                        # wait for an eligible busy worker instead of stopping it.
                        reusable = connection.execute(
                            "SELECT 1 FROM agents WHERE active=1 AND role='worker' "
                            "AND state IN ('running','waiting') "
                            "AND json_extract(record,'$.domain') IN (?, 'general') LIMIT 1",
                            (task["domain"],),
                        ).fetchone()
                        if reusable is None:
                            task.update(
                                state="blocked",
                                reason="Logical worker limit reached; no eligible worker can "
                                "be reused. Lead or owner action is required.",
                            )
                            store._save_task(connection, task)
                            store._event(
                                connection, "task.blocked", task["reason"], task_id=task["id"]
                            )
                        continue
                    agent_id = actor.agent_id
            actor = store._claim(connection, controller, agent_id, task["id"], store.clock())
            claims.append(
                DispatchClaim(
                    actor,
                    _json(task),
                    (team["started_at"] if team["started_at"] is not None else store.clock())
                    + team["limits"]["runtime_seconds"],
                    team["mode"],
                    source_bound,
                )
            )
            if actor.agent_id != team["lead_id"]:
                worker_slots -= 1
        if claims:
            next_offset = 0
    return DispatchBatch(tuple(claims), tuple(sources.items()), next_offset, has_ready, _json(team))
