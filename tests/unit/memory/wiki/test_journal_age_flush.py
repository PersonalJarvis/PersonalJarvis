"""Pending ambient candidates must become visible by age, not only count
(spec A4) — a fresh machine with 2 candidates used to sit at zero .md
files forever (threshold was 8).
"""
from __future__ import annotations

from pathlib import Path

from jarvis.memory.wiki.journal import CandidateFact, CandidateJournal


def _mk_journal(tmp_path: Path, now_ms: list[int]) -> CandidateJournal:
    return CandidateJournal(tmp_path / "journal.db", clock=lambda: now_ms[0] / 1000)


def test_oldest_pending_ms_none_when_empty(tmp_path: Path) -> None:
    j = _mk_journal(tmp_path, [1_000_000])
    assert j.oldest_pending_ms() is None


def test_oldest_pending_ms_returns_first_pending(tmp_path: Path) -> None:
    now = [1_000_000]
    j = _mk_journal(tmp_path, now)
    j.append(
        [CandidateFact(fact="Fact one about Joy.", kind="fact", subjects=("joy",))],
        source_label="test",
        turn_hash="h1",
    )
    now[0] += 60_000
    j.append(
        [CandidateFact(fact="Fact two about Rome.", kind="fact", subjects=("rome",))],
        source_label="test",
        turn_hash="h2",
    )
    oldest = j.oldest_pending_ms()
    assert oldest is not None
    assert oldest <= 1_000_000


def test_oldest_pending_ms_ignores_consolidated_rows(tmp_path: Path) -> None:
    now = [1_000_000]
    j = _mk_journal(tmp_path, now)
    j.append(
        [CandidateFact(fact="Fact one about Joy.", kind="fact", subjects=("joy",))],
        source_label="test",
        turn_hash="h1",
    )
    now[0] += 60_000
    j.append(
        [CandidateFact(fact="Fact two about Rome.", kind="fact", subjects=("rome",))],
        source_label="test",
        turn_hash="h2",
    )
    rows = j.pending()
    j.mark([rows[0].id], status="consolidated", decision="add", target_path="entities/joy.md")

    oldest = j.oldest_pending_ms()
    assert oldest is not None
    assert oldest >= 1_060_000


def test_default_consolidation_threshold_is_three() -> None:
    from jarvis.core.config import SchedulerConfig

    cfg = SchedulerConfig()
    assert cfg.consolidate_after_candidates == 3
    assert cfg.flush_pending_max_age_minutes == 10
