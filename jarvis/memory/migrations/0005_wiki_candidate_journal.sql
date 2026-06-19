-- 0005_wiki_candidate_journal.sql
-- Stage-1 candidate facts extracted from conversation turns (Wave 2,
-- docs/superpowers/specs/2026-06-09-wiki-autonomous-curator-design.md §4).
-- Durable append-only queue: survives restarts; drained by the consolidator.
--
-- The status/decision CHECK lists MUST stay byte-aligned with
-- jarvis/memory/wiki/constants.py (CANDIDATE_STATUSES / CURATOR_DECISIONS);
-- tests/unit/memory/wiki/test_curator_decision_parity.py enforces parity.
CREATE TABLE IF NOT EXISTS wiki_candidate_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ms      INTEGER NOT NULL,
    source_label    TEXT    NOT NULL,
    turn_hash       TEXT    NOT NULL,
    fact            TEXT    NOT NULL,
    kind            TEXT    NOT NULL DEFAULT 'other',
    subjects        TEXT    NOT NULL DEFAULT '[]',
    status          TEXT    NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'consolidated', 'rejected', 'skipped')),
    decision        TEXT
        CHECK (decision IS NULL OR decision IN ('add', 'update', 'noop', 'invalidate')),
    target_path     TEXT,
    consolidated_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_wiki_candidate_journal_status
    ON wiki_candidate_journal (status, id);

CREATE INDEX IF NOT EXISTS idx_wiki_candidate_journal_turn_hash
    ON wiki_candidate_journal (turn_hash);
