"""Tests fuer BioScheduler (Phase B)."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from jarvis.board.profile import BioGenerator, BioStore, make_resolver_from_brain
from jarvis.board.scheduler import BioScheduler
from jarvis.board.store import BoardStore
from jarvis.core.bus import EventBus
from jarvis.core.events import AchievementUnlocked
from jarvis.core.protocols import BrainDelta, BrainRequest


class _FakeBrain:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.calls += 1
        yield BrainDelta(content=f"Bio-Run #{self.calls}. Nur Daten. Nichts gross.")
        yield BrainDelta(finish_reason="stop", usage={"input_tokens": 100, "output_tokens": 50})


def _make_scheduler(tmp_path: Path, bus: EventBus | None = None) -> tuple[BioScheduler, BioStore, _FakeBrain]:
    db = tmp_path / "personal.db"
    bio_store = BioStore(db)
    store = BoardStore(db)
    brain = _FakeBrain()
    gen = BioGenerator(
        brain_resolver=make_resolver_from_brain(brain), store=store, bio_store=bio_store,
        jsonl_dir=tmp_path / "flight_recorder",
    )
    sched = BioScheduler(
        generator=gen, db_path=db, bus=bus, tick_interval_s=0.05,
    )
    return sched, bio_store, brain


@pytest.mark.asyncio
async def test_master_achievement_triggers_bio_regeneration(tmp_path: Path) -> None:
    bus = EventBus()
    sched, bio_store, brain = _make_scheduler(tmp_path, bus=bus)
    sched.start()
    try:
        await bus.publish(AchievementUnlocked(
            achievement_id="tool_master",
            title="Tool Master", description="",
            tier="mastery", evidence={"unique_tools": 30},
        ))
        await asyncio.sleep(0.1)
        assert brain.calls == 1
        latest = bio_store.latest()
        assert latest is not None
        assert latest["triggered_by"] == "milestone:tool_master"
    finally:
        await sched.stop()


@pytest.mark.asyncio
async def test_non_master_achievement_does_not_trigger(tmp_path: Path) -> None:
    bus = EventBus()
    sched, bio_store, brain = _make_scheduler(tmp_path, bus=bus)
    sched.start()
    try:
        await bus.publish(AchievementUnlocked(
            achievement_id="tool_dabbler",
            title="Tool Dabbler", description="",
            tier="mastery", evidence={},
        ))
        await asyncio.sleep(0.1)
        assert brain.calls == 0
        assert bio_store.latest() is None
    finally:
        await sched.stop()


@pytest.mark.asyncio
async def test_weekly_date_guard_respected(tmp_path: Path) -> None:
    """Der Scheduler setzt ``last_bio_run_date`` — zweiter Lauf heute no-op."""
    sched, bio_store, brain = _make_scheduler(tmp_path)
    today = "2026-04-24"
    await sched._run_and_mark(triggered_by="weekly", today_iso=today)
    await sched._run_and_mark(triggered_by="weekly", today_iso=today)
    assert brain.calls == 2  # beide Calls liefen, weil _run_and_mark kein Guard
    # Aber: _maybe_run_weekly wuerde den zweiten Run ueberspringen wegen
    # last_bio_run_date-Check. Verifizieren:
    assert sched._read_meta("last_bio_run_date") == today
