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


# ---------------------------------------------------------------------------
# _should_age_flush: pure age/enable/empty decision behind the flush loop
# (spec A4). Testing it directly avoids driving the 120s-sleep loop.
# ---------------------------------------------------------------------------

_NOW_MS = 10_000_000


def test_should_age_flush_false_when_nothing_pending() -> None:
    from jarvis.memory.wiki.integration import _should_age_flush

    assert _should_age_flush(None, _NOW_MS, 10) is False


def test_should_age_flush_false_when_disabled() -> None:
    from jarvis.memory.wiki.integration import _should_age_flush

    # max_age_min <= 0 disables the flush even for an arbitrarily old row.
    assert _should_age_flush(0, _NOW_MS, 0) is False
    assert _should_age_flush(0, _NOW_MS, -5) is False


def test_should_age_flush_false_below_threshold() -> None:
    from jarvis.memory.wiki.integration import _should_age_flush

    # 9 minutes old, threshold 10 → not yet.
    oldest = _NOW_MS - 9 * 60_000
    assert _should_age_flush(oldest, _NOW_MS, 10) is False


def test_should_age_flush_true_at_and_over_threshold() -> None:
    from jarvis.memory.wiki.integration import _should_age_flush

    # Exactly 10 minutes old → fire (>=), and clearly over → fire.
    at_threshold = _NOW_MS - 10 * 60_000
    over_threshold = _NOW_MS - 20 * 60_000
    assert _should_age_flush(at_threshold, _NOW_MS, 10) is True
    assert _should_age_flush(over_threshold, _NOW_MS, 10) is True
