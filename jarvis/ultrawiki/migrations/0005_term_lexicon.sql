-- Word-level lexicon: the corpus vocabulary, one row per term, with one
-- vector per term in the SAME embedding space as the documents.
--
-- Why: UltraWiki could already answer a QUESTION by meaning, but not a WORD.
-- "Show me what sits near this term" needs a vocabulary that is itself
-- embedded — the document index cannot answer it, because a passage vector
-- says what a passage is about, never what a word means on its own.
--
-- `doc_freq` counts the PASSAGES a term was seen in while the harvester
-- walked them. It is a rarity signal, not an exact count: a tombstoned item
-- takes its passages with it and this number is not decremented, so it drifts
-- high over a long-lived corpus. That is deliberate — the exact count costs a
-- full re-scan and nothing here is a filter that could drop a term, only an
-- ordering hint. `POST /api/ultrawiki/lexicon/rebuild` resets and recounts.
--
-- Term vectors are keyed by (model, dim) like `uw_embeddings`, so a model
-- switch does not mix vector spaces (design rule D-3): the neighbour query
-- reads the ACTIVE space only, and terms of a retired space are simply
-- re-embedded on the next lexicon pass.

CREATE TABLE IF NOT EXISTS uw_terms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    term       TEXT NOT NULL UNIQUE,
    doc_freq   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- The embed candidate order: the most-seen vocabulary first, so a bounded
-- lexicon holds the words the corpus actually talks about.
CREATE INDEX IF NOT EXISTS idx_uw_terms_freq ON uw_terms(doc_freq DESC, id);

CREATE TABLE IF NOT EXISTS uw_term_embeddings (
    term_id    INTEGER NOT NULL REFERENCES uw_terms(id) ON DELETE CASCADE,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    vector     BLOB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (term_id, model, dim)
);

CREATE INDEX IF NOT EXISTS idx_uw_term_embeddings_space
    ON uw_term_embeddings(model, dim);
