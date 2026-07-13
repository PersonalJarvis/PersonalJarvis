"""Runtime tests for the recurring (`every`) trigger and agent action.

Covers the three pieces the recurring schedule needs end-to-end:
  - Store: persists `every` with a computed/anchored due time + set_next_due.
  - Scheduler: re-arms an `every` task after each dispatch; one-shot triggers
    do NOT re-arm.
  - Runner: an `every` task returns to `scheduled` (not `completed`) so it
    survives a restart and keeps firing.
  - Store: migrates a legacy DB whose trigger_type CHECK predates `every`.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import aiosqlite
import pytest

from jarvis.core.bus import EventBus
from jarvis.tasks.runner import TaskRunner
from jarvis.tasks.scheduler import TaskScheduler, parse_iso_timestamp_to_ns
from jarvis.tasks.schema import (
    AgentAction,
    PluginGrant,
    SpeakAction,
    TaskSpec,
    TriggerAfterDelay,
    TriggerEvery,
)
from jarvis.tasks.store import TaskStore

pytestmark = pytest.mark.phase5

_NS = 1_000_000_000


@pytest.fixture
async def store(tmp_path: Path):
    s = TaskStore(tmp_path / "tasks.db")
    await s.init()
    try:
        yield s
    finally:
        await s.close()


# ---------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------

async def test_store_insert_every_computes_due_from_interval(store: TaskStore):
    before = time.time_ns()
    spec = TaskSpec(
        title="hourly",
        trigger=TriggerEvery(interval_seconds=3600.0),
        action=SpeakAction(text="tick"),
    )
    tid = await store.insert(spec)
    row = await store.get(tid)
    assert row["trigger_type"] == "every"
    assert row["due_at_ns"] >= before + 3600 * _NS - _NS
    assert row["due_at_ns"] <= time.time_ns() + 3600 * _NS + _NS


async def test_store_insert_every_with_start_at_anchors_due(store: TaskStore):
    start = "2099-01-01T00:00:00+00:00"
    spec = TaskSpec(
        title="daily",
        trigger=TriggerEvery(interval_seconds=86400.0, start_at=start),
        action=SpeakAction(text="morning"),
    )
    tid = await store.insert(spec)
    row = await store.get(tid)
    assert row["due_at_ns"] == parse_iso_timestamp_to_ns(start)


async def test_store_set_next_due_updates_due(store: TaskStore):
    spec = TaskSpec(
        title="hourly",
        trigger=TriggerEvery(interval_seconds=3600.0),
        action=SpeakAction(text="tick"),
    )
    tid = await store.insert(spec)
    await store.set_next_due(tid, 424242)
    row = await store.get(tid)
    assert row["due_at_ns"] == 424242


# ---------------------------------------------------------------------
# Scheduler re-arm
# ---------------------------------------------------------------------

class _FakeRunner:
    def __init__(self) -> None:
        self.dispatched: list[str] = []

    async def run(self, task_id: str, **_kwargs: object) -> None:
        self.dispatched.append(task_id)


async def test_scheduler_rearms_every_after_dispatch(store: TaskStore):
    bus = EventBus()
    runner = _FakeRunner()
    sched = TaskScheduler(store=store, bus=bus, runner=runner)
    spec = TaskSpec(
        title="hourly",
        trigger=TriggerEvery(interval_seconds=3600.0),
        action=SpeakAction(text="tick"),
    )
    tid = await sched.schedule(spec)

    # Drain at a moment well past the first due time.
    now = time.time_ns() + 3601 * _NS
    await sched._drain_due_tasks(now)
    await asyncio.sleep(0.05)  # let the fire-and-forget runner task run

    assert runner.dispatched == [tid]
    assert sched._heap, "an `every` task must be re-armed into the heap"
    next_due = sched._heap[0][0]
    assert next_due >= now + 3600 * _NS - _NS
    assert next_due <= now + 3600 * _NS + _NS


async def test_scheduler_does_not_rearm_oneshot(store: TaskStore):
    bus = EventBus()
    runner = _FakeRunner()
    sched = TaskScheduler(store=store, bus=bus, runner=runner)
    spec = TaskSpec(
        title="once",
        trigger=TriggerAfterDelay(delay_seconds=5.0),
        action=SpeakAction(text="tick"),
    )
    tid = await sched.schedule(spec)

    now = time.time_ns() + 10 * _NS
    await sched._drain_due_tasks(now)
    await asyncio.sleep(0.05)  # let the fire-and-forget runner task run

    assert runner.dispatched == [tid]
    assert not sched._heap, "a one-shot task must NOT be re-armed"


# ---------------------------------------------------------------------
# Runner recurring lifecycle
# ---------------------------------------------------------------------

class _FakeTTS:
    async def synthesize(self, text: str, voice: str | None = None):
        async def _gen():
            yield b"\x00"
        return _gen()


async def test_runner_every_returns_to_scheduled(store: TaskStore):
    bus = EventBus()
    runner = TaskRunner(store=store, bus=bus, tts=_FakeTTS())
    spec = TaskSpec(
        title="hourly",
        trigger=TriggerEvery(interval_seconds=3600.0),
        action=SpeakAction(text="tick"),
    )
    tid = await store.insert(spec)
    await runner.run(tid)
    row = await store.get(tid)
    assert row["state"] == "scheduled", "recurring task must re-arm, not complete"


async def test_runner_agent_dispatches_to_brain(store: TaskStore):
    """An agent action runs a brain turn with the prompt + the granted tools."""
    bus = EventBus()
    seen: dict[str, object] = {}

    class _FakeBrain:
        async def run_task(self, *, prompt: str, allowed_tools, model_tier, trace_id=None):
            seen["prompt"] = prompt
            seen["allowed_tools"] = list(allowed_tools)
            seen["model_tier"] = model_tier
            return "done"

    runner = TaskRunner(store=store, bus=bus, agent_brain=_FakeBrain())
    spec = TaskSpec(
        title="brief",
        trigger=TriggerAfterDelay(delay_seconds=5.0),
        action=AgentAction(
            prompt="Summarize my inbox.",
            plugin_grants=(PluginGrant(plugin_id="gmail", scope="read"),),
            model_tier="deep",
        ),
    )
    tid = await store.insert(spec)
    await runner.run(tid)
    row = await store.get(tid)
    assert row["state"] == "completed"
    assert seen["prompt"] == "Summarize my inbox."
    assert seen["allowed_tools"] == ["gmail"]
    assert seen["model_tier"] == "deep"


async def test_runner_agent_delivers_result_as_announcement(store: TaskStore):
    """A finished agent task speaks its result via AnnouncementRequested."""
    bus = EventBus()
    announced: list[object] = []

    async def _capture(ev: object) -> None:
        if type(ev).__name__ == "AnnouncementRequested":
            announced.append(ev)

    bus.subscribe_all(_capture)

    class _FakeBrain:
        async def run_task(self, *, prompt, allowed_tools, model_tier, trace_id=None):
            return "Your briefing: 2 meetings today."

    runner = TaskRunner(store=store, bus=bus, agent_brain=_FakeBrain())
    spec = TaskSpec(
        title="brief",
        trigger=TriggerAfterDelay(delay_seconds=5.0),
        action=AgentAction(prompt="brief me"),
    )
    tid = await store.insert(spec)
    await runner.run(tid)
    assert any("briefing" in getattr(ev, "text", "").lower() for ev in announced)


async def test_runner_arms_auto_approver_for_write_grants(store: TaskStore):
    """A write-scoped grant lets an ask-tier tool run unattended."""
    from jarvis.core.events import ActionApprovalRequired, ActionApproved
    from jarvis.tasks.approval_bridge import TaskAutoApprover

    bus = EventBus()
    approver = TaskAutoApprover(bus)
    approvals: list[ActionApproved] = []

    async def _cap(ev: object) -> None:
        if isinstance(ev, ActionApproved):
            approvals.append(ev)

    bus.subscribe_all(_cap)

    class _Brain:
        async def run_task(self, *, prompt, allowed_tools, model_tier, trace_id):
            # Simulate the tool loop proposing an ask-tier action mid-turn.
            await bus.publish(
                ActionApprovalRequired(
                    trace_id=trace_id, tool_name="buffer", risk_tier="ask"
                )
            )
            await asyncio.sleep(0)
            return "posted"

    runner = TaskRunner(store=store, bus=bus, agent_brain=_Brain(), auto_approver=approver)
    spec = TaskSpec(
        title="tweet",
        trigger=TriggerAfterDelay(delay_seconds=5.0),
        action=AgentAction(
            prompt="post a tweet",
            plugin_grants=(PluginGrant(plugin_id="buffer", scope="write"),),
        ),
    )
    tid = await store.insert(spec)
    await runner.run(tid)
    assert any(ev.tool_name == "buffer" for ev in approvals)


async def test_runner_does_not_auto_approve_read_grants(store: TaskStore):
    """A read-scoped grant must NOT pre-authorize an ask-tier action."""
    from jarvis.core.events import ActionApprovalRequired, ActionApproved
    from jarvis.tasks.approval_bridge import TaskAutoApprover

    bus = EventBus()
    approver = TaskAutoApprover(bus)
    approvals: list[ActionApproved] = []

    async def _cap(ev: object) -> None:
        if isinstance(ev, ActionApproved):
            approvals.append(ev)

    bus.subscribe_all(_cap)

    class _Brain:
        async def run_task(self, *, prompt, allowed_tools, model_tier, trace_id):
            await bus.publish(
                ActionApprovalRequired(
                    trace_id=trace_id, tool_name="gmail", risk_tier="ask"
                )
            )
            await asyncio.sleep(0)
            return "read done"

    runner = TaskRunner(store=store, bus=bus, agent_brain=_Brain(), auto_approver=approver)
    spec = TaskSpec(
        title="briefing",
        trigger=TriggerAfterDelay(delay_seconds=5.0),
        action=AgentAction(
            prompt="summarize inbox",
            plugin_grants=(PluginGrant(plugin_id="gmail", scope="read"),),
        ),
    )
    tid = await store.insert(spec)
    await runner.run(tid)
    assert approvals == []


async def test_runner_agent_without_brain_fails_cleanly(store: TaskStore):
    """No agent brain configured → clean failure, not an unknown-kind crash."""
    bus = EventBus()
    runner = TaskRunner(store=store, bus=bus)  # no agent_brain
    spec = TaskSpec(
        title="brief",
        trigger=TriggerAfterDelay(delay_seconds=5.0),
        action=AgentAction(prompt="do x"),
    )
    tid = await store.insert(spec)
    await runner.run(tid)
    row = await store.get(tid)
    assert row["state"] == "failed"
    assert "agent" in (row["last_error"] or "").lower()


async def test_runner_oneshot_completes(store: TaskStore):
    bus = EventBus()
    runner = TaskRunner(store=store, bus=bus, tts=_FakeTTS())
    spec = TaskSpec(
        title="once",
        trigger=TriggerAfterDelay(delay_seconds=5.0),
        action=SpeakAction(text="tick"),
    )
    tid = await store.insert(spec)
    await runner.run(tid)
    row = await store.get(tid)
    assert row["state"] == "completed"


# ---------------------------------------------------------------------
# Legacy migration (CHECK constraint predating `every`)
# ---------------------------------------------------------------------

_LEGACY_SCHEMA = """
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN (
        'pending','scheduled','running','completed',
        'failed','cancelled','interrupted')),
    trigger_type TEXT NOT NULL CHECK(trigger_type IN (
        'after_delay','at_time','on_event')),
    due_at_ns INTEGER,
    event_selector TEXT,
    title TEXT NOT NULL DEFAULT '',
    created_at_ns INTEGER NOT NULL,
    started_at_ns INTEGER,
    finished_at_ns INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    result_json TEXT
);
CREATE TABLE task_steps (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    timestamp_ns INTEGER NOT NULL,
    PRIMARY KEY (task_id, seq)
);
"""


async def test_store_migrates_legacy_trigger_check(tmp_path: Path):
    db = tmp_path / "legacy.db"
    conn = await aiosqlite.connect(db)
    await conn.executescript(_LEGACY_SCHEMA)
    # Seed one legacy row so migration must preserve data.
    await conn.execute(
        "INSERT INTO tasks (id, trace_id, spec_json, state, trigger_type, "
        "due_at_ns, title, created_at_ns, attempts) "
        "VALUES ('old1','t','{}','scheduled','after_delay',123,'old',1,0)"
    )
    await conn.commit()
    await conn.close()

    store = TaskStore(db)
    await store.init()
    try:
        # Legacy row survived the migration.
        old = await store.get("old1")
        assert old is not None and old["trigger_type"] == "after_delay"
        # And an `every` task now inserts without a CHECK violation.
        spec = TaskSpec(
            title="hourly",
            trigger=TriggerEvery(interval_seconds=3600.0),
            action=SpeakAction(text="tick"),
        )
        tid = await store.insert(spec)
        row = await store.get(tid)
        assert row["trigger_type"] == "every"
    finally:
        await store.close()
