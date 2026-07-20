-- Persist the evidence basis and personal-salience score per candidate fact.
--
-- basis records HOW a fact is grounded: "explicit" (the user asserted it),
-- "behavioral" (first-person lived-experience report without a literal
-- preference assertion), or "inferred" (reserved for a future cross-session
-- reflection pass; nothing writes it yet). salience scores how central the
-- fact is to the user's own life (1 = trivia .. 5 = core identity) and drives
-- the configurable Stage-1 floor.

-- Keep both values in their own one-to-one table. Unlike ALTER TABLE ADD
-- COLUMN, CREATE TABLE IF NOT EXISTS is safe when CandidateJournal initialized
-- its standalone schema before RecallStore later replays numbered migrations.

BEGIN;

CREATE TABLE IF NOT EXISTS wiki_candidate_basis (
    candidate_id  INTEGER PRIMARY KEY
        REFERENCES wiki_candidate_journal(id) ON DELETE CASCADE,
    basis         TEXT NOT NULL DEFAULT 'explicit'
        CHECK (basis IN ('explicit', 'behavioral', 'inferred')),
    salience      INTEGER NOT NULL DEFAULT 3
        CHECK (salience BETWEEN 1 AND 5)
);

COMMIT;
