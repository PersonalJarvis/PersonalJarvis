"""CuratorScheduler JOURNAL trigger (Wave-2 B4): journal pressure drains
through the Stage-2 consolidator under the shared lock, without delaying a
reviewed durable turn behind the general scheduler cooldown.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.core.config import SchedulerConfig
from jarvis.memory.wiki.lock import VaultLock
from jarvis.memory.wiki.scheduler import (
    CuratorScheduler,
    TriggerSource,
    fire_journal_trigger,
)


class FakeCurator:
    def __init__(self) -> None:
        self.ingest_calls: list[str] = []

    async def ingest(self, source_content: str, source_label: str):
        self.ingest_calls.append(source_label)


class FakeConsolidator:
    def __init__(self) -> None:
        self.runs = 0
        self.review_keys: list[tuple[str, ...] | None] = []

    async def run_once(self, *, review_keys=None) -> str:  # noqa: ANN001
        self.runs += 1
        self.review_keys.append(
            tuple(review_keys) if review_keys is not None else None
        )
        return f"journal-batch:{self.runs}"


def _scheduler(tmp_path: Path, *, consolidator=None, cooldown: int = 60) -> CuratorScheduler:
    return CuratorScheduler(
        curator=FakeCurator(),
        lock=VaultLock(tmp_path / "curator.lock"),
        config=SchedulerConfig(cooldown_seconds=cooldown),
        consolidator=consolidator,
    )


@pytest.mark.asyncio
async def test_journal_trigger_runs_consolidator(tmp_path: Path) -> None:
    consolidator = FakeConsolidator()
    scheduler = _scheduler(tmp_path, consolidator=consolidator)

    result = await scheduler.trigger(TriggerSource.JOURNAL)

    assert result.triggered is True
    assert consolidator.runs == 1
    assert result.curator_output_label == "journal-batch:1"


@pytest.mark.asyncio
async def test_global_trigger_immediately_rechecks_deferred_same_target_rows(
    tmp_path: Path,
) -> None:
    class _DeferredOnce(FakeConsolidator):
        async def run_once(self, *, review_keys=None) -> str:  # noqa: ANN001
            self.runs += 1
            self.review_keys.append(review_keys)
            return "journal-deferred:1" if self.runs == 1 else "journal-batch:1"

    consolidator = _DeferredOnce()
    scheduler = _scheduler(tmp_path, consolidator=consolidator)

    result = await scheduler.trigger(TriggerSource.JOURNAL)

    assert result.triggered is True
    assert consolidator.runs == 2
    assert result.curator_output_label == "journal-batch:1"


@pytest.mark.asyncio
async def test_targeted_journal_trigger_forwards_exact_review_keys(
    tmp_path: Path,
) -> None:
    consolidator = FakeConsolidator()
    scheduler = _scheduler(tmp_path, consolidator=consolidator)

    result = await scheduler.trigger(
        TriggerSource.JOURNAL,
        review_keys=("session:v2:one", "session:v2:two"),
    )

    assert result.triggered is True
    assert consolidator.review_keys == [
        ("session:v2:one", "session:v2:two")
    ]


@pytest.mark.asyncio
async def test_targeted_trigger_waits_for_active_pressure_drain(
    tmp_path: Path,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class _SlowGlobalConsolidator(FakeConsolidator):
        async def run_once(self, *, review_keys=None) -> str:  # noqa: ANN001
            self.runs += 1
            keys = tuple(review_keys) if review_keys is not None else None
            self.review_keys.append(keys)
            if keys is None:
                entered.set()
                await release.wait()
            return f"journal-batch:{self.runs}"

    consolidator = _SlowGlobalConsolidator()
    scheduler = _scheduler(tmp_path, consolidator=consolidator)
    pressure = fire_journal_trigger(scheduler)
    await entered.wait()
    scoped = asyncio.create_task(
        scheduler.trigger(
            TriggerSource.JOURNAL,
            review_keys=("session:v3:backfill",),
        )
    )
    await asyncio.sleep(0)

    assert not scoped.done(), "scoped maintenance must wait for the lock owner"
    release.set()
    pressure_result, scoped_result = await asyncio.gather(pressure, scoped)

    assert pressure_result.triggered is True
    assert scoped_result.triggered is True
    assert scoped_result.skip_reason == ""
    assert consolidator.review_keys == [None, ("session:v3:backfill",)]


@pytest.mark.asyncio
async def test_journal_trigger_bypasses_cooldown(tmp_path: Path) -> None:
    consolidator = FakeConsolidator()
    scheduler = _scheduler(tmp_path, consolidator=consolidator, cooldown=3600)

    first = await scheduler.trigger(TriggerSource.JOURNAL)
    second = await scheduler.trigger(TriggerSource.JOURNAL)

    assert first.triggered is True
    assert second.triggered is True
    assert second.skip_reason == ""
    assert consolidator.runs == 2


@pytest.mark.asyncio
async def test_overlapping_journal_triggers_are_coalesced_not_lost(
    tmp_path: Path,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class _SlowConsolidator(FakeConsolidator):
        async def run_once(self) -> str:
            self.runs += 1
            if self.runs == 1:
                entered.set()
                await release.wait()
            return f"journal-batch:{self.runs}"

    consolidator = _SlowConsolidator()
    scheduler = _scheduler(tmp_path, consolidator=consolidator)
    first = asyncio.create_task(scheduler.trigger(TriggerSource.JOURNAL))
    await entered.wait()
    second = asyncio.create_task(scheduler.trigger(TriggerSource.JOURNAL))
    await asyncio.sleep(0)

    assert not second.done(), "the later trigger must wait instead of returning locked"
    release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result.triggered is True
    assert second_result.triggered is True
    assert consolidator.runs == 2


@pytest.mark.asyncio
async def test_many_overlapping_journal_triggers_need_only_one_follow_up(
    tmp_path: Path,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class _SlowConsolidator(FakeConsolidator):
        async def run_once(self) -> str:
            self.runs += 1
            if self.runs == 1:
                entered.set()
                await release.wait()
            return f"journal-batch:{self.runs}"

    consolidator = _SlowConsolidator()
    scheduler = _scheduler(tmp_path, consolidator=consolidator)
    first = asyncio.create_task(scheduler.trigger(TriggerSource.JOURNAL))
    await entered.wait()
    followers = [
        asyncio.create_task(scheduler.trigger(TriggerSource.JOURNAL))
        for _ in range(25)
    ]
    await asyncio.sleep(0)

    release.set()
    results = await asyncio.gather(first, *followers)

    assert all(result.triggered for result in results)
    assert consolidator.runs == 2


@pytest.mark.asyncio
async def test_fire_and_forget_requests_share_one_task(tmp_path: Path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class _SlowConsolidator(FakeConsolidator):
        async def run_once(self) -> str:
            self.runs += 1
            entered.set()
            await release.wait()
            return f"journal-batch:{self.runs}"

    scheduler = _scheduler(tmp_path, consolidator=_SlowConsolidator())
    first = fire_journal_trigger(scheduler)
    await entered.wait()
    second = fire_journal_trigger(scheduler)

    assert second is first
    release.set()
    await first


@pytest.mark.asyncio
async def test_journal_trigger_without_consolidator_skips(tmp_path: Path) -> None:
    scheduler = _scheduler(tmp_path, consolidator=None)

    result = await scheduler.trigger(TriggerSource.JOURNAL)

    assert result.triggered is False
    assert result.skip_reason == "no_consolidator"


def test_scheduler_config_has_journal_pressure_threshold() -> None:
    assert SchedulerConfig().consolidate_after_candidates == 1


# ---------------------------------------------------------------------------
# Boot-time backlog drain (C1): leftovers below the pressure threshold are
# consolidated at the next boot instead of waiting for 8 candidates.
# ---------------------------------------------------------------------------


class _FakeJournal:
    def __init__(self, backlog: int) -> None:
        self._backlog = backlog

    def backlog_count(self) -> int:
        return self._backlog


class _RecordingScheduler:
    def __init__(self) -> None:
        self.triggers: list[TriggerSource] = []

    async def trigger(self, source: TriggerSource):
        self.triggers.append(source)


@pytest.mark.asyncio
async def test_boot_drain_fires_journal_trigger_when_backlog_pending(tmp_path: Path) -> None:
    from jarvis.memory.wiki.integration import kick_journal_backlog

    scheduler = _RecordingScheduler()
    kick_journal_backlog(_FakeJournal(3), scheduler)
    # Fire-and-forget: the trigger runs as a background task.
    import asyncio

    await asyncio.sleep(0.05)
    assert scheduler.triggers == [TriggerSource.JOURNAL]


@pytest.mark.asyncio
async def test_boot_drain_is_silent_on_empty_backlog(tmp_path: Path) -> None:
    from jarvis.memory.wiki.integration import kick_journal_backlog

    scheduler = _RecordingScheduler()
    kick_journal_backlog(_FakeJournal(0), scheduler)
    import asyncio

    await asyncio.sleep(0.05)
    assert scheduler.triggers == []
