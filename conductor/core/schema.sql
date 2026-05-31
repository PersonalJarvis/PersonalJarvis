-- Conductor SQL-Schema. Additiv; CREATE IF NOT EXISTS damit wiederholtes
-- Laden einer bestehenden DB keine Fehler wirft.

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    spec_json       TEXT NOT NULL,          -- JobSpec (typed union)
    schedule_json   TEXT NOT NULL,          -- Schedule (typed union)
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at_ns   INTEGER NOT NULL,
    tags_json       TEXT NOT NULL DEFAULT '[]',
    -- denormalized fields fuer schnelle Queries im Scheduler
    type            TEXT NOT NULL,          -- 'shell' | 'http' | 'agent'
    schedule_type   TEXT NOT NULL,          -- 'cron' | 'interval' | 'manual' | 'webhook'
    schedule_expr   TEXT,                   -- Cron-Expr oder Interval-Sec als String
    webhook_token   TEXT UNIQUE,            -- nur bei schedule_type='webhook'
    last_run_at_ns  INTEGER,
    last_run_state  TEXT,
    next_run_at_ns  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_jobs_next
    ON jobs (enabled, next_run_at_ns);
CREATE INDEX IF NOT EXISTS idx_jobs_webhook
    ON jobs (webhook_token) WHERE webhook_token IS NOT NULL;


CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'pending',
    trigger         TEXT NOT NULL DEFAULT 'manual',
    started_at_ns   INTEGER NOT NULL DEFAULT 0,
    finished_at_ns  INTEGER NOT NULL DEFAULT 0,
    exit_code       INTEGER,
    output          TEXT NOT NULL DEFAULT '',
    error           TEXT,
    input_json      TEXT NOT NULL DEFAULT '{}',
    metrics_json    TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_runs_job
    ON runs (job_id, started_at_ns DESC);
CREATE INDEX IF NOT EXISTS idx_runs_timeline
    ON runs (started_at_ns DESC);


CREATE TABLE IF NOT EXISTS run_steps (
    run_id         TEXT NOT NULL,
    seq            INTEGER NOT NULL,
    kind           TEXT NOT NULL,
    label          TEXT NOT NULL DEFAULT '',
    started_at_ns  INTEGER NOT NULL,
    finished_at_ns INTEGER,
    success        INTEGER,
    payload_json   TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, seq),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
