-- Two embedding spaces may coexist, so switching the model no longer blinds
-- semantic search.
--
-- Why: `uw_embeddings` was keyed by `document_id` alone, so exactly ONE vector
-- per document could exist and therefore exactly one embedding space per
-- store. Changing the embedding provider or model consequently had to DELETE
-- every stored vector before the first replacement was computed — semantic
-- search went dark for the whole re-embed, minutes to hours on a real corpus,
-- and switching back cost the same again. The rebuild itself is unavoidable
-- (vectors of two models are not comparable), but the blackout is not.
--
-- Widening the key to (document_id, model, dim) lets the NEW space be built
-- alongside the live one; the `uw_vec` ANN index stays derived from the ACTIVE
-- space only and is rebuilt at the moment of promotion. A cancelled switch
-- simply drops the half-built shadow rows and leaves the live space untouched.

BEGIN;

CREATE TABLE uw_embeddings_new (
    document_id INTEGER NOT NULL
                REFERENCES uw_documents(id) ON DELETE CASCADE,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    vector      BLOB NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (document_id, model, dim)
);

INSERT INTO uw_embeddings_new
    (document_id, model, dim, vector, created_at)
SELECT document_id, model, dim, vector, created_at FROM uw_embeddings;

DROP TABLE uw_embeddings;

ALTER TABLE uw_embeddings_new RENAME TO uw_embeddings;

-- The promotion and progress queries both filter by space.
CREATE INDEX IF NOT EXISTS idx_uw_embeddings_space
    ON uw_embeddings(model, dim);

COMMIT;
