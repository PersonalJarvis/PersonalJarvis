-- UltraWiki store — base schema (SQLite dialect).
--
-- Own DB file (data/ultrawiki.db), following the missions.db precedent
-- (ADR-0009): the staged ingest pipeline is a heavy writer and must not
-- contend with the shared jarvis.db tenants. Applied idempotently on every
-- open; forward-only migrations live in jarvis/ultrawiki/migrations/ and are
-- tracked via PRAGMA user_version (same engine contract as
-- jarvis/memory/migration_runner.py).
--
-- Dialect notes: the Postgres backend derives its DDL from this logical
-- schema inside store.py (tsvector replaces the FTS5 table, pgvector replaces
-- the sqlite-vec index). State/consent CHECK lists are parity-tested against
-- jarvis/ultrawiki/types.py (five-layer drift rule).

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;

-- Store-level facts: schema ownership, the pinned embedding space
-- (embed_model / embed_dim — semi-permanent, decision D-3), default area.
CREATE TABLE IF NOT EXISTS uw_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS uw_areas (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- last_error vs last_notice: an ERROR means the sync failed and the source is
-- broken until the cause is fixed; a NOTICE means the sync ran fine but had
-- nothing to import (today: a plugin-bridge integration whose pull adapter is
-- not built yet). Keeping them apart is what lets the UI colour one red and
-- the other amber instead of making a healthy source look broken.
CREATE TABLE IF NOT EXISTS uw_sources (
    id           TEXT PRIMARY KEY,
    connector    TEXT NOT NULL,
    label        TEXT NOT NULL,
    config_json  TEXT NOT NULL DEFAULT '{}',
    areas_json   TEXT NOT NULL DEFAULT '[]',
    consent      TEXT NOT NULL DEFAULT 'pending'
                 CHECK (consent IN ('pending', 'approved', 'revoked')),
    enabled      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    last_sync_at TEXT,
    last_error   TEXT,
    last_notice  TEXT
);

-- The last_* outcome block survives restarts, so a card can say "fully
-- imported: N items, last sync <when>" instead of the ambiguous "Approved /
-- Never synced" pair. The live job registry is in-memory and empty after a
-- restart; this is the durable half of the same story.
CREATE TABLE IF NOT EXISTS uw_sync_state (
    source_id            TEXT PRIMARY KEY
                         REFERENCES uw_sources(id) ON DELETE CASCADE,
    cursor               TEXT,
    backfill_checkpoint  TEXT,
    backfill_complete_at TEXT,
    last_success_at      TEXT,
    last_outcome_at      TEXT,
    last_outcome_status  TEXT,
    last_outcome_mode    TEXT,
    last_new             INTEGER NOT NULL DEFAULT 0,
    last_changed         INTEGER NOT NULL DEFAULT 0,
    last_unchanged       INTEGER NOT NULL DEFAULT 0,
    last_tombstoned      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS uw_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     TEXT NOT NULL REFERENCES uw_sources(id) ON DELETE CASCADE,
    external_id   TEXT NOT NULL,
    thread_key    TEXT NOT NULL DEFAULT '',
    author_raw    TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    body_raw      TEXT NOT NULL,
    permalink     TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    areas_json    TEXT NOT NULL DEFAULT '[]',
    content_hash  TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'captured'
                  CHECK (state IN ('captured', 'keyword_indexed', 'embedded',
                                   'distilled', 'failed')),
    -- Whatever the connector attached to the RawItem: the source format, a
    -- file's modification time, a photo's capture date and camera, and the
    -- reference an enrichment run needs to reopen a media file's bytes.
    metadata_json TEXT NOT NULL DEFAULT '{}',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    last_error    TEXT,
    deleted_at    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (source_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_uw_items_state
    ON uw_items(state, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_uw_items_source
    ON uw_items(source_id, state);
CREATE INDEX IF NOT EXISTS idx_uw_items_time
    ON uw_items(timestamp_utc);

-- Keyword leg: contentless FTS5 over title+body, keyed by item id
-- (delete+insert upsert, same pattern as wiki_fts). Kept in lockstep with
-- uw_items by store.py inside the SAME transaction as the state transition.
CREATE VIRTUAL TABLE IF NOT EXISTS uw_fts USING fts5(
    item_id UNINDEXED,
    title,
    body,
    tokenize = "unicode61 remove_diacritics 2"
);

-- Derived, embed-ready documents (design doc 01). Several documents may
-- derive from one item (summary + bursts).
CREATE TABLE IF NOT EXISTS uw_documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id         INTEGER NOT NULL REFERENCES uw_items(id) ON DELETE CASCADE,
    doc_type        TEXT NOT NULL CHECK (doc_type IN ('raw', 'summary', 'burst')),
    text_norm       TEXT NOT NULL,
    distill_json    TEXT,
    distill_version INTEGER NOT NULL DEFAULT 0,
    content_hash    TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_uw_documents_item ON uw_documents(item_id);

-- Embedding vectors, stored provider-neutral as little-endian float32 BLOBs
-- keyed by document AND embedding space. The sqlite-vec vec0 index (uw_vec) is
-- DERIVED from this table by store.py once the extension loads and the
-- dimension is pinned — so a host without the extension still accumulates
-- vectors and gains semantic search the moment the extension becomes
-- available, without re-embedding anything.
--
-- The (model, dim) part of the key is what lets a model switch build the new
-- space ALONGSIDE the live one (uw_meta: embed_model/embed_dim pin the active
-- space, pending_embed_model/pending_embed_dim the one being built). Semantic
-- search keeps answering from the active space until the shadow is complete
-- and store.py promotes it. Steady state holds exactly one space.
CREATE TABLE IF NOT EXISTS uw_embeddings (
    document_id INTEGER NOT NULL
                REFERENCES uw_documents(id) ON DELETE CASCADE,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    vector      BLOB NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (document_id, model, dim)
);
CREATE INDEX IF NOT EXISTS idx_uw_embeddings_space
    ON uw_embeddings(model, dim);

-- Distillation results cached on (content_hash, prompt_version, model):
-- identical input is never paid for twice, and crash re-runs converge
-- (design doc 02, determinism economics).
CREATE TABLE IF NOT EXISTS uw_distill_cache (
    content_hash   TEXT NOT NULL,
    prompt_version INTEGER NOT NULL,
    model          TEXT NOT NULL,
    result_json    TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (content_hash, prompt_version, model)
);

-- The identity layer (uw_entities / uw_identifiers / uw_confirm_queue /
-- uw_merge_log, design doc 05) ships as migrations/0003_identity.sql rather
-- than as part of this file, so its DDL exists exactly once per dialect. The
-- runner applies it to fresh and existing databases alike.
--
-- Episodic events (uw_events / uw_event_participants / uw_event_fts, design
-- doc 01) ship the same way as migrations/0004_events.sql.
