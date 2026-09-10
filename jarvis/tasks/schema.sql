-- Persistent task queue, additive to the memory database.
-- WAL and busy_timeout are configured by TaskStore.

CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,             -- UUID4 as string
    trace_id        TEXT NOT NULL,
    spec_json       TEXT NOT NULL,                -- serialized TaskSpec (Pydantic)
    state           TEXT NOT NULL CHECK(state IN (
                        'pending','scheduled','paused','running','completed',
                        'failed','cancelled','interrupted')),
    trigger_type    TEXT NOT NULL CHECK(trigger_type IN (
                        'after_delay','at_time','on_event','every','calendar','webhook','event_hook','source','cron')),
    due_at_ns       INTEGER,                      -- NULL for on_event
    event_selector  TEXT,                         -- on_event only (event class)
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
