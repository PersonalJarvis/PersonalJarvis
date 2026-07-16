-- 0006_wiki_extraction_audit.sql
-- Durable, content-free audit state for conversation-to-wiki capture.
--
-- Raw transcripts and provider error messages deliberately do not belong in
-- the audit table. It stores only stable identifiers, a text digest, bounded
-- status metadata, and aggregate counters. Candidate evidence is kept in a
-- separate one-to-one table; migration 0008 adds a bounded, redacted user-only
-- excerpt store without changing the journal status/decision parity contract.

BEGIN;

CREATE TABLE IF NOT EXISTS wiki_extraction_audit (
    review_key       TEXT PRIMARY KEY,
    source_label     TEXT    NOT NULL,
    source_kind      TEXT    NOT NULL,
    text_hash        TEXT    NOT NULL,
    session_id       TEXT    NOT NULL DEFAULT '',
    turn_id          TEXT    NOT NULL DEFAULT '',
    status           TEXT    NOT NULL
        CHECK (status IN ('started', 'filtered', 'empty', 'candidates', 'failed')),
    candidate_count  INTEGER NOT NULL DEFAULT 0 CHECK (candidate_count >= 0),
    provider         TEXT    NOT NULL DEFAULT '',
    duration_ms      INTEGER NOT NULL DEFAULT 0 CHECK (duration_ms >= 0),
    error_code       TEXT    NOT NULL DEFAULT '',
    attempts         INTEGER NOT NULL DEFAULT 1 CHECK (attempts >= 1),
    created_ms       INTEGER NOT NULL,
    updated_ms       INTEGER NOT NULL,
    started_ms       INTEGER NOT NULL,
    finished_ms      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_wiki_extraction_audit_updated
    ON wiki_extraction_audit (updated_ms);

CREATE INDEX IF NOT EXISTS idx_wiki_extraction_audit_session
    ON wiki_extraction_audit (session_id, source_kind);

CREATE TABLE IF NOT EXISTS wiki_candidate_evidence (
    candidate_id      INTEGER PRIMARY KEY
        REFERENCES wiki_candidate_journal(id) ON DELETE CASCADE,
    evidence_turn_id  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_wiki_candidate_evidence_turn
    ON wiki_candidate_evidence (evidence_turn_id);

COMMIT;
