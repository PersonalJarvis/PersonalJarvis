"""Unit tests for ``jarvis.memory.wiki.journal`` — the Stage-1 candidate store.

The journal is the durable append-only queue between the cheap conversation
extractor (Stage 1) and the body-aware consolidator (Stage 2). It must
survive restarts (real SQLite file), enforce the status/decision vocab at
the SQL layer, and support the consolidator's drain/mark cycle.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jarvis.memory.wiki.journal import CandidateFact, CandidateJournal


@pytest.fixture
def journal(tmp_path: Path) -> CandidateJournal:
    j = CandidateJournal(tmp_path / "jarvis.db")
    yield j
    j.close()


def _facts() -> list[CandidateFact]:
    return [
        CandidateFact(fact="Lena moved to Hamburg.", kind="person", subjects=("lena",)),
        CandidateFact(fact="User prefers dark mode.", kind="preference", subjects=("alex",)),
    ]


def test_append_then_pending_roundtrip(journal: CandidateJournal) -> None:
    n = journal.append(_facts(), source_label="voice-fact:123", turn_hash="abc")
    assert n == 2
    rows = journal.pending(limit=10)
    assert [r.fact for r in rows] == [
        "Lena moved to Hamburg.",
        "User prefers dark mode.",
    ]
    assert rows[0].status == "pending"
    assert rows[0].subjects == ("lena",)
    assert rows[0].source_label == "voice-fact:123"
    assert journal.backlog_count() == 2


def test_mark_consolidated_removes_from_pending(journal: CandidateJournal) -> None:
    journal.append(_facts(), source_label="s", turn_hash="h1")
    rows = journal.pending()
    journal.mark(
        [rows[0].id], status="consolidated", decision="add",
        target_path="entities/lena.md",
    )
    remaining = journal.pending()
    assert len(remaining) == 1
    assert remaining[0].fact == "User prefers dark mode."
    assert journal.backlog_count() == 1


def test_mark_skipped_without_decision(journal: CandidateJournal) -> None:
    journal.append(_facts()[:1], source_label="s", turn_hash="h2")
    rows = journal.pending()
    journal.mark([rows[0].id], status="skipped")
    assert journal.pending() == []


def test_seen_turn_dedupe(journal: CandidateJournal) -> None:
    assert journal.seen_turn("hash-1") is False
    journal.append(_facts()[:1], source_label="s", turn_hash="hash-1")
    assert journal.seen_turn("hash-1") is True
    assert journal.seen_turn("hash-2") is False


def test_survives_reopen(tmp_path: Path) -> None:
    db = tmp_path / "jarvis.db"
    j1 = CandidateJournal(db)
    j1.append(_facts(), source_label="s", turn_hash="h")
    j1.close()
    j2 = CandidateJournal(db)
    try:
        assert j2.backlog_count() == 2
    finally:
        j2.close()


def test_sql_check_rejects_invalid_status(journal: CandidateJournal) -> None:
    """The vocab is enforced at the SQL layer, not only in Python."""
    journal.append(_facts()[:1], source_label="s", turn_hash="h")
    conn = journal._conn  # noqa: SLF001 — deliberate raw-SQL layer probe
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE wiki_candidate_journal SET status = 'banana'"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE wiki_candidate_journal SET decision = 'delete'"
        )
