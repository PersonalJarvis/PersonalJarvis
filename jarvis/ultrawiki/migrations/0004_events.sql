-- Episodic events with absolute time anchoring (design doc 01 · uw_events,
-- doc 03 · "cross-source reconstruction").
--
-- Why a table and not a document type: an episodic question ("when did I have
-- dinner with X in Y") has a STRUCTURED answer — a date, a place, a set of
-- people — and reconstructing that from prose at read time cannot fit the
-- voice budget. The write path fuses the fragments once; the read path looks
-- one row up.
--
-- BI-TEMPORAL, and the two clocks must never be collapsed:
--   * occurred_at / occurred_end = VALID time. When it happened in the world.
--     Always absolute: a relative expression is resolved against the source
--     item's own timestamp at extraction time, so "next Friday" is never
--     stored as text and still means something two years later.
--   * recorded_at = TRANSACTION time. When the SOURCE recorded the statement
--     (the evidence item's timestamp). A Monday message about a Friday dinner
--     has two different, both correct, answers to "when" — this is what keeps
--     them apart.
--   * created_at = when this row was written. Bookkeeping, not a temporal
--     dimension: a re-derivation moves it and means nothing changed in the
--     world.
--
-- occurred_end is ALWAYS filled, from occurred_precision when the source gave
-- no end: "in March" is one event covering 31 days, not an event at midnight
-- on the 1st, and a range query that treats it as a point silently misses it.
--
-- Dialect note: this is the SQLite source of truth. `PostgresStore.
-- ddl_statements()` mirrors it (BIGSERIAL/BIGINT keys, a generated tsvector
-- instead of the FTS5 table) and DERIVES every CHECK list from the enums in
-- `jarvis/ultrawiki/events.py`; `tests/unit/ultrawiki/test_event_store.py`
-- holds the layers together (AP-4 / BUG-008).

BEGIN;

CREATE TABLE IF NOT EXISTS uw_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    -- The anchor evidence. ON DELETE CASCADE is the whole deletion story:
    -- purging a source removes its items, and every event derived from them
    -- goes with it (design doc 05, "deletion is honored end-to-end").
    item_id      INTEGER NOT NULL REFERENCES uw_items(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL DEFAULT 'other'
                 CHECK (kind IN ('meal', 'travel', 'meeting', 'purchase',
                                 'milestone', 'other')),
    title        TEXT NOT NULL DEFAULT '',
    summary      TEXT NOT NULL DEFAULT '',
    -- Valid time.
    occurred_at  TEXT NOT NULL,
    occurred_end TEXT NOT NULL,
    occurred_precision TEXT NOT NULL DEFAULT 'day'
                 CHECK (occurred_precision IN ('minute', 'hour', 'day', 'week',
                                               'month', 'year')),
    -- Where the absolute time came from. An event anchored merely on the
    -- item's own timestamp is worth far less than one whose source spelled
    -- the date out, and a surface that cannot tell them apart will state a
    -- guessed date as a fact.
    time_anchor  TEXT NOT NULL DEFAULT 'recorded'
                 CHECK (time_anchor IN ('absolute', 'relative', 'recorded')),
    -- Transaction time (the evidence item's own timestamp).
    recorded_at  TEXT NOT NULL,
    place_entity_id INTEGER REFERENCES uw_entities(id) ON DELETE SET NULL,
    place_raw    TEXT NOT NULL DEFAULT '',
    confidence   REAL NOT NULL DEFAULT 0,
    extraction_version INTEGER NOT NULL DEFAULT 0,
    -- Stable identity of the event WITHIN its item: re-deriving an unchanged
    -- item lands on the same key, which is what makes re-extraction idempotent
    -- instead of duplicating on every pipeline pass.
    dedupe_key   TEXT NOT NULL DEFAULT '',
    -- Further items corroborating the same event (cross-source fusion). The
    -- anchor stays in item_id so the citation permalink is never ambiguous.
    evidence_json TEXT NOT NULL DEFAULT '[]',
    -- The denormalized keyword card (title + date in several written forms +
    -- participants + place). Kept on the row so BOTH dialects index the same
    -- text: FTS5 here, a generated tsvector on Postgres.
    search_text  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_uw_events_dedupe
    ON uw_events(item_id, dedupe_key);
-- The range-query shape: "what happened between A and B", newest first.
CREATE INDEX IF NOT EXISTS idx_uw_events_occurred
    ON uw_events(occurred_at, id);
CREATE INDEX IF NOT EXISTS idx_uw_events_kind_time
    ON uw_events(kind, occurred_at);
CREATE INDEX IF NOT EXISTS idx_uw_events_item ON uw_events(item_id);
CREATE INDEX IF NOT EXISTS idx_uw_events_recorded ON uw_events(recorded_at);
CREATE INDEX IF NOT EXISTS idx_uw_events_place ON uw_events(place_entity_id);

-- Who was there. entity_id is NULLABLE on purpose: a participant the identity
-- layer refuses to resolve (ambiguous name, nothing known yet) keeps its
-- spelling here and stays searchable, instead of being dropped because the
-- store could not decide who it was.
CREATE TABLE IF NOT EXISTS uw_event_participants (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     INTEGER NOT NULL REFERENCES uw_events(id) ON DELETE CASCADE,
    entity_id    INTEGER REFERENCES uw_entities(id) ON DELETE SET NULL,
    display_name TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_uw_event_participants_event
    ON uw_event_participants(event_id);
CREATE INDEX IF NOT EXISTS idx_uw_event_participants_entity
    ON uw_event_participants(entity_id);

-- The event keyword leg: contentless FTS5 over the card, keyed by event id
-- (delete+insert upsert, the uw_fts pattern). Separate from uw_fts because an
-- event is not an item: it has its own title, its own date and its own
-- lifetime, and mixing them would make one purge impossible to express.
CREATE VIRTUAL TABLE IF NOT EXISTS uw_event_fts USING fts5(
    event_id UNINDEXED,
    title,
    body,
    tokenize = "unicode61 remove_diacritics 2"
);

COMMIT;
