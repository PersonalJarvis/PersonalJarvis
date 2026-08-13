-- Personal Jarvis — Errand schema (autonomous missions, C1)
--
-- Additive on the memory DB (`data/jarvis.db`), like the task and workflow
-- schemas next to it. Every CREATE is idempotent so repeated init() is safe.
-- WAL mode and busy_timeout are already set by jarvis/memory/schema.sql.
--
-- The whole point of this table is C1's "survives a restart": an errand that
-- lost its plan on restart would resume a half-processed booking from zero.

CREATE TABLE IF NOT EXISTS errands (
    id              TEXT PRIMARY KEY,             -- uuid4 as str
    goal            TEXT NOT NULL,
    state           TEXT NOT NULL CHECK(state IN (
                        'planning','needs_input','running',
                        'completed','stalled','impossible','cancelled')),
    record_json     TEXT NOT NULL,                -- the full Errand model
    trace_id        TEXT NOT NULL DEFAULT '',
    created_at_ns   INTEGER NOT NULL,
    updated_at_ns   INTEGER NOT NULL
);

-- Resume path: the runner asks for every non-terminal errand at startup, so
-- this index carries the only hot query.
CREATE INDEX IF NOT EXISTS idx_errands_state ON errands(state, updated_at_ns);
