"""WorkLedger: one read-only view over missions, tasks, quests and workflows.

The ledger reads the REAL stores (temp files here) through their existing read
methods and must leave every byte of them unchanged. A parked mission
(``WAITING_CAPACITY``) is its own status and needs the person's attention.
"""

from __future__ import annotations

import json
import sqlite3
import typing
from pathlib import Path
from typing import Any

import pytest

from jarvis.missions.manager import MissionManager
from jarvis.missions.state_machine import MissionState
from jarvis.ops.ledger import (
    MISSION_STATUS,
    QUEST_STATUS,
    TASK_STATUS,
    WORK_STATUSES,
    WORKFLOW_RUN_STATUS,
    WorkLedger,
)
from jarvis.society.events import QuestState
from jarvis.society.quests import QuestRecord
from jarvis.tasks.schema import TASK_STATES, AgentAction, TaskSpec, TriggerEvery
from jarvis.tasks.store import TaskStore
from jarvis.workflows.schema import (
    CronTrigger,
    ManualTrigger,
    SpeakStep,
    WorkflowDef,
    WorkflowRunState,
)
from jarvis.workflows.store import WorkflowStore

# --- Status mapping parity: every state of every source is mapped -------------


def test_every_mission_state_is_mapped() -> None:
    assert set(MISSION_STATUS) == {s.value for s in MissionState}
    assert MISSION_STATUS["WAITING_CAPACITY"] == "waiting_capacity"


def test_every_task_state_is_mapped() -> None:
    assert set(TASK_STATUS) == set(TASK_STATES)


def test_every_quest_state_is_mapped() -> None:
    assert set(QUEST_STATUS) == {s.value for s in QuestState}


def test_every_workflow_run_state_is_mapped() -> None:
    assert set(WORKFLOW_RUN_STATUS) == set(typing.get_args(WorkflowRunState))


def test_every_mapping_targets_a_ledger_status() -> None:
    for mapping in (MISSION_STATUS, TASK_STATUS, QUEST_STATUS, WORKFLOW_RUN_STATUS):
        assert set(mapping.values()) <= set(WORK_STATUSES)


# --- Real stores ---------------------------------------------------------------


class FakeQuests:
    """The read side of ``society.quests.Quests`` (real QuestRecord rows)."""

    def __init__(self, records: list[QuestRecord]) -> None:
        self.records = records
        self.calls: list[str] = []

    async def list(self, *, state: Any = None, limit: int = 200) -> list[QuestRecord]:
        self.calls.append("list")
        return self.records[:limit]


def _quest(quest_id: str, state: str, **result: Any) -> QuestRecord:
    return QuestRecord.from_row(
        {
            "quest_id": quest_id,
            "title": f"Quest {quest_id}",
            "text": "the five most important mails",
            "state": state,
            "trace_id": f"trace-{quest_id}",
            "agent_id": "mailbox",
            "result_json": json.dumps(result) if result else None,
            "created_ms": 1_000,
            "updated_ms": 2_000,
        }
    )


@pytest.fixture
async def world(tmp_path: Path):
    missions = MissionManager(tmp_path / "missions.db")
    await missions.start()
    tasks = TaskStore(tmp_path / "tasks.db")
    await tasks.init()
    workflows = WorkflowStore(tmp_path / "workflows.sqlite")
    await workflows.init()

    parked = await missions.dispatch(prompt="Write the quarterly report")
    await missions.transition_state(parked, MissionState.RUNNING, reason="test")
    await missions.transition_state(parked, MissionState.WAITING_CAPACITY, reason="quota")
    running = await missions.dispatch(prompt="Refactor the parser")
    await missions.transition_state(running, MissionState.RUNNING, reason="test")
    await missions.dispatch(prompt="Queued mission")

    digest = await tasks.insert(
        TaskSpec(
            title="Morning digest",
            trigger=TriggerEvery(interval_seconds=86400),
            action=AgentAction(prompt="Summarise my day."),
        )
    )
    await tasks.update_state(digest, "scheduled")
    done_task = await tasks.insert(
        TaskSpec(
            title="Old one",
            trigger=TriggerEvery(interval_seconds=3600),
            action=AgentAction(prompt="x"),
        )
    )
    await tasks.update_state(done_task, "completed")

    manual = WorkflowDef(name="Inbox sweep", trigger=ManualTrigger(), steps=(SpeakStep(text="hi"),))
    await workflows.upsert_workflow(manual)
    run_id = await workflows.create_run(str(manual.id), trigger="cron")
    await workflows.update_run_state(run_id, "missed")
    cron = WorkflowDef(
        name="Weekly review",
        trigger=CronTrigger(expression="0 9 * * 1"),
        steps=(SpeakStep(text="hi"),),
    )
    await workflows.upsert_workflow(cron)
    await workflows.set_next_run(str(cron.id), 5_000_000_000_000)

    quests = FakeQuests(
        [
            _quest("q1", "running"),
            _quest("q2", "assigned", status="waiting", reason="agent_busy"),
            _quest("q3", "done"),
        ]
    )
    try:
        yield {
            "missions": missions,
            "tasks": tasks,
            "workflows": workflows,
            "quests": quests,
            "ids": {"parked": parked, "running": running, "digest": digest},
            "dbs": [tmp_path / "missions.db", tmp_path / "tasks.db", tmp_path / "workflows.sqlite"],
        }
    finally:
        await workflows.close()
        await tasks.close()
        await missions.stop()


