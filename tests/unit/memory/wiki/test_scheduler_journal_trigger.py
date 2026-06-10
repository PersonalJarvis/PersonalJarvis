"""CuratorScheduler JOURNAL trigger (Wave-2 B4): journal pressure drains
through the Stage-2 consolidator, under the same cooldown + lock gates as
the legacy sources.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.core.config import SchedulerConfig
from jarvis.memory.wiki.lock import VaultLock
from jarvis.memory.wiki.scheduler import CuratorScheduler, TriggerSource


class FakeCurator:
    def __init__(self) -> None:
        self.ingest_calls: list[str] = []

    async def ingest(self, source_content: str, source_label: str):
        self.ingest_calls.append(source_label)


class FakeConsolidator:
    def __init__(self) -> None:
        self.runs = 0

    async def run_once(self) -> str:
        self.runs += 1
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
async def test_journal_trigger_honours_cooldown(tmp_path: Path) -> None:
    consolidator = FakeConsolidator()
    scheduler = _scheduler(tmp_path, consolidator=consolidator, cooldown=3600)

    first = await scheduler.trigger(TriggerSource.JOURNAL)
    second = await scheduler.trigger(TriggerSource.JOURNAL)

    assert first.triggered is True
    assert second.triggered is False
    assert second.skip_reason == "cooldown"
    assert consolidator.runs == 1


@pytest.mark.asyncio
async def test_journal_trigger_without_consolidator_skips(tmp_path: Path) -> None:
    scheduler = _scheduler(tmp_path, consolidator=None)

    result = await scheduler.trigger(TriggerSource.JOURNAL)

    assert result.triggered is False
    assert result.skip_reason == "no_consolidator"


def test_scheduler_config_has_journal_pressure_threshold() -> None:
    assert SchedulerConfig().consolidate_after_candidates == 8


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
