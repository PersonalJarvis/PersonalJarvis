"""AU-12 / AU-13 — a scheduled routine that breaks must say so.

Two silent deaths lived here:

* the scheduler only ``log.warning``-ed a cron trigger that failed, and only
  ``log.exception``-ed a tick that crashed — so a broken routine, or every
  routine at once, simply stopped happening;
* the runner ``break``s out of a failed step chain, which skips a trailing
  speak step — Jarvis went quiet exactly when it had the most to report.

Both now publish the announcement event every other background result travels
on (``AnnouncementRequested(kind="subagent")``), rate-limited so a routine that
fails every minute is not said sixty times an hour.

Fakes throughout — a real store on tmp_path, a fake runner/brain, no network.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import AnnouncementRequested
from jarvis.workflows.runner import FailureAnnouncer, WorkflowRunner
from jarvis.workflows.scheduler import WorkflowScheduler
from jarvis.workflows.schema import (
    BrainPromptStep,
    ManualTrigger,
    SpeakStep,
    WorkflowDef,
)
from jarvis.workflows.store import WorkflowStore


@pytest.fixture
async def store(tmp_path: Path) -> WorkflowStore:
    s = WorkflowStore(tmp_path / "wf.sqlite")
    await s.init()
    yield s
    await s.close()


def _announcements(bus: EventBus) -> list[AnnouncementRequested]:
    seen: list[AnnouncementRequested] = []
    bus.subscribe(AnnouncementRequested, seen.append)
    return seen


async def _await_run_state(store: WorkflowStore, run_id: str, state: str) -> None:
    for _ in range(50):
        await asyncio.sleep(0.01)
        run = await store.get_run(run_id)
        if run and run["state"] == state:
            return
    pytest.fail(f"Run did not reach state {state!r} within 500ms")


class _BrokenBrain:
    """Async callable that always fails — a step that dies mid-chain."""

    async def __call__(self, prompt: str) -> str:
        raise RuntimeError("the notes file could not be opened")


class _BrokenRunner:
    """Runner stand-in whose trigger never gets off the ground."""

    def __init__(self) -> None:
        self.attempts = 0

    async def trigger(self, workflow_id: str, *, trigger_reason: str) -> str:
        self.attempts += 1
        raise RuntimeError("no worker available")


class _OneWorkflowStore:
    """Store stand-in holding exactly one overdue cron row."""

    def __init__(self, name: str = "Morning Briefing") -> None:
        self.rows = [
            {
                "id": "wf-0001",
                "name": name,
                "enabled": 1,
                "trigger_type": "cron",
                "cron_expression": "* * * * *",
                # Already due, so the tick fires it immediately.
                "next_run_at_ns": time.time_ns() - 1,
            }
        ]

    async def list_workflows(self) -> list[dict[str, Any]]:
        return list(self.rows)

    async def set_next_run(self, workflow_id: str, next_run_at_ns: int | None) -> None:
        self.rows[0]["next_run_at_ns"] = next_run_at_ns


# ----------------------------------------------------------------------
# AU-13 — the runner
# ----------------------------------------------------------------------

async def test_failed_step_is_announced_to_the_user(store: WorkflowStore) -> None:
    bus = EventBus()
    seen = _announcements(bus)
    runner = WorkflowRunner(store=store, bus=bus, brain=_BrokenBrain())

    wf = WorkflowDef(
        name="Morning Briefing",
        trigger=ManualTrigger(),
        steps=(
            BrainPromptStep(label="Read the notes", prompt="summarise"),
            SpeakStep(text="here is your briefing"),
        ),
    )
    wid = await store.upsert_workflow(wf)
    run_id = await runner.trigger(wid)
    await _await_run_state(store, run_id, "failed")

    # The trailing speak step never ran — that part of the control flow is
    # correct and unchanged. The failure report is what must exist.
    assert len(seen) == 1
    text = seen[0].text
    assert seen[0].kind == "subagent"
    assert "Morning Briefing" in text
    assert "Read the notes" in text
    assert "the notes file could not be opened" in text
    # Plain language only — no exception class name, no run id.
    assert "RuntimeError" not in text
    assert run_id not in text


async def test_failure_without_a_speakable_reason_still_announces(
    store: WorkflowStore,
) -> None:
    """An opaque error degrades to the reasonless sentence, never to silence."""

    class _OpaqueBrain:
        async def __call__(self, prompt: str) -> str:
            raise RuntimeError("exit 1")

    bus = EventBus()
    seen = _announcements(bus)
    runner = WorkflowRunner(store=store, bus=bus, brain=_OpaqueBrain())

    wf = WorkflowDef(
        name="Nightly Sync",
        trigger=ManualTrigger(),
        steps=(BrainPromptStep(prompt="go"),),
    )
    wid = await store.upsert_workflow(wf)
    run_id = await runner.trigger(wid)
    await _await_run_state(store, run_id, "failed")

    assert len(seen) == 1
    assert "Nightly Sync" in seen[0].text
    assert "exit 1" not in seen[0].text
    # No author label on that step, so the plain ordinal stands in.
    assert "1" in seen[0].text


async def test_a_routine_failing_every_run_is_announced_once(
    store: WorkflowStore,
) -> None:
    """The anti-spam rule: first failure speaks, the repeats stay quiet."""
    bus = EventBus()
    seen = _announcements(bus)
    runner = WorkflowRunner(store=store, bus=bus, brain=_BrokenBrain())

    wf = WorkflowDef(
        name="Every Minute",
        trigger=ManualTrigger(),
        steps=(BrainPromptStep(prompt="go"),),
    )
    wid = await store.upsert_workflow(wf)
    for _ in range(5):
        run_id = await runner.trigger(wid)
        await _await_run_state(store, run_id, "failed")

    assert len(seen) == 1, "a repeating failure turned into a metronome"


async def test_a_recovery_re_arms_the_next_failure(store: WorkflowStore) -> None:
    """A successful run clears the memory, so the NEXT failure is news again."""

    class _FlakyBrain:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, prompt: str) -> str:
            self.calls += 1
            if self.calls == 2:
                return "fine"
            raise RuntimeError("the notes file could not be opened")

    bus = EventBus()
    seen = _announcements(bus)
    runner = WorkflowRunner(store=store, bus=bus, brain=_FlakyBrain())

    wf = WorkflowDef(
        name="Flaky",
        trigger=ManualTrigger(),
        steps=(BrainPromptStep(prompt="go"),),
    )
    wid = await store.upsert_workflow(wf)
    for expected in ("failed", "completed", "failed"):
        run_id = await runner.trigger(wid)
        await _await_run_state(store, run_id, expected)

    assert len(seen) == 2


# ----------------------------------------------------------------------
# AU-12 — the scheduler
# ----------------------------------------------------------------------

async def test_failed_cron_trigger_is_announced_to_the_user() -> None:
    bus = EventBus()
    seen = _announcements(bus)
    broken = _BrokenRunner()
    scheduler = WorkflowScheduler(
        store=_OneWorkflowStore(), runner=broken, bus=bus,  # type: ignore[arg-type]
    )

    await scheduler._tick()

    assert broken.attempts == 1
    announcements = [e for e in seen if e.source_layer == "workflows.scheduler"]
    assert len(announcements) == 1
    assert "Morning Briefing" in announcements[0].text
    assert announcements[0].kind == "subagent"
    # The routine's name, never its id.
    assert "wf-0001" not in announcements[0].text


async def test_a_cron_trigger_failing_every_minute_is_announced_once() -> None:
    bus = EventBus()
    seen = _announcements(bus)
    store = _OneWorkflowStore()
    scheduler = WorkflowScheduler(
        store=store, runner=_BrokenRunner(), bus=bus,  # type: ignore[arg-type]
    )

    for _ in range(5):
        store.rows[0]["next_run_at_ns"] = time.time_ns() - 1
        await scheduler._tick()

    announcements = [e for e in seen if e.source_layer == "workflows.scheduler"]
    assert len(announcements) == 1


async def test_a_crashed_tick_tells_the_user_the_routines_are_down() -> None:
    """The whole poll loop died — every routine stopped, so say so once."""

    class _DeadStore:
        async def list_workflows(self) -> list[dict[str, Any]]:
            raise RuntimeError("the workflow database is unreachable")

    bus = EventBus()
    seen = _announcements(bus)
    scheduler = WorkflowScheduler(
        store=_DeadStore(), runner=_BrokenRunner(), bus=bus,  # type: ignore[arg-type]
    )
    scheduler.start()
    try:
        # Two ticks would happen back to back if the loop did not sleep 30s
        # after a crash; one announcement is all the user may hear either way.
        for _ in range(30):
            await asyncio.sleep(0.01)
            if seen:
                break
    finally:
        await scheduler.stop()

    announcements = [e for e in seen if e.source_layer == "workflows.scheduler"]
    assert len(announcements) == 1
    assert announcements[0].text
    assert "RuntimeError" not in announcements[0].text


# ----------------------------------------------------------------------
# The shared suppression rule
# ----------------------------------------------------------------------

def test_failure_announcer_speaks_again_after_the_cooldown() -> None:
    announcer = FailureAnnouncer(cooldown_s=0.0)
    assert announcer.should_speak("wf") is True
    assert announcer.should_speak("wf") is True


def test_failure_announcer_keys_are_independent() -> None:
    announcer = FailureAnnouncer()
    assert announcer.should_speak("a") is True
    assert announcer.should_speak("b") is True
    assert announcer.should_speak("a") is False
    announcer.clear("a")
    assert announcer.should_speak("a") is True
