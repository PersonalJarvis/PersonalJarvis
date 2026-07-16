"""Unit tests for ``jarvis.memory.wiki.journal`` — the Stage-1 candidate store.

The journal is the durable append-only queue between the cheap conversation
extractor (Stage 1) and the body-aware consolidator (Stage 2). It must
survive restarts (real SQLite file), enforce the status/decision vocab at
the SQL layer, and support the consolidator's drain/mark cycle.
"""
from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from jarvis.memory.migration_runner import run_migrations_sync
from jarvis.memory.wiki.journal import CandidateFact, CandidateJournal


@pytest.fixture
def journal(tmp_path: Path) -> CandidateJournal:
    j = CandidateJournal(tmp_path / "jarvis.db")
    yield j
    j.close()


def _facts() -> list[CandidateFact]:
    return [
        CandidateFact(
            fact="Lena moved to Hamburg.",
            kind="person",
            subjects=("lena",),
            evidence_turn_id="turn-17",
            evidence_excerpt="I know Lena moved to Hamburg.",
        ),
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
    assert rows[0].evidence_turn_id == "turn-17"
    assert rows[0].evidence_excerpt == "I know Lena moved to Hamburg."
    assert rows[0].session_id == ""
    assert rows[1].evidence_turn_id == ""
    assert rows[0].source_label == "voice-fact:123"
    assert journal.backlog_count() == 2


def test_append_blocks_secret_shaped_candidate_defense_in_depth(
    journal: CandidateJournal,
) -> None:
    secret = "sk-proj-" + "B" * 30
    count = journal.append(
        [CandidateFact(fact=f"Credential: {secret}", subjects=("alex",))],
        source_label="test",
        turn_hash="secret-defense",
    )

    assert count == 0
    assert journal.pending() == []


def test_candidate_evidence_survives_reopen(tmp_path: Path) -> None:
    db = tmp_path / "jarvis.db"
    first = CandidateJournal(db)
    first.append(_facts()[:1], source_label="s", turn_hash="h")
    first.close()

    second = CandidateJournal(db)
    try:
        assert second.pending()[0].evidence_turn_id == "turn-17"
        assert second.pending()[0].evidence_excerpt == (
            "I know Lena moved to Hamburg."
        )
        tables = {
            row[0]
            for row in second._conn.execute(  # noqa: SLF001 - schema contract probe
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "wiki_extraction_audit" in tables
        assert "wiki_candidate_evidence" in tables
    finally:
        second.close()


def test_migration_0008_adds_grounding_excerpt_store_to_existing_database(
    tmp_path: Path,
) -> None:
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE TABLE wiki_candidate_evidence ("
            "candidate_id INTEGER PRIMARY KEY, "
            "evidence_turn_id TEXT NOT NULL DEFAULT '')"
        )
        conn.execute("PRAGMA user_version = 7")

        assert run_migrations_sync(conn) == 8

        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(wiki_candidate_evidence_excerpt)"
            ).fetchall()
        }
        assert "evidence_excerpt" in columns
    finally:
        conn.close()


def test_standalone_journal_then_numbered_migrations_is_idempotent(
    tmp_path: Path,
) -> None:
    """Journal bootstrap must not make RecallStore skip unrelated migrations."""
    db = tmp_path / "fresh-ordering.db"
    journal = CandidateJournal(db)
    journal.append(_facts()[:1], source_label="s", turn_hash="h")
    journal.close()

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        schema = (
            Path(__file__).resolve().parents[4]
            / "jarvis"
            / "memory"
            / "schema.sql"
        )
        conn.executescript(schema.read_text(encoding="utf-8"))

        assert run_migrations_sync(conn) == 8
        assert run_migrations_sync(conn) == 8
    finally:
        conn.close()

    reopened = CandidateJournal(db)
    try:
        row = reopened.pending()[0]
        assert row.evidence_turn_id == "turn-17"
        assert row.evidence_excerpt == "I know Lena moved to Hamburg."
    finally:
        reopened.close()


def test_development_column_excerpt_is_copied_without_migration_collision(
    tmp_path: Path,
) -> None:
    """Preserve data written by the short-lived ALTER-column implementation."""
    db = tmp_path / "development-column.db"
    migrations = Path(__file__).resolve().parents[4] / "jarvis" / "memory" / "migrations"
    conn = sqlite3.connect(db)
    try:
        for name in (
            "0005_wiki_candidate_journal.sql",
            "0006_wiki_extraction_audit.sql",
            "0007_wiki_candidate_capture.sql",
        ):
            conn.executescript((migrations / name).read_text(encoding="utf-8"))
        conn.execute(
            "ALTER TABLE wiki_candidate_evidence "
            "ADD COLUMN evidence_excerpt TEXT NOT NULL DEFAULT ''"
        )
        cur = conn.execute(
            "INSERT INTO wiki_candidate_journal "
            "(created_ms, source_label, turn_hash, fact) VALUES (?, ?, ?, ?)",
            (1, "legacy", "legacy-hash", "Lena moved to Hamburg."),
        )
        conn.execute(
            "INSERT INTO wiki_candidate_evidence "
            "(candidate_id, evidence_turn_id, evidence_excerpt) VALUES (?, ?, ?)",
            (int(cur.lastrowid), "turn-17", "Legacy grounded user evidence."),
        )
        conn.commit()
    finally:
        conn.close()

    journal = CandidateJournal(db)
    try:
        assert journal.pending()[0].evidence_excerpt == (
            "Legacy grounded user evidence."
        )
    finally:
        journal.close()

    conn = sqlite3.connect(db)
    try:
        schema = (
            Path(__file__).resolve().parents[4]
            / "jarvis"
            / "memory"
            / "schema.sql"
        )
        conn.executescript(schema.read_text(encoding="utf-8"))
        assert run_migrations_sync(conn) == 8
    finally:
        conn.close()


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


def test_pending_can_be_scoped_to_capture_review_keys(
    journal: CandidateJournal,
) -> None:
    selected_key = "session:v2:selected"
    other_key = "session:v2:other"
    for key, fact in (
        (other_key, "An older unrelated fact."),
        (selected_key, "The selected session fact."),
    ):
        assert journal.claim_capture(
            key,
            key,
            "session-sweep",
            "a" * 64,
            key.rsplit(":", 1)[-1],
        )
        assert journal.commit_capture_candidates(
            [
                CandidateFact(
                    fact=fact,
                    evidence_turn_id=f"{key}-turn",
                    evidence_excerpt=f"User evidence for {fact}",
                )
            ],
            review_key=key,
            source_label=key,
            turn_hash=key,
        ) == 1

    assert [row.fact for row in journal.pending(review_keys=(selected_key,))] == [
        "The selected session fact."
    ]
    selected = journal.pending(review_keys=(selected_key,))[0]
    assert selected.session_id == "selected"
    assert selected.evidence_excerpt.startswith("User evidence")
    assert journal.pending(review_keys=()) == []
    assert len(journal.pending()) == 2


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


def test_close_is_terminal_no_silent_reopen(tmp_path: Path) -> None:
    """A background task landing after shutdown must NOT re-open the DB.

    close() is terminal: every later operation degrades to a logged no-op
    (append -> 0, pending -> [], backlog -> 0, seen_turn -> False) instead
    of silently re-opening a connection on a closing process.
    """
    j = CandidateJournal(tmp_path / "jarvis.db")
    j.append(_facts()[:1], source_label="s", turn_hash="h")
    j.close()

    assert j.append(_facts(), source_label="late", turn_hash="h9") == 0
    assert j.pending() == []
    assert j.backlog_count() == 0
    assert j.seen_turn("h") is False
    assert j.claim_capture("review:h", "s", "realtime", "a" * 40) is False
    assert j.finish_capture("review:h", "empty") is False
    assert j.capture_seen("review:h") is False
    assert j.capture_summary() == {
        "window_hours": 24,
        "total": 0,
        "started": 0,
        "filtered": 0,
        "empty": 0,
        "candidates": 0,
        "failed": 0,
        "facts": 0,
        "sessions_swept": 0,
    }
    j.mark([1], status="skipped")  # must not raise
    assert j._conn is None  # noqa: SLF001 — the terminal-close contract


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


def test_capture_claim_is_atomic_across_connections(tmp_path: Path) -> None:
    db = tmp_path / "jarvis.db"
    first = CandidateJournal(db)
    second = CandidateJournal(db)
    try:
        # Open both connections before the race so DDL is not part of it.
        first.capture_summary()
        second.capture_summary()
        barrier = threading.Barrier(2)

        def _claim(journal: CandidateJournal) -> bool:
            barrier.wait(timeout=2)
            return journal.claim_capture(
                "live:v2:session-1:turn-1",
                "realtime:1",
                "realtime",
                "a" * 40,
                "session-1",
                "turn-1",
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(_claim, (first, second)))
        assert sorted(results) == [False, True]
    finally:
        first.close()
        second.close()


def test_capture_failed_and_stale_started_are_retried(tmp_path: Path) -> None:
    now_s = [1_000.0]
    journal = CandidateJournal(tmp_path / "jarvis.db", clock=lambda: now_s[0])
    key = "live:v2:session-1:turn-1"
    try:
        assert journal.claim_capture(
            key, "realtime:1", "realtime", "b" * 40, "session-1", "turn-1"
        )
        assert journal.claim_capture(
            key, "realtime:1", "realtime", "b" * 40, "session-1", "turn-1"
        ) is False
        assert journal.capture_seen(key) is False

        now_s[0] += 301
        assert journal.claim_capture(
            key, "realtime:1", "realtime", "b" * 40, "session-1", "turn-1"
        )
        assert journal.finish_capture(
            key,
            "failed",
            provider="gemini",
            duration_ms=123,
            error_code="provider_timeout",
        )
        assert journal.capture_seen(key) is False

        assert journal.claim_capture(
            key, "realtime:1", "realtime", "b" * 40, "session-1", "turn-1"
        )
        assert journal.finish_capture(
            key, "candidates", candidate_count=2, provider="gemini", duration_ms=50
        )
        assert journal.capture_seen(key) is True
        assert journal.claim_capture(
            key, "realtime:1", "realtime", "b" * 40, "session-1", "turn-1"
        ) is False

        row = journal._conn.execute(  # noqa: SLF001 - audit persistence probe
            "SELECT status, attempts, candidate_count, error_code, finished_ms "
            "FROM wiki_extraction_audit WHERE review_key = ?",
            (key,),
        ).fetchone()
        assert row == ("candidates", 3, 2, "", int(now_s[0] * 1000))
    finally:
        journal.close()


def test_candidate_append_and_capture_finish_are_one_transaction(tmp_path: Path) -> None:
    now_s = [1_000.0]
    db = tmp_path / "jarvis.db"
    journal = CandidateJournal(db, clock=lambda: now_s[0])
    key = "live:v2:session-atomic:turn-1"
    try:
        assert journal.claim_capture(
            key,
            "realtime:atomic",
            "realtime",
            "d" * 40,
            "session-atomic",
            "turn-1",
        )
        journal._conn.execute(  # noqa: SLF001 - force a mid-transaction failure
            "CREATE TRIGGER fail_capture_link BEFORE INSERT ON wiki_candidate_capture "
            "BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
        )
        journal._conn.commit()  # noqa: SLF001

        with pytest.raises(sqlite3.IntegrityError, match="forced failure"):
            journal.commit_capture_candidates(
                _facts()[:1],
                review_key=key,
                source_label="realtime:atomic",
                turn_hash="turn-1",
                provider="gemini",
            )
        assert journal.pending() == []
        assert journal.capture_seen(key) is False

        journal._conn.execute("DROP TRIGGER fail_capture_link")  # noqa: SLF001
        journal._conn.commit()  # noqa: SLF001
        now_s[0] += 301
        assert journal.claim_capture(
            key,
            "realtime:atomic",
            "realtime",
            "d" * 40,
            "session-atomic",
            "turn-1",
        )
        assert journal.commit_capture_candidates(
            _facts()[:1],
            review_key=key,
            source_label="realtime:atomic",
            turn_hash="turn-1",
            provider="gemini",
        ) == 1
        assert len(journal.pending()) == 1
        assert journal.capture_seen(key) is True
    finally:
        journal.close()


def test_capture_summary_has_fixed_shape_and_no_raw_errors(tmp_path: Path) -> None:
    journal = CandidateJournal(tmp_path / "jarvis.db", clock=lambda: 2_000.0)
    try:
        captures = (
            ("r1", "realtime", "session-1", "filtered", 0),
            ("r2", "session-sweep", "session-2", "empty", 0),
            ("r3", "session_sweep", "session-2", "candidates", 3),
            ("r4", "realtime", "session-3", "failed", 0),
        )
        secret = "sk-proj-" + "A" * 30
        for key, source_kind, session_id, status, count in captures:
            assert journal.claim_capture(
                key, f"source:{key}", source_kind, key, session_id, f"turn-{key}"
            )
            assert journal.finish_capture(
                key,
                status,  # type: ignore[arg-type]
                candidate_count=count,
                provider=secret,
                error_code=f"HTTP 500: raw provider body with {secret}",
            )
        assert journal.claim_capture(
            "r5", "source:r5", "realtime", "r5", "session-4", "turn-r5"
        )

        assert journal.capture_summary(window_hours=24) == {
            "window_hours": 24,
            "total": 5,
            "started": 1,
            "filtered": 1,
            "empty": 1,
            "candidates": 1,
            "failed": 1,
            "facts": 3,
            "sessions_swept": 1,
        }
        error = journal._conn.execute(  # noqa: SLF001 - redaction contract probe
            "SELECT error_code FROM wiki_extraction_audit WHERE review_key = 'r4'"
        ).fetchone()[0]
        assert error == "other"
        raw = journal._conn.execute(  # noqa: SLF001 - no-raw-content contract probe
            "SELECT GROUP_CONCAT(error_code || provider || source_label) "
            "FROM wiki_extraction_audit"
        ).fetchone()[0]
        assert secret not in raw
    finally:
        journal.close()


def test_capture_status_is_checked_in_python_and_sql(journal: CandidateJournal) -> None:
    assert journal.claim_capture("review-1", "s", "realtime", "c" * 40)
    with pytest.raises(ValueError, match="unknown capture status"):
        journal.finish_capture("review-1", "banana")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="requires a terminal"):
        journal.finish_capture("review-1", "started")

    conn = journal._conn  # noqa: SLF001 - deliberate SQL constraint probe
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE wiki_extraction_audit SET status = 'banana' "
            "WHERE review_key = 'review-1'"
        )
