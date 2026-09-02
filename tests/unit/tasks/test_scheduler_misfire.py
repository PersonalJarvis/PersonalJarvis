"""BUG-212 — recurring automations stay on their grid and never catch up.

Two defects in the recurring (``every``) path:

* ``hydrate()`` re-inserted a stale ``due_at_ns`` as-is, so a daily 07:30
  automation the app slept through fired the moment the app came up;
* ``_rearm_every`` re-armed at ``now + interval`` instead of the wall-clock
  grid, so that one late firing shifted the schedule permanently.

One-shot triggers keep their ADR-0005 H9 behaviour: a reminder due while the
app was down still fires at boot — there is no next occurrence to skip to.
"""
from __future__ import annotations

import asyncio
import heapq
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.misfire import MISFIRE_GRACE_NS
from jarvis.tasks.scheduler import TaskScheduler, parse_iso_timestamp_to_ns
from jarvis.tasks.schema import SpeakAction, TaskSpec, TriggerAtTime, TriggerEvery
from jarvis.tasks.store import TaskStore

_DAY_S = 86_400.0
_DAY_NS = int(_DAY_S * 1e9)


class RecordingRunner:
    def __init__(self) -> None:
        self.dispatched: list[str] = []
        self.gate = asyncio.Event()

    async def run(self, task_id: str, *_a: Any, **_k: Any) -> None:
        self.dispatched.append(task_id)
        self.gate.set()


@pytest.fixture
async def store(tmp_path: Path):
    s = TaskStore(tmp_path / "misfire.db")
    await s.init()
    try:
        yield s
    finally:
        await s.close()


def _daily_at(anchor: datetime) -> TaskSpec:
    return TaskSpec(
        title="Morning Brief",
        trigger=TriggerEvery(
            interval_seconds=_DAY_S,
            start_at=anchor.isoformat(timespec="seconds"),
        ),
        action=SpeakAction(text="brief"),
    )


async def _missed_steps(store: TaskStore, task_id: str) -> list[dict[str, Any]]:
    conn = store._require_conn()
    cur = await conn.execute(
        "SELECT payload_json FROM task_steps WHERE task_id = ? ORDER BY seq", (task_id,)
    )
    rows = await cur.fetchall()
    await cur.close()
    payloads = [json.loads(r["payload_json"]) for r in rows]
    return [p for p in payloads if p.get("event") == "missed"]


# ----------------------------------------------------------------------
# hydrate — the boot path
# ----------------------------------------------------------------------

async def test_hydrate_skips_a_stale_daily_slot_to_the_next_grid_point(
    store: TaskStore,
) -> None:
    """The 07:30 automation the app slept through does not fire at 15:04."""
    now = datetime.now(UTC).replace(microsecond=0)
    # Daily grid anchored days ago; the slot two days in is nine hours old.
    anchor = now - timedelta(days=2, hours=9)
    spec = _daily_at(anchor)
    task_id = await store.insert(spec)
    stale_due = parse_iso_timestamp_to_ns(anchor.isoformat()) + 2 * _DAY_NS
    assert stale_due < parse_iso_timestamp_to_ns(now.isoformat()) - MISFIRE_GRACE_NS
    await store.set_next_due(task_id, stale_due)

    runner = RecordingRunner()
    scheduler = TaskScheduler(store=store, bus=EventBus(), runner=runner)
    await scheduler.hydrate()

    assert scheduler._heap, "the task is still scheduled"
    due_ns, tid = scheduler._heap[0]
    assert tid == task_id
    expected = parse_iso_timestamp_to_ns(anchor.isoformat()) + 3 * _DAY_NS
    assert due_ns == expected, "next occurrence on the anchor's grid, not now+interval"
    assert due_ns > parse_iso_timestamp_to_ns(now.isoformat())

    row = await store.get(task_id)
    assert row is not None and row["due_at_ns"] == expected
    missed = await _missed_steps(store, task_id)
    assert len(missed) == 1
    assert missed[0]["due_at_ns"] == stale_due
    assert missed[0]["next_due_at_ns"] == expected
    assert runner.dispatched == []


