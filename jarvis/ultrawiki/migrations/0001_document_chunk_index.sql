-- Multi-chunk documents: one item may now hold MANY documents of the same
-- type, one per passage of its text.
--
-- Why: the embed stage cut every item at 8 000 characters and wrote exactly
-- one document per (item, doc_type). A source file, a long thread or a
-- transcript therefore reached the vector space as its opening paragraph and
-- nothing else — the rest sat in SQL, unsearchable by meaning. Storing several
-- passages was not a tuning question but structurally impossible: the
-- replace-on-(item_id, doc_type) rule meant chunk 2 deleted chunk 1.
--
-- `chunk_index` is the passage ordinal (0 for a whole-item document, so every
-- existing row keeps its meaning under the DEFAULT). The unique index is what
-- makes "replace this item's passages" an atomic, idempotent operation.

ALTER TABLE uw_documents ADD COLUMN chunk_index INTEGER NOT NULL DEFAULT 0;

-- Byte offsets into the item's body, so a hit can be located in the original
-- text and a UI can show WHERE in a 200 KB file the answer came from.
ALTER TABLE uw_documents ADD COLUMN char_start INTEGER NOT NULL DEFAULT 0;
ALTER TABLE uw_documents ADD COLUMN char_end INTEGER NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX IF NOT EXISTS idx_uw_documents_chunk
    ON uw_documents(item_id, doc_type, chunk_index);
