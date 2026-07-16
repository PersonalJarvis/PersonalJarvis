-- Link extracted candidate rows to their durable capture review.
--
-- The separate mapping keeps migration 0006 forward-compatible for databases
-- that already opened during development. Candidate insertion and terminal
-- audit completion are committed in one SQLite transaction; a retry can never
-- duplicate rows after a crash between those two logical steps.

BEGIN;

CREATE TABLE IF NOT EXISTS wiki_candidate_capture (
    candidate_id  INTEGER PRIMARY KEY
        REFERENCES wiki_candidate_journal(id) ON DELETE CASCADE,
    review_key    TEXT NOT NULL
        REFERENCES wiki_extraction_audit(review_key) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_wiki_candidate_capture_review
    ON wiki_candidate_capture (review_key, candidate_id);

COMMIT;
