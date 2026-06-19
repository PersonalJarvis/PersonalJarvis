-- Board Phase A Schema. Additiv + idempotent — kein Alembic.
-- Alle Daten aus FlightRecorder-JSONL aggregiert, lokal, offline.

PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;

-- Eine Zeile pro Tag mit aggregierten Safe-Fields.
-- Rohe Event-Payloads (text, utterance, args) werden NIE gespeichert.
CREATE TABLE IF NOT EXISTS daily_stats (
    date                  TEXT PRIMARY KEY,          -- ISO-Date YYYY-MM-DD
    tasks_completed       INTEGER NOT NULL DEFAULT 0,
    tasks_failed          INTEGER NOT NULL DEFAULT 0,
    tools_used            TEXT    NOT NULL DEFAULT '[]',  -- JSON-Array
    unique_tools_count    INTEGER NOT NULL DEFAULT 0,
    voice_commands_count  INTEGER NOT NULL DEFAULT 0,
    voice_first_try_rate  REAL,                      -- 0.0..1.0 oder NULL
    hours_saved_estimate  REAL    NOT NULL DEFAULT 0.0,
    active_events_count   INTEGER NOT NULL DEFAULT 0,
    conversation_seconds_estimate REAL NOT NULL DEFAULT 0.0,
    -- Dictation-style word counts, derived from sessions.db voice_turns.
    -- Raw text is counted at aggregation time and never stored.
    user_words_count      INTEGER NOT NULL DEFAULT 0,   -- words the user spoke/typed
    jarvis_words_count    INTEGER NOT NULL DEFAULT 0,   -- words Jarvis spoke back
    session_count         INTEGER NOT NULL DEFAULT 0,   -- voice sessions started this day
    -- Usage-by-category: JSON object {category_key: invocation_count}, derived
    -- from per-turn tool calls via jarvis.board.categories.categorize_tool.
    category_counts       TEXT    NOT NULL DEFAULT '{}',
    created_at            TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Achievement-Platzhalter — Phase-B-Payload schon vorbereitet, damit
-- Phase A und B dieselbe DB-Datei nutzen ohne Migration.
CREATE TABLE IF NOT EXISTS achievements (
    id           TEXT PRIMARY KEY,
    unlocked_at  TEXT NOT NULL,
    evidence     TEXT                                 -- JSON
);

-- Personal Records — ein Eintrag pro Metrik.
CREATE TABLE IF NOT EXISTS personal_records (
    metric       TEXT PRIMARY KEY,
    value        REAL NOT NULL,
    achieved_on  TEXT NOT NULL,                       -- ISO-Date
    context      TEXT                                 -- JSON-Blob
);

-- Metadaten-Tabelle fuer Aggregator-Run-Tracking (Startup-Resume etc.).
CREATE TABLE IF NOT EXISTS aggregator_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- AI-generierte Kurzbio (Phase B). Append-only Log; die aktuellste Zeile
-- (MAX(generated_at)) wird im UI gerendert. Kein Overwrite, damit History
-- eingesehen werden kann.
CREATE TABLE IF NOT EXISTS bio (
    generated_at  TEXT PRIMARY KEY,         -- ISO-UTC
    text          TEXT NOT NULL,
    model_used    TEXT,
    triggered_by  TEXT                       -- "weekly" | "manual" | "milestone" | "cold_start"
);

-- User-Reaktion auf eine Bio (Brainstorm 2026-05-02): drei Buttons unter
-- dem Profil-Text — Trifft / Trifft nicht / Haerter. Append-only; aggregiert
-- als Tone-Vector in den Prompt der naechsten Bio-Generation.
CREATE TABLE IF NOT EXISTS bio_feedback (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    bio_generated_at  TEXT    NOT NULL,
    kind              TEXT    NOT NULL CHECK(kind IN ('trifft','trifft_nicht','haerter')),
    created_at        TEXT    NOT NULL,
    FOREIGN KEY (bio_generated_at) REFERENCES bio(generated_at)
);

CREATE INDEX IF NOT EXISTS idx_bio_feedback_kind_time
    ON bio_feedback(kind, created_at DESC);
