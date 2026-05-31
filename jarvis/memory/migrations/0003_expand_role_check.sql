-- Migration 0003: Expand `messages.role` CHECK constraint.
--
-- Old set: ('user','assistant','system','tool')
-- New set: ('user','assistant','system','tool','computer_use','announcement')
--
-- Rationale
-- =========
-- F6 / BUG-019 / AP-UF13 — on 2026-05-15, 49 occurrences of
-- `sqlite3.IntegrityError: CHECK constraint failed: role IN
-- ('user','assistant','system','tool')` were observed in a single
-- day's `data/jarvis_desktop.log`. Producers had grown new role
-- values (`computer_use` and `announcement`) without the schema
-- side learning about them. This migration makes the F6 fix a
-- permanent part of the schema so producers and the CHECK never
-- drift apart and re-discover the same constraint trap.
--
-- The canonical Python source for the vocabulary is
-- `jarvis/memory/constants.py::ALLOWED_ROLES`; the recorder
-- (`jarvis/memory/message_recorder.py`) and the test
-- `tests/unit/memory/test_role_constraint.py` are kept in lockstep
-- with that tuple.
--
-- SQLite restrictions
-- ===================
-- SQLite cannot ALTER a CHECK constraint in place; the only path
-- is the documented "12-step recipe" — copy data into a new table
-- with the desired constraint, swap names, recreate dependent
-- objects. We use a slim version of that here because the schema
-- of `messages` is small and there is exactly one dependent
-- structure (the `messages_fts` virtual table) whose external-
-- content index is keyed on `messages.id` and stays consistent as
-- long as we preserve ids during the copy.

BEGIN;

PRAGMA foreign_keys = OFF;

-- Drop the AFTER-row triggers so the bulk INSERT below does not
-- thrash the FTS index. They are recreated at the end of the
-- migration.
DROP TRIGGER IF EXISTS messages_ai;
DROP TRIGGER IF EXISTS messages_ad;
DROP TRIGGER IF EXISTS messages_au;

-- Stage table with the expanded CHECK. Column definitions mirror
-- jarvis/memory/schema.sql one-for-one; the only change is the
-- CHECK clause.
CREATE TABLE messages_migration_0003 (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id     TEXT NOT NULL,
    thread_id    TEXT,
    timestamp_ns INTEGER NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('user','assistant','system','tool','computer_use','announcement')),
    text         TEXT NOT NULL,
    tool_calls   TEXT,
    reasoning    TEXT,
    provider     TEXT,
    model        TEXT,
    tokens_in    INTEGER DEFAULT 0,
    tokens_out   INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Copy preserving rowids so the messages_fts external-content
-- index stays addressable against the new table after the rename.
INSERT INTO messages_migration_0003 (
    id, trace_id, thread_id, timestamp_ns, role, text,
    tool_calls, reasoning, provider, model,
    tokens_in, tokens_out, created_at
)
SELECT
    id, trace_id, thread_id, timestamp_ns, role, text,
    tool_calls, reasoning, provider, model,
    tokens_in, tokens_out, created_at
FROM messages;

DROP TABLE messages;

ALTER TABLE messages_migration_0003 RENAME TO messages;

CREATE INDEX IF NOT EXISTS idx_messages_trace     ON messages(trace_id);
CREATE INDEX IF NOT EXISTS idx_messages_thread    ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_messages_role      ON messages(role);

-- Recreate FTS sync triggers. They are identical to the
-- definitions in schema.sql; restated here so the migration is
-- self-contained on existing databases.
CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text, tool_calls, reasoning)
    VALUES (new.id, new.text, coalesce(new.tool_calls, ''), coalesce(new.reasoning, ''));
END;

CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text, tool_calls, reasoning)
    VALUES ('delete', old.id, old.text, coalesce(old.tool_calls, ''), coalesce(old.reasoning, ''));
END;

CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text, tool_calls, reasoning)
    VALUES ('delete', old.id, old.text, coalesce(old.tool_calls, ''), coalesce(old.reasoning, ''));
    INSERT INTO messages_fts(rowid, text, tool_calls, reasoning)
    VALUES (new.id, new.text, coalesce(new.tool_calls, ''), coalesce(new.reasoning, ''));
END;

PRAGMA foreign_keys = ON;

COMMIT;
