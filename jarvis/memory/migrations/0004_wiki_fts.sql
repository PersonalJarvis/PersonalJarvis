-- Migration 0004: Create wiki_fts virtual table (FTS5).
--
-- Rationale
-- =========
-- B5 search backend migration: replaces the rglob/regex file-walking
-- implementation with an SQLite FTS5 index in the shared jarvis.db.
-- The virtual table is keyed on vault-root-relative POSIX paths so that
-- upserts are idempotent (delete-by-path + insert).
--
-- The ``mtime`` column stores the float mtime string returned by
-- ``os.path.getmtime`` so the indexer can short-circuit unchanged pages.
--
-- The ``frontmatter`` column stores a space-joined flat string of all
-- frontmatter values (lists are also joined) so that aliases and tags
-- are searchable as plain tokens.
--
-- Tokeniser: unicode61 with remove_diacritics=2 so that accented
-- characters (ä, ö, ü, é …) match their base form.
--
-- Running this migration on a database that already has the table is
-- a no-op thanks to ``IF NOT EXISTS``.
--
-- Note: FTS5 virtual tables do not support ``BEGIN`` / ``COMMIT`` wrapping
-- in the same executescript call on some SQLite builds (the CREATE is
-- auto-committed by the FTS5 extension).  The BEGIN/COMMIT are kept for
-- consistency with the migration runner contract; SQLite ignores the outer
-- transaction when an implicit one is already active.

BEGIN;

CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(
    path      UNINDEXED,
    title,
    frontmatter,
    body,
    mtime     UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);

COMMIT;
