---
title: "ADR-0003: Task-Queue in Memory-DB"
slug: adr-0003-task-queue-storage
diataxis: adr
status: active
owner: sam
last_reviewed: 2026-04-29
phase: 5
audience: developer
---

# ADR-0003 — Task-queue storage in the existing memory DB

**Status:** Accepted  (2026-04-22)
**Phase:** 5 — Async Capability

## Context

The mandate requires persistent, crash-safe tasks (`"in zwei Stunden …"`, `"wenn Outlook eine Mail von Tom bekommt"`) with retry logic, UI visibility, and cancel capability. The question is where the persistent state lives.  # i18n-allow

## Decision

**Same SQLite file as memory (`data/jarvis.db`)**, two new tables, no separate DB.

```sql
CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,         -- UUID4
    trace_id        TEXT NOT NULL,
    spec_json       TEXT NOT NULL,            -- serialized TaskSpec (Pydantic)
    state           TEXT NOT NULL CHECK(state IN (
                        'pending','scheduled','running','completed',
                        'failed','cancelled','interrupted')),
    trigger_type    TEXT NOT NULL CHECK(trigger_type IN (
                        'after_delay','at_time','on_event')),
    due_at_ns       INTEGER,                   -- NULL for on_event
    event_selector  TEXT,                      -- only for on_event
    created_at_ns   INTEGER NOT NULL,
    started_at_ns   INTEGER,
    finished_at_ns  INTEGER,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    result_json     TEXT
);
CREATE INDEX idx_tasks_state_due ON tasks(state, due_at_ns);
CREATE INDEX idx_tasks_trace ON tasks(trace_id);

CREATE TABLE IF NOT EXISTS task_steps (
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    kind            TEXT NOT NULL,             -- 'observation'|'action'|'verify'|'log'
    payload_json    TEXT NOT NULL,
    timestamp_ns    INTEGER NOT NULL,
    PRIMARY KEY (task_id, seq)
);
```

**Transactionality:** State changes run in a single transaction with a step append. WAL mode is already active (Phase-2 schema).

**Startup cleanup:** On app start, all tasks with `state='running'` → `state='interrupted'`, plus an error log `"App exit detected"`. The retry policy (in TaskSpec) decides whether to auto-retry or prompt the user.

## Consequences

+ A single backup job copies `jarvis.db`, and memory + tasks are consistently in it.
+ No second connection pool, no second WAL file.
+ Simple join for queries like „alle Tasks, die Memory-Entries `X` produziert haben" ("all tasks that produced memory entries `X`") (via `trace_id`).
- Locking contention: when a RecallStore write (FTS5 trigger) is running and a task-state update is pending at the same time. Mitigation: WAL + `journal_mode=WAL` + `busy_timeout=5000` (already set).
- Schema migrations have to be coordinated. Introduce by extending `jarvis/memory/schema.sql` + a migration script on the setup-wizard re-run.

## Alternatives Considered

- **Separate `data/tasks.db`:** Clean separation, but duplicated backup logic and missing cross-references. Rejected.
- **JSON files per task in `data/tasks/`:** Not crash-safe (a partial write can tear the schema apart), no atomic state transition. Rejected.
- **Redis/SQLite queue libs (`huey`, `rq`):** Bring their own worker model, which does not fit the event-bus pattern. +Dep. Rejected.
- **APScheduler with a SQLAlchemy job store:** Handled in ADR-0005, result: a lightweight in-house build.

## Open

- Auto-VACUUM interval: tasks remain X days after completion (config: `task_retention_days = 30`), then deletion + VACUUM every 24h.
- `result_json` can grow large (flight-recorder data). Limit: 256 KB, beyond that the blob goes into the flight-recorder JSONL and only the path is kept in the DB.
