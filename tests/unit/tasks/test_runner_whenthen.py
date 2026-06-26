"""TaskRunner When-Then behaviour — event templating + notify-on-complete.

Covers the runner half of the When-Then feature: the triggering event's fields
interpolate into the action prompt, and ``announce_on_success`` /
``announce_on_failure`` emit a spoken confirmation (AnnouncementRequested,
kind="subagent") that survives the voice hangup gate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import AnnouncementRequested
from jarvis.core.protocols import HarnessResult
from jarvis.tasks.runner import TaskRunner, _safe_format
from jarvis.tasks.schema import (
    HarnessDispatchAction,
    TaskSpec,
    TriggerOnEvent,
)
from jarvis.tasks.store import TaskStore


class FakeHarnessManager:
    def __init__(self, results: list[HarnessResult] | None = None) -> None:
        self.dispatched: list[tuple[str, Any]] = []
        self._results = results or [
            HarnessResult(stdout="ok\n", exit_code=0, is_final=True),
        ]

    async def dispatch(self, name: str, task: Any):
        self.dispatched.append((name, task))

        async def gen():
            for r in self._results:
                yield r
        return gen()


@pytest.fixture
async def store(tmp_path: Path):
    s = TaskStore(tmp_path / "wt.db")
    await s.init()
    try:
        yield s
    finally:
        await s.close()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


def _collect_announcements(bus: EventBus) -> list[AnnouncementRequested]:
    got: list[AnnouncementRequested] = []

    async def on_ann(e: AnnouncementRequested) -> None:
        got.append(e)

    bus.subscribe(AnnouncementRequested, on_ann)
    return got


def _mission_event(**over: Any) -> dict[str, Any]:
    base = {
        "mission_id": "m1",
        "status": "approved",
        "summary_de": "Fertig.",
        "summary_en": "Done.",
        "result_uri": "/out/result.md",
        "reason": "",
    }
    base.update(over)
    return base


# ----------------------------------------------------------------------
# _safe_format
# ----------------------------------------------------------------------

def test_safe_format_interpolates_known_fields() -> None:
    assert _safe_format("open {result_uri}", {"result_uri": "/x"}) == "open /x"


def test_safe_format_passes_unknown_through() -> None:
    assert _safe_format("hi {nope}", {"result_uri": "/x"}) == "hi {nope}"


def test_safe_format_tolerates_malformed_braces() -> None:
    assert _safe_format("100% { done", {}) == "100% { done"


def test_safe_format_no_braces_is_identity() -> None:
    assert _safe_format("plain text", {"a": 1}) == "plain text"


# ----------------------------------------------------------------------
# Templating into the action
# ----------------------------------------------------------------------

async def test_harness_prompt_is_templated_from_event(
    store: TaskStore, bus: EventBus
) -> None:
    hm = FakeHarnessManager()
    runner = TaskRunner(store=store, bus=bus, harness_manager=hm)
    spec = TaskSpec(
        title="open result",
        trigger=TriggerOnEvent(event_name="MissionCompleted",
                               filter_expr="status == 'approved'"),
        action=HarnessDispatchAction(
            harness="screenshot",
            prompt="open {result_uri} in the browser",
            allow_computer_use=True,
        ),
    )
    tid = await store.insert(spec)

    await runner.run(tid, trigger_event=_mission_event())

    assert len(hm.dispatched) == 1
    name, task = hm.dispatched[0]
    assert name == "screenshot"
    assert task.prompt == "open /out/result.md in the browser"
    assert task.allow_computer_use is True


# ----------------------------------------------------------------------
# Notify-on-complete
# ----------------------------------------------------------------------

async def test_announce_on_success_emits_subagent_announcement(
    store: TaskStore, bus: EventBus
) -> None:
    hm = FakeHarnessManager()
    runner = TaskRunner(store=store, bus=bus, harness_manager=hm)
    anns = _collect_announcements(bus)
    spec = TaskSpec(
        title="open + notify",
        trigger=TriggerOnEvent(event_name="MissionCompleted"),
        action=HarnessDispatchAction(harness="screenshot", prompt="x",
                                     allow_computer_use=True),
        announce_on_success="Done — opened {result_uri}.",
    )
    tid = await store.insert(spec)

    await runner.run(tid, trigger_event=_mission_event())

    assert len(anns) == 1
    assert anns[0].text == "Done — opened /out/result.md."
    assert anns[0].kind == "subagent"


async def test_announce_on_failure_fires_when_action_fails(
    store: TaskStore, bus: EventBus
) -> None:
    hm = FakeHarnessManager(
        results=[HarnessResult(stdout="", stderr="boom", exit_code=5, is_final=True)]
    )
    runner = TaskRunner(store=store, bus=bus, harness_manager=hm)
    anns = _collect_announcements(bus)
    spec = TaskSpec(
        title="open + notify-fail",
        trigger=TriggerOnEvent(event_name="MissionCompleted"),
        action=HarnessDispatchAction(harness="screenshot", prompt="x",
                                     allow_computer_use=True),
        announce_on_success="should not fire",
        announce_on_failure="Could not open the result.",
    )
    tid = await store.insert(spec)

    await runner.run(tid, trigger_event=_mission_event())

    assert len(anns) == 1
    assert anns[0].text == "Could not open the result."
    task = await store.get(tid)
    assert task is not None
    assert task["state"] == "failed"


async def test_no_announcement_when_unset(store: TaskStore, bus: EventBus) -> None:
    hm = FakeHarnessManager()
    runner = TaskRunner(store=store, bus=bus, harness_manager=hm)
    anns = _collect_announcements(bus)
    spec = TaskSpec(
        title="silent",
        trigger=TriggerOnEvent(event_name="MissionCompleted"),
        action=HarnessDispatchAction(harness="screenshot", prompt="x",
                                     allow_computer_use=True),
    )
    tid = await store.insert(spec)

    await runner.run(tid, trigger_event=_mission_event())

    assert anns == []