async def test_hydrate_keeps_a_slot_inside_the_grace_window(
    store: TaskStore,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    spec = _daily_at(now - timedelta(days=2))
    task_id = await store.insert(spec)
    recent = parse_iso_timestamp_to_ns(now.isoformat()) - MISFIRE_GRACE_NS // 2
    await store.set_next_due(task_id, recent)

    scheduler = TaskScheduler(store=store, bus=EventBus(), runner=RecordingRunner())
    await scheduler.hydrate()

    assert scheduler._heap[0] == (recent, task_id)
    assert await _missed_steps(store, task_id) == []


async def test_hydrate_still_fires_a_stale_one_shot_reminder(
    store: TaskStore,
) -> None:
    """ADR-0005 H9: a reminder must survive a crash, however late."""
    past = datetime.now(UTC) - timedelta(hours=3)
    spec = TaskSpec(
        title="reminder",
        trigger=TriggerAtTime(iso_timestamp=past.isoformat(timespec="seconds")),
        action=SpeakAction(text="x"),
    )
    task_id = await store.insert(spec)

    runner = RecordingRunner()
    scheduler = TaskScheduler(store=store, bus=EventBus(), runner=runner)
    await scheduler.hydrate()
    await scheduler._drain_due_tasks(parse_iso_timestamp_to_ns(datetime.now(UTC).isoformat()))
    await asyncio.wait_for(runner.gate.wait(), timeout=1.0)

    assert runner.dispatched == [task_id]


# ----------------------------------------------------------------------
# re-arm — the running path
# ----------------------------------------------------------------------

async def test_rearm_stays_on_the_wall_clock_grid(store: TaskStore) -> None:
    """Firing five minutes late must not move tomorrow's slot by five minutes."""
    now = datetime.now(UTC).replace(microsecond=0)
    anchor = now - timedelta(minutes=5)
    spec = _daily_at(anchor)
    task_id = await store.insert(spec)
    anchor_ns = parse_iso_timestamp_to_ns(anchor.isoformat())
    await store.set_next_due(task_id, anchor_ns)

    runner = RecordingRunner()
    scheduler = TaskScheduler(store=store, bus=EventBus(), runner=runner)
    await scheduler.hydrate()
    await scheduler._drain_due_tasks(anchor_ns + 5 * 60 * 1_000_000_000)
    await asyncio.wait_for(runner.gate.wait(), timeout=1.0)

    assert runner.dispatched == [task_id]
    assert scheduler._heap[0] == (anchor_ns + _DAY_NS, task_id)
    row = await store.get(task_id)
    assert row is not None and row["due_at_ns"] == anchor_ns + _DAY_NS


async def test_drain_skips_a_slot_the_loop_slept_through(store: TaskStore) -> None:
    """The process lived but did not tick (laptop lid closed): same rule."""
    now = datetime.now(UTC).replace(microsecond=0)
    anchor = now - timedelta(hours=9)
    spec = _daily_at(anchor)
    task_id = await store.insert(spec)
    anchor_ns = parse_iso_timestamp_to_ns(anchor.isoformat())

    runner = RecordingRunner()
    scheduler = TaskScheduler(store=store, bus=EventBus(), runner=runner)
    heapq.heappush(scheduler._heap, (anchor_ns, task_id))
    scheduler._known.add(task_id)

    await scheduler._drain_due_tasks(parse_iso_timestamp_to_ns(now.isoformat()))

    assert runner.dispatched == []
    assert scheduler._heap[0] == (anchor_ns + _DAY_NS, task_id)
    assert len(await _missed_steps(store, task_id)) == 1


async def test_unanchored_task_rearms_one_interval_from_now(store: TaskStore) -> None:
    spec = TaskSpec(
        title="hourly",
        trigger=TriggerEvery(interval_seconds=3600.0),
        action=SpeakAction(text="x"),
    )
    task_id = await store.insert(spec)
    runner = RecordingRunner()
    scheduler = TaskScheduler(store=store, bus=EventBus(), runner=runner)
    now_ns = parse_iso_timestamp_to_ns(datetime.now(UTC).isoformat())
    heapq.heappush(scheduler._heap, (now_ns - 1, task_id))
    scheduler._known.add(task_id)

    await scheduler._drain_due_tasks(now_ns)
    await asyncio.wait_for(runner.gate.wait(), timeout=1.0)

    assert scheduler._heap[0] == (now_ns + 3600 * 1_000_000_000, task_id)