def _ledger(world: dict[str, Any]) -> WorkLedger:
    return WorkLedger(
        missions=lambda: world["missions"],
        tasks=lambda: world["tasks"],
        quests=lambda: world["quests"],
        workflows=lambda: world["workflows"],
    )


def _dump(db: Path) -> dict[str, list[tuple[Any, ...]]]:
    con = sqlite3.connect(db)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        # Table names come from sqlite_master of a temp test file, not from input.
        query = 'SELECT * FROM "{}"'
        return {t: sorted(map(tuple, con.execute(query.format(t)))) for t in tables}
    finally:
        con.close()


async def test_snapshot_normalises_every_source(world: dict[str, Any]) -> None:
    snap = await _ledger(world).snapshot()
    by = {(i.source, i.id): i for i in snap.items}
    ids = world["ids"]

    parked = by[("mission", ids["parked"])]
    assert (parked.status, parked.source_state) == ("waiting_capacity", "WAITING_CAPACITY")
    assert parked.needs_attention and parked.attention_reason == "capacity_decision"
    assert by[("mission", ids["running"])].status == "running"
    assert by[("task", ids["digest"])].status == "scheduled"
    assert by[("task", ids["digest"])].title == "Morning digest"
    assert by[("quest", "q1")].status == "running"
    assert (by[("quest", "q2")].status, by[("quest", "q2")].detail) == ("waiting", "agent_busy")
    assert by[("quest", "q3")].status == "done"
    workflow = {i.title: i for i in snap.items if i.source == "workflow"}
    assert workflow["Inbox sweep"].status == "skipped"  # a missed cron slot
    assert workflow["Weekly review"].status == "scheduled"
    assert workflow["Weekly review"].due_ms == 5_000_000

    # The parked mission is the one thing that needs the person: listed first.
    assert snap.items[0].id == ids["parked"]
    data = snap.to_dict()
    assert data["needs_attention"] == 1
    assert data["counts"]["waiting_capacity"] == 1
    assert {s["source"]: s["state"] for s in data["sources"]} == dict.fromkeys(
        ("mission", "task", "quest", "workflow"), "ok"
    )


async def test_reading_changes_no_store(world: dict[str, Any]) -> None:
    before = [_dump(db) for db in world["dbs"]]
    events_before = await world["missions"].store.events_since(0)

    await _ledger(world).snapshot()
    await _ledger(world).snapshot(include_finished=False, sources=["mission", "task"])

    assert [_dump(db) for db in world["dbs"]] == before
    assert await world["missions"].store.events_since(0) == events_before
    assert world["quests"].calls == ["list"]  # only the read method


async def test_include_finished_false_keeps_open_work(world: dict[str, Any]) -> None:
    snap = await _ledger(world).snapshot(include_finished=False)
    statuses = {i.status for i in snap.items}
    assert statuses.isdisjoint({"done", "failed", "cancelled", "skipped"})
    assert "waiting_capacity" in statuses


async def test_source_filter(world: dict[str, Any]) -> None:
    snap = await _ledger(world).snapshot(sources=["task"])
    assert {i.source for i in snap.items} == {"task"}
    assert [s.source for s in snap.sources] == ["task"]


# --- Unavailable and broken sources ---------------------------------------------


class BrokenTasks:
    async def list(self, **_: Any) -> list[dict[str, Any]]:
        raise RuntimeError("database is locked")


async def test_a_missing_or_broken_source_never_hides_the_others(
    world: dict[str, Any],
) -> None:
    ledger = WorkLedger(
        missions=lambda: world["missions"],
        tasks=lambda: BrokenTasks(),
        quests=lambda: None,  # society runtime not started: not started by reading
        workflows=None,
    )
    snap = await ledger.snapshot()
    reports = {s.source: s for s in snap.sources}
    assert reports["task"].state == "error"
    assert reports["task"].message == "RuntimeError"  # no store text reaches the API
    assert reports["quest"].state == "unavailable"
    assert reports["workflow"].state == "unavailable"
    assert reports["mission"].state == "ok"
    assert {i.source for i in snap.items} == {"mission"}


async def test_an_unknown_source_state_is_shown_not_dropped() -> None:
    class Tasks:
        async def list(self, **_: Any) -> list[dict[str, Any]]:
            return [{"id": "t1", "state": "from-the-future", "title": "New"}]

    snap = await WorkLedger(tasks=lambda: Tasks()).snapshot(sources=["task"])
    assert [(i.id, i.source_state) for i in snap.items] == [("t1", "from-the-future")]
