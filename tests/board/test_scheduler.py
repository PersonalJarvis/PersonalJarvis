"""BioScheduler — the bio is rewritten on board events, never on a timer."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest

from jarvis.board.profile import BioGenerator, BioStore, make_resolver_from_brain
from jarvis.board.scheduler import BioScheduler
from jarvis.board.store import BoardStore
from jarvis.brain.background_policy import BackgroundDeferred
from jarvis.core.bus import EventBus
from jarvis.core.events import AchievementUnlocked
from jarvis.core.protocols import BrainDelta, BrainRequest


class _FakeBrain:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.calls += 1
        yield BrainDelta(content=f"Bio run {self.calls}. Data only. Nothing big.")
        yield BrainDelta(finish_reason="stop", usage={"input_tokens": 100, "output_tokens": 50})


def _unlocked(achievement_id: str) -> AchievementUnlocked:
    return AchievementUnlocked(
        achievement_id=achievement_id, title=achievement_id, description="",
        tier="mastery", evidence={},
    )


def _make(
    tmp_path: Path,
    *,
    bus: EventBus | None = None,
    resolver: Callable[[], object] | None = None,
    debounce_s: float = 0.05,
    min_spacing_s: float = 3600.0,
) -> tuple[BioScheduler, BioGenerator, BioStore, _FakeBrain]:
    db = tmp_path / "personal.db"
    bio_store = BioStore(db)
    brain = _FakeBrain()
    gen = BioGenerator(
        brain_resolver=resolver or make_resolver_from_brain(brain),
        store=BoardStore(db),
        bio_store=bio_store,
        jsonl_dir=tmp_path / "flight_recorder",
    )
    sched = BioScheduler(
        generator=gen, bio_store=bio_store, bus=bus,
        debounce_s=debounce_s, min_spacing_s=min_spacing_s,
    )
    return sched, gen, bio_store, brain


async def _settle(sched: BioScheduler, timeout_s: float = 5.0) -> None:
    """Wait until no generation is pending (a run may hand over to a follow-up)."""
    for _ in range(5):
        task = sched._pending
        if task is None:
            return
        await asyncio.wait_for(task, timeout=timeout_s)
    assert sched._pending is None, "pending bio generation never finished"


@pytest.mark.asyncio
async def test_burst_of_achievements_causes_one_generation(tmp_path: Path) -> None:
    bus = EventBus()
    sched, _gen, bio_store, brain = _make(tmp_path, bus=bus)
    sched.start()
    try:
        for achievement in ("tool_dabbler", "triple_combo", "first_mcp"):
            await bus.publish(_unlocked(achievement))
        await _settle(sched)
        assert brain.calls == 1
        latest = bio_store.latest()
        assert latest is not None
        assert latest["triggered_by"] == "milestone:tool_dabbler"
    finally:
        await sched.stop()


@pytest.mark.asyncio
async def test_start_generates_nothing_without_a_hook(tmp_path: Path) -> None:
    """No weekly tick, no boot-time cold start — even with no bio at all."""
    bus = EventBus()
    sched, _gen, bio_store, brain = _make(tmp_path, bus=bus, debounce_s=0.0)
    sched.start()
    try:
        await asyncio.sleep(0.2)
        assert sched._pending is None
        assert brain.calls == 0
        assert bio_store.latest() is None
    finally:
        await sched.stop()


@pytest.mark.asyncio
async def test_unrelated_events_are_not_hooks(tmp_path: Path) -> None:
    from jarvis.core.events import BioFeedbackRecorded

    bus = EventBus()
    sched, _gen, _bio_store, brain = _make(tmp_path, bus=bus, debounce_s=0.0)
    sched.start()
    try:
        await bus.publish(BioFeedbackRecorded(bio_generated_at="x", kind="haerter"))
        await asyncio.sleep(0.1)
        assert sched._pending is None
        assert brain.calls == 0
    finally:
        await sched.stop()


@pytest.mark.asyncio
async def test_hook_inside_spacing_waits_for_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timedelta

    import jarvis.board.scheduler as scheduler_module

    sched, _gen, bio_store, brain = _make(tmp_path, min_spacing_s=3600)
    bio_store.insert("earlier bio", triggered_by="manual")
    generated_at = datetime.fromisoformat(bio_store.latest()["generated_at"])
    now = [generated_at + timedelta(seconds=1)]
    sched._clock = lambda: now[0]
    waiting = asyncio.Event()
    advance = asyncio.Event()
    real_sleep = asyncio.sleep

    async def controlled_sleep(delay: float) -> None:
        if delay > 10:
            assert delay == pytest.approx(3599)
            waiting.set()
            await advance.wait()
        else:
            await real_sleep(delay)

    monkeypatch.setattr(scheduler_module.asyncio, "sleep", controlled_sleep)
    sched.start()
    try:
        sched.notify("milestone:centennial")
        await asyncio.wait_for(waiting.wait(), timeout=5)
        assert brain.calls == 0, "generated inside the spacing window"
        now[0] = generated_at + timedelta(seconds=3601)
        advance.set()
        await _settle(sched)
        assert brain.calls == 1
        assert bio_store.latest()["triggered_by"] == "milestone:centennial"  # type: ignore[index]
    finally:
        await sched.stop()


@pytest.mark.asyncio
async def test_bio_written_after_the_hook_covers_the_pending_run(tmp_path: Path) -> None:
    """A manual refresh after the hook already reflects it — no second run."""
    sched, gen, bio_store, brain = _make(tmp_path, min_spacing_s=0.3)
    bio_store.insert("earlier bio", triggered_by="manual")
    await asyncio.sleep(0.01)
    sched.start()
    try:
        sched.notify("milestone:kilo_club")
        await gen.generate_bio(triggered_by="manual")
        assert brain.calls == 1
        await _settle(sched)
        assert brain.calls == 1
        assert bio_store.latest()["triggered_by"] == "manual"  # type: ignore[index]
    finally:
        await sched.stop()


@pytest.mark.asyncio
async def test_deferred_generation_waits_for_the_next_hook(tmp_path: Path) -> None:
    attempts: list[int] = []

    def _deferred() -> object:
        attempts.append(1)
        raise BackgroundDeferred("No subscription or local model can write the bio right now.")

    sched, gen, bio_store, _brain = _make(tmp_path, resolver=_deferred)
    sched.start()
    try:
        sched.notify("milestone:first_mcp")
        await _settle(sched)
        assert len(attempts) == 1
        assert bio_store.latest() is None
        assert gen.last_skip_reason and "subscription" in gen.last_skip_reason

        await asyncio.sleep(0.2)
        assert len(attempts) == 1, "a deferred bio must not be retried on a timer"

        sched.notify("milestone:tool_dabbler")
        await _settle(sched)
        assert len(attempts) == 2
    finally:
        await sched.stop()


@pytest.mark.asyncio
async def test_stop_cancels_pending_work_and_unsubscribes(tmp_path: Path) -> None:
    bus = EventBus()
    sched, _gen, _bio_store, brain = _make(tmp_path, bus=bus, debounce_s=10.0)
    sched.start()
    await bus.publish(_unlocked("tool_master"))
    assert sched._pending is not None
    await sched.stop()
    assert sched._pending is None

    await bus.publish(_unlocked("centennial"))
    await asyncio.sleep(0.05)
    assert sched._pending is None
    assert brain.calls == 0
