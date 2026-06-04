-- Phase-6 Mission-Event-Store-Schema. Idempotent (CREATE IF NOT EXISTS).
-- Lebt unter data/missions.db (separater Lifecycle vs. data/jarvis.db der
-- Phase-5-Memory/Tasks).

CREATE TABLE IF NOT EXISTS missions (
    id              TEXT PRIMARY KEY,           -- UUIDv7
    prompt          TEXT NOT NULL,
    state           TEXT NOT NULL,              -- MissionState.value
    language        TEXT NOT NULL DEFAULT 'de',
    created_ms      INTEGER NOT NULL,
    updated_ms      INTEGER NOT NULL,
    -- Phase-3 (Critic-Loop): pro Mission-Iterations-Counter + Cost-Akkumulator.
    -- Bestehende DBs werden via _apply_migrations() in event_store.py upgegradet
    -- (SQLite hat kein ADD COLUMN IF NOT EXISTS).
    iteration       INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_missions_state ON missions(state);
CREATE INDEX IF NOT EXISTS idx_missions_created ON missions(created_ms);

CREATE TABLE IF NOT EXISTS mission_events (
    seq               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id          TEXT NOT NULL UNIQUE,     -- UUIDv7
    mission_id        TEXT NOT NULL,
    event_type        TEXT NOT NULL,            -- Discriminator (MissionDispatched, ...)
    parent_event_id   TEXT,
    worker_id         TEXT,
    source_actor      TEXT NOT NULL,            -- hauptjarvis|kontrollierer|worker|critic|ui|system
    ts_ms             INTEGER NOT NULL,
    schema_version    INTEGER NOT NULL DEFAULT 1,
    payload_json      TEXT NOT NULL             -- Pydantic model_dump_json() output
);

CREATE INDEX IF NOT EXISTS idx_events_mission ON mission_events(mission_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_type ON mission_events(event_type, seq);
