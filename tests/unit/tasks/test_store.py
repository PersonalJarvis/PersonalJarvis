"""Unit tests for TaskStore — CRUD + startup cleanup + append_step."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.tasks.schema import SpeakAction, TaskSpec, TriggerAfterDelay, TriggerOnEvent
from jarvis.tasks.store import TaskStore


@pytest.fixture
async def store(tmp_path: Path):
    s = TaskStore(tmp_path / "test.db")
    await s.init()
    try:
        yield s
    finally:
        await s.close()


async def test_init_creates_tables(store: TaskStore) -> None:
    # Empty list call should not crash — proves tables exist
    rows = await store.list()
    assert rows == []


async def test_insert_and_get_roundtrip(store: TaskStore) -> None:
    spec = TaskSpec(
        title="Erinnere mich in 5 Minuten",
        trigger=TriggerAfterDelay(delay_seconds=300.0),
        action=SpeakAction(text="Zeit ist um"),
    )
    tid = await store.insert(spec)
    assert tid == str(spec.id)

    task = await store.get(tid)
    assert task is not None
    assert task["state"] == "scheduled"
    assert task["trigger_type"] == "after_delay"
    assert task["due_at_ns"] is not None
    assert task["title"] == "Erinnere mich in 5 Minuten"
    assert task["attempts"] == 0
    assert task["steps"] == []


async def test_insert_on_event_stores_selector(store: TaskStore) -> None:
    spec = TaskSpec(
        title="Reagiere auf Mails",
        trigger=TriggerOnEvent(event_name="MessageSent", filter_expr="role == 'user'"),
        action=SpeakAction(text="Neue Nachricht!"),  # i18n-allow
    )
    tid = await store.insert(spec)
    task = await store.get(tid)
    assert task is not None
    assert task["trigger_type"] == "on_event"
    assert task["event_selector"] == "MessageSent"
    assert task["due_at_ns"] is None


async def test_update_state_records_lifecycle(store: TaskStore) -> None:
    spec = TaskSpec(
        title="T",
        trigger=TriggerAfterDelay(delay_seconds=1.0),
        action=SpeakAction(text="x"),
    )
    tid = await store.insert(spec)

    await store.update_state(tid, "running", increment_attempts=True)
    row = await store.get(tid)
    assert row is not None
    assert row["state"] == "running"
    assert row["started_at_ns"] is not None
    assert row["attempts"] == 1

    await store.update_state(tid, "completed", result={"duration_ms": 42})
    row = await store.get(tid)
    assert row is not None
    assert row["state"] == "completed"
    assert row["finished_at_ns"] is not None
    assert row["result_json"] is not None


async def test_append_step_increments_seq(store: TaskStore) -> None:
    spec = TaskSpec(
        title="T",
        trigger=TriggerAfterDelay(delay_seconds=1.0),
        action=SpeakAction(text="hi"),
    )
    tid = await store.insert(spec)

    seq1 = await store.append_step(tid, "action", {"kind": "speak"})
    seq2 = await store.append_step(tid, "log", {"message": "ok"})
    assert seq1 == 1
    assert seq2 == 2

    task = await store.get(tid)
    assert task is not None
    assert len(task["steps"]) == 2
    assert task["steps"][0]["kind"] == "action"
    assert task["steps"][1]["kind"] == "log"
    assert task["steps"][1]["payload"]["message"] == "ok"


async def test_list_filters_by_state(store: TaskStore) -> None:
    s1 = TaskSpec(title="A", trigger=TriggerAfterDelay(delay_seconds=1),
                  action=SpeakAction(text="a"))
    s2 = TaskSpec(title="B", trigger=TriggerAfterDelay(delay_seconds=1),
                  action=SpeakAction(text="b"))
    tid1 = await store.insert(s1)
    tid2 = await store.insert(s2)
    await store.update_state(tid1, "completed")

    scheduled = await store.list(state_filter="scheduled")
    assert len(scheduled) == 1
    assert scheduled[0]["id"] == tid2

    completed = await store.list(state_filter="completed")
    assert len(completed) == 1
    assert completed[0]["id"] == tid1


async def test_list_filters_by_state_list(store: TaskStore) -> None:
    s = TaskSpec(title="T", trigger=TriggerAfterDelay(delay_seconds=1),
                 action=SpeakAction(text="x"))
    tid = await store.insert(s)
    await store.update_state(tid, "running")

    rows = await store.list(state_filter=["scheduled", "running"])
    assert len(rows) == 1
    assert rows[0]["id"] == tid


async def test_delete_cascades_steps(store: TaskStore) -> None:
    spec = TaskSpec(title="T", trigger=TriggerAfterDelay(delay_seconds=1),
                    action=SpeakAction(text="x"))
    tid = await store.insert(spec)
    await store.append_step(tid, "log", {"a": 1})

    ok = await store.delete(tid)
    assert ok is True
    assert await store.get(tid) is None


async def test_cleanup_interrupted_flips_running_to_interrupted(store: TaskStore) -> None:
    # Drei Tasks: 1x scheduled, 1x running, 1x completed.
    specs = [
        TaskSpec(title=f"T{i}", trigger=TriggerAfterDelay(delay_seconds=1),
                 action=SpeakAction(text="x"))
        for i in range(3)
    ]
    ids = [await store.insert(s) for s in specs]
    await store.update_state(ids[1], "running")
    await store.update_state(ids[2], "completed")

    affected = await store.cleanup_interrupted()
    assert affected == 1

    task = await store.get(ids[1])
    assert task is not None
    assert task["state"] == "interrupted"
    assert task["last_error"] == "App exit detected"
    assert task["finished_at_ns"] is not None


async def test_get_spec_deserialises(store: TaskStore) -> None:
    spec = TaskSpec(
        title="Hallo",
        trigger=TriggerAfterDelay(delay_seconds=2.0),
        action=SpeakAction(text="moin"),
    )
    tid = await store.insert(spec)

    recovered = await store.get_spec(tid)
    assert recovered is not None
    assert recovered.title == "Hallo"
    assert isinstance(recovered.trigger, TriggerAfterDelay)
    assert recovered.trigger.delay_seconds == 2.0
