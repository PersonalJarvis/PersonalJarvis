-- Identity layer (design doc 05 · D-10): entities, identifiers, the
-- confirmation queue, and a reversible merge audit trail.
--
-- Why these four tables and not one: the corpus arrives as raw handles — a
-- phone number here, a display name there, an address-book slug in a third
-- place — and the ONLY safe way to fuse them is to keep the mapping itself as
-- data. `uw_identifiers` is that mapping (many handles -> one entity),
-- `uw_entities` holds the thing they point at, `uw_confirm_queue` holds every
-- link the evidence merely SUGGESTS, and `uw_merge_log` records each applied
-- merge together with the exact state needed to undo it.
--
-- Reversibility is the whole point. A wrong merge silently poisons every
-- future answer about both identities and is discovered months later, so no
-- merge may ever be a one-way door: the audit row carries which identifier
-- rows moved, which duplicates were dropped, and what the loser's own
-- bookkeeping looked like before, which is enough to restore the prior state
-- exactly.
--
-- Dialect note: this is the SQLite source of truth. `PostgresStore.
-- ddl_statements()` mirrors it (BIGSERIAL/BIGINT for the keys) and DERIVES
-- every CHECK list from the enums in `jarvis/ultrawiki/identity.py`;
-- `tests/unit/ultrawiki/test_identity_parity.py` holds the three layers
-- together (AP-4 / BUG-008).

BEGIN;

-- A person, place, organization, project or topic. A merged-away entity is
-- NOT deleted: it stays as a tombstone whose `merged_into` forwards every old
-- reference to the survivor, which is what makes an unmerge possible at all.
CREATE TABLE IF NOT EXISTS uw_entities (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL DEFAULT 'person'
                  CHECK (kind IN ('person', 'place', 'org', 'project', 'topic')),
    display_name  TEXT NOT NULL,
    -- Normalized `display_name` (identity.normalize_name): the sort key and
    -- the cheap lookup key. Empty when the display name normalizes to nothing.
    canonical_key TEXT NOT NULL DEFAULT '',
    merged_into   INTEGER REFERENCES uw_entities(id) ON DELETE SET NULL,
    -- Provenance of a seeded row, e.g. 'contacts:<slug>'. Unique among the
    -- rows that carry one, so re-seeding the address book is idempotent.
    source_ref    TEXT NOT NULL DEFAULT '',
    profile_json  TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_uw_entities_live
    ON uw_entities(merged_into, kind);
CREATE INDEX IF NOT EXISTS idx_uw_entities_key
    ON uw_entities(canonical_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_uw_entities_source_ref
    ON uw_entities(source_ref) WHERE source_ref != '';

-- Raw handles mapped many-to-one onto an entity. `value` is ALWAYS the
-- normalized form (that is what makes a match a match); `display_value` keeps
-- the spelling a human would recognize.
--
-- The uniqueness is per ENTITY, not global, on purpose: the same name
-- legitimately sits on two different people, and after an unmerge both sides
-- must be able to hold their own copy again. A deterministic identifier
-- appearing on a second entity is not a constraint violation — it is the
-- merge trigger.
CREATE TABLE IF NOT EXISTS uw_identifiers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id     INTEGER NOT NULL REFERENCES uw_entities(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL
                  CHECK (kind IN ('email', 'phone', 'contact', 'handle', 'name')),
    value         TEXT NOT NULL,
    display_value TEXT NOT NULL DEFAULT '',
    -- Denormalized length of `value`: the indexed half of the fuzzy-candidate
    -- block (identity.LEN_WINDOW), so a near-name lookup never scans the table.
    value_len     INTEGER NOT NULL DEFAULT 0,
    source_ref    TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_uw_identifiers_unique
    ON uw_identifiers(entity_id, kind, value);
CREATE INDEX IF NOT EXISTS idx_uw_identifiers_value
    ON uw_identifiers(kind, value);
CREATE INDEX IF NOT EXISTS idx_uw_identifiers_len
    ON uw_identifiers(kind, value_len);
CREATE INDEX IF NOT EXISTS idx_uw_identifiers_entity
    ON uw_identifiers(entity_id);

-- Proposed merges awaiting one human decision. `pair_key` is UNIQUE and
-- order-independent, so re-observing the same weak evidence refreshes the one
-- open proposal instead of stacking duplicates — and a REJECTED pair is never
-- asked about again (anti-confirmation-fatigue).
CREATE TABLE IF NOT EXISTS uw_confirm_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_key        TEXT NOT NULL UNIQUE,
    left_entity_id  INTEGER NOT NULL REFERENCES uw_entities(id) ON DELETE CASCADE,
    right_entity_id INTEGER NOT NULL REFERENCES uw_entities(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'confirmed', 'rejected')),
    score           REAL NOT NULL DEFAULT 0,
    evidence_json   TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    decided_at      TEXT,
    decided_by      TEXT
);

CREATE INDEX IF NOT EXISTS idx_uw_confirm_queue_status
    ON uw_confirm_queue(status, score DESC, id);

-- Every applied merge, automatic or confirmed, with the evidence that
-- justified it and the undo payload that reverses it.
--
-- Deliberately NO foreign keys onto uw_entities: the audit trail must survive
-- a purge that removes the entities themselves. A record of what happened is
-- worth nothing if it disappears together with the thing it happened to.
CREATE TABLE IF NOT EXISTS uw_merge_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    winner_id     INTEGER NOT NULL,
    loser_id      INTEGER NOT NULL,
    tier          TEXT NOT NULL CHECK (tier IN ('deterministic', 'probable')),
    reason        TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    undo_json     TEXT NOT NULL DEFAULT '{}',
    queue_id      INTEGER,
    merged_at     TEXT NOT NULL,
    undone_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_uw_merge_log_winner
    ON uw_merge_log(winner_id, undone_at);
CREATE INDEX IF NOT EXISTS idx_uw_merge_log_loser
    ON uw_merge_log(loser_id, undone_at);

COMMIT;
