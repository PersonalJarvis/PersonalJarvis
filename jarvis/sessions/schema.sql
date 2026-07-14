-- Voice session recorder schema (Transcription view feature).
-- Idempotent (CREATE IF NOT EXISTS). Stored in data/sessions.db with a
-- separate lifecycle from data/jarvis.db (memory) and data/missions.db
-- (Phase 6 worker/critic).
--
-- Three-table layout:
--   voice_sessions  : Header row per session (wake -> hangup).
--   voice_turns     : Aggregate per turn (user utterance + Jarvis answer).
--   voice_events    : Raw event stream for detail replay (every relevant bus
--                     event during a session as a JSON payload).

CREATE TABLE IF NOT EXISTS voice_sessions (
    id                 TEXT PRIMARY KEY,         -- session_id (UUIDv4 string)
    started_ms         INTEGER NOT NULL,         -- wake timestamp
    ended_ms           INTEGER,                  -- NULL while the session is active
    hangup_reason      TEXT,                     -- voice_pattern|hotkey|client_stop|ws_closed|realtime_fallback|idle_timeout|turn_complete|shutdown|error
    turn_count         INTEGER NOT NULL DEFAULT 0,
    total_cost_usd     REAL NOT NULL DEFAULT 0.0,
    total_tokens_in    INTEGER NOT NULL DEFAULT 0,
    total_tokens_out   INTEGER NOT NULL DEFAULT 0,
    providers_used     TEXT NOT NULL DEFAULT '[]',  -- JSON array of distinct provider names
    language           TEXT NOT NULL DEFAULT 'de',
    wake_keyword       TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_sessions_started ON voice_sessions(started_ms DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_open ON voice_sessions(ended_ms) WHERE ended_ms IS NULL;


CREATE TABLE IF NOT EXISTS voice_turns (
    id                 TEXT PRIMARY KEY,         -- turn_id (UUIDv4 string)
    session_id         TEXT NOT NULL REFERENCES voice_sessions(id) ON DELETE CASCADE,
    idx                INTEGER NOT NULL,         -- zero-based turn position in session
    started_ms         INTEGER NOT NULL,         -- turn start (user starts speaking)
    ended_ms           INTEGER,                  -- AudioOutFirst (Jarvis finished)
    user_text          TEXT NOT NULL DEFAULT '',
    user_lang          TEXT NOT NULL DEFAULT 'de',
    jarvis_text        TEXT NOT NULL DEFAULT '',
    jarvis_lang        TEXT NOT NULL DEFAULT 'de',
    tier               TEXT NOT NULL DEFAULT '', -- router|openclaw|sub_jarvis|trivial|fast|deep|code|realtime|''
    provider           TEXT NOT NULL DEFAULT '',
    model              TEXT NOT NULL DEFAULT '',
    tokens_in          INTEGER NOT NULL DEFAULT 0,
    tokens_out         INTEGER NOT NULL DEFAULT 0,
    cost_usd           REAL NOT NULL DEFAULT 0.0,
    latency_total_ms   INTEGER NOT NULL DEFAULT 0,
    -- Latency breakdown (from recorder SystemStateChanged boundaries):
    --   think_ms : TranscriptFinal -> SystemStateChanged(SPEAKING)
    --              = Jarvis think time (user done until Jarvis starts speaking)
    --   speak_ms : SystemStateChanged(SPEAKING) -> SystemStateChanged(LISTENING)
    --              = Jarvis speaking time (TTS playback duration)
    think_ms           INTEGER NOT NULL DEFAULT 0,
    speak_ms           INTEGER NOT NULL DEFAULT 0,
    -- 1 when the turn ended on a two-turn voice/chat confirmation
    -- (finish_reason="voice_confirm_pending"): the reply is a pending yes/no
    -- question, not a settled answer. Also added via _apply_migrations for
    -- pre-existing DBs (the idempotent PRAGMA-guard makes both paths safe).
    awaiting_confirmation INTEGER NOT NULL DEFAULT 0,
    tool_calls_json    TEXT NOT NULL DEFAULT '[]'  -- JSON array of tool-name strings
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON voice_turns(session_id, idx);


-- Store-level bookkeeping. Currently holds the retention-prune horizon
-- (key ``prune_horizon_ms``): the highest cutoff ever used by
-- ``prune_older_than``. Readers (the board aggregator) use it to tell
-- "rows were deleted below this instant" from "rows never existed", so an
-- already-recorded day is never recomputed from a half-pruned source.
CREATE TABLE IF NOT EXISTS store_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS voice_events (
    seq                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id         TEXT NOT NULL REFERENCES voice_sessions(id) ON DELETE CASCADE,
    turn_id            TEXT REFERENCES voice_turns(id) ON DELETE SET NULL,
    ts_ms              INTEGER NOT NULL,
    kind               TEXT NOT NULL,            -- event type (e.g. WakeWordDetected, TranscriptFinal, ToolCallCompleted)
    payload_json       TEXT NOT NULL DEFAULT '{}'  -- selected fields as JSON
);

CREATE INDEX IF NOT EXISTS idx_events_session ON voice_events(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_turn ON voice_events(turn_id, seq);
