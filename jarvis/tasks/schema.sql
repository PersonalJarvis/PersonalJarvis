-- Personal Jarvis — Task-Queue Schema (Phase 5, ADR-0003)
--
-- Additives Schema fuer die persistente Task-Queue, wird additiv auf die
-- Memory-DB (`data/jarvis.db`) angewandt. Alle CREATEs sind idempotent
-- (IF NOT EXISTS), sodass beim TaskStore.init() mehrfaches Ausfuehren
-- keine Probleme macht.
--
-- WAL-Mode + busy_timeout sind schon durch `jarvis/memory/schema.sql`
-- aktiv und muessen hier nicht nochmal gesetzt werden.

CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,             -- UUID4 als str
    trace_id        TEXT NOT NULL,
    spec_json       TEXT NOT NULL,                -- serialized TaskSpec (Pydantic)
    state           TEXT NOT NULL CHECK(state IN (
                        'pending','scheduled','running','completed',
                        'failed','cancelled','interrupted')),
    trigger_type    TEXT NOT NULL CHECK(trigger_type IN (
                        'after_delay','at_time','on_event','every')),
    due_at_ns       INTEGER,                      -- NULL fuer on_event
    event_selector  TEXT,                         -- nur fuer on_event (Event-Klasse)
    title           TEXT NOT NULL DEFAULT '',
    created_at_ns   INTEGER NOT NULL,
    started_at_ns   INTEGER,
    finished_at_ns  INTEGER,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    result_json     TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_state_due ON tasks(state, due_at_ns);
CREATE INDEX IF NOT EXISTS idx_tasks_trace     ON tasks(trace_id);
CREATE INDEX IF NOT EXISTS idx_tasks_event_sel ON tasks(event_selector);

CREATE TABLE IF NOT EXISTS task_steps (
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    kind            TEXT NOT NULL,                -- 'observation'|'action'|'verify'|'log'
    payload_json    TEXT NOT NULL,
    timestamp_ns    INTEGER NOT NULL,
    PRIMARY KEY (task_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_task_steps_ts ON task_steps(task_id, timestamp_ns);
