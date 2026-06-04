-- Personal Jarvis — Recall-Memory Schema (SQLite FTS5)
--
-- Idempotent: alle CREATEs nutzen "IF NOT EXISTS". Kann bei jedem Start
-- der App gegen die DB gefahren werden.

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;

-- Main table: one row per persisted message turn.
-- The `role` column is kept in sync with jarvis.memory.constants.ALLOWED_ROLES;
-- accepted values: user | assistant | system | tool | computer_use | announcement.
-- See docs/anti-drift-three-layer.md for the parity-test pattern; see migration
-- 0003_expand_role_check.sql for the historical CHECK widening that introduced
-- `computer_use` and `announcement`.
CREATE TABLE IF NOT EXISTS messages (
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

CREATE INDEX IF NOT EXISTS idx_messages_trace     ON messages(trace_id);
CREATE INDEX IF NOT EXISTS idx_messages_thread    ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp_ns);
CREATE INDEX IF NOT EXISTS idx_messages_role      ON messages(role);

-- FTS5-Virtual-Table für Volltext-Suche. External-Content-Pattern:
-- der Index bezieht sich auf `messages.id` und wird nur über Triggers gefüllt.
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text,
    tool_calls,
    reasoning,
    content='messages',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- Triggers für Auto-Sync zwischen messages und messages_fts
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text, tool_calls, reasoning)
    VALUES (new.id, new.text, coalesce(new.tool_calls, ''), coalesce(new.reasoning, ''));
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text, tool_calls, reasoning)
    VALUES ('delete', old.id, old.text, coalesce(old.tool_calls, ''), coalesce(old.reasoning, ''));
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text, tool_calls, reasoning)
    VALUES ('delete', old.id, old.text, coalesce(old.tool_calls, ''), coalesce(old.reasoning, ''));
    INSERT INTO messages_fts(rowid, text, tool_calls, reasoning)
    VALUES (new.id, new.text, coalesce(new.tool_calls, ''), coalesce(new.reasoning, ''));
END;

-- Small-KV-Store für beliebige Namespaces (für MemoryStore.put/get/forget).
CREATE TABLE IF NOT EXISTS kv_store (
    namespace   TEXT NOT NULL,
    key         TEXT NOT NULL,
    value_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (namespace, key)
);

CREATE INDEX IF NOT EXISTS idx_kv_namespace ON kv_store(namespace);

-- ---------------------------------------------------------------------------
-- Awareness L2 — Story Tracker (Phase A2, Plan §6)
-- ---------------------------------------------------------------------------
-- Zwei Tabellen: `awareness_frames` (rohe Window-Snapshots fuer L3-Search) und
-- `awareness_episodes` (verdichtete 150-Wort-Summaries vom Verdichter-Haiku).
-- FTS5-Index nur auf Episodes (Frames werden nicht gesucht, nur archiviert).
-- Idempotent via CREATE TABLE IF NOT EXISTS — `executescript()` darf bei jedem
-- App-Start gefahren werden.

CREATE TABLE IF NOT EXISTS awareness_frames (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ns    INTEGER NOT NULL,
    window_title    TEXT NOT NULL,
    process_name    TEXT NOT NULL,
    salience_score  INTEGER NOT NULL,    -- 0..100, vom SalienceScorer
    metadata_json   TEXT,                 -- git_branch, open_file_hint, etc.
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_aw_frames_ts ON awareness_frames(timestamp_ns);

CREATE TABLE IF NOT EXISTS awareness_episodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at_ns   INTEGER NOT NULL,
    ended_at_ns     INTEGER NOT NULL,
    trigger_kind    TEXT NOT NULL,        -- "window_switch" | "file_save" | "terminal_exit" | "brain_turn" | "timer" | "idle_entered"
    summary         TEXT NOT NULL,        -- Verdichter-Output, Markdown, ~150 Worte
    frame_count     INTEGER NOT NULL,
    primary_app     TEXT NOT NULL,        -- meist-zeit-aktive App in der Episode
    tokens_in       INTEGER DEFAULT 0,
    tokens_out      INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_aw_eps_ts ON awareness_episodes(started_at_ns);

-- FTS5-Index ueber Episode-Summary fuer L3-Recall ("zeig mir was ich
-- gestern an Python gemacht habe"). External-Content-Pattern: Index referenziert
-- `awareness_episodes.id` und wird via AFTER-INSERT-Trigger gefuellt.
CREATE VIRTUAL TABLE IF NOT EXISTS awareness_episodes_fts USING fts5(
    summary,
    primary_app,
    trigger_kind,
    content='awareness_episodes',
    content_rowid='id'
);

-- Trigger fuer FTS-Sync (nach awareness_episodes-Insert)
CREATE TRIGGER IF NOT EXISTS awareness_episodes_ai AFTER INSERT ON awareness_episodes
BEGIN
    INSERT INTO awareness_episodes_fts(rowid, summary, primary_app, trigger_kind)
    VALUES (new.id, new.summary, new.primary_app, new.trigger_kind);
END;
