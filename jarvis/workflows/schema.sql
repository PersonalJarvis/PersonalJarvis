-- Workflow-System Schema (Phase 6).
-- Additiv zum Memory-DB-Schema; alle Tabellen mit IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS workflows (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    def_json        TEXT NOT NULL,          -- WorkflowDef als JSON
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at_ns   INTEGER NOT NULL,
    created_by      TEXT NOT NULL DEFAULT 'user',
    trigger_type    TEXT NOT NULL,          -- 'manual' | 'cron'
    cron_expression TEXT,                   -- NULL wenn manual
    last_run_at_ns  INTEGER,
    last_run_state  TEXT,                   -- 'completed' | 'failed' | NULL
    next_run_at_ns  INTEGER                 -- fuer cron, sonst NULL
);

CREATE INDEX IF NOT EXISTS idx_workflows_enabled
    ON workflows (enabled, next_run_at_ns);


CREATE TABLE IF NOT EXISTS workflow_runs (
    id              TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'pending',
    trigger         TEXT NOT NULL DEFAULT 'manual',
    started_at_ns   INTEGER NOT NULL DEFAULT 0,
    finished_at_ns  INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    input_json      TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (workflow_id) REFERENCES workflows(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_runs_workflow
    ON workflow_runs (workflow_id, started_at_ns DESC);


CREATE TABLE IF NOT EXISTS workflow_run_steps (
    run_id         TEXT NOT NULL,
    seq            INTEGER NOT NULL,
    kind           TEXT NOT NULL,
    label          TEXT NOT NULL DEFAULT '',
    started_at_ns  INTEGER NOT NULL,
    finished_at_ns INTEGER,
    success        INTEGER,                 -- NULL solange running
    output         TEXT NOT NULL DEFAULT '',
    error          TEXT,
    PRIMARY KEY (run_id, seq),
    FOREIGN KEY (run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
);
