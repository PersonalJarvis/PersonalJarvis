-- Persist a bounded, redacted user-only evidence excerpt for Stage 2.
--
-- The candidate fact alone is not sufficient provenance: Stage 2 must be able
-- to distinguish a user assertion from an assistant guess that Stage 1 copied
-- accidentally. Raw provider output and assistant text are never stored here.

-- Keep the excerpt in its own one-to-one table. Unlike ALTER TABLE ADD COLUMN,
-- CREATE TABLE IF NOT EXISTS is safe when CandidateJournal initialized its
-- standalone schema before RecallStore later replays numbered migrations.

BEGIN;

CREATE TABLE IF NOT EXISTS wiki_candidate_evidence_excerpt (
    candidate_id      INTEGER PRIMARY KEY
        REFERENCES wiki_candidate_journal(id) ON DELETE CASCADE,
    evidence_excerpt  TEXT NOT NULL DEFAULT ''
);

COMMIT;
