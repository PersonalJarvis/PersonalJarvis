-- Voice-Session-Recorder-Schema (Transcription-View Feature).
-- Idempotent (CREATE IF NOT EXISTS). Lebt unter data/sessions.db,
-- separater Lifecycle vs. data/jarvis.db (Memory) und data/missions.db
-- (Phase-6 Worker-Critic).
--
-- Drei-Tabellen-Layout:
--   voice_sessions  : Header-Row pro Session (Wake -> Hangup).
--   voice_turns     : Aggregat pro Turn (User-Utterance + Jarvis-Antwort).
--   voice_events    : Roh-Event-Stream fuer Detail-Replay (jedes relevante
--                     Bus-Event waehrend einer Session als JSON-Payload).

CREATE TABLE IF NOT EXISTS voice_sessions (
    id                 TEXT PRIMARY KEY,         -- session_id (UUIDv4-String)
    started_ms         INTEGER NOT NULL,         -- Wake-Zeitpunkt
    ended_ms           INTEGER,                  -- NULL solange Session laeuft
    hangup_reason      TEXT,                     -- voice_pattern|hotkey|idle_timeout|turn_complete|shutdown|error
    turn_count         INTEGER NOT NULL DEFAULT 0,
    total_cost_usd     REAL NOT NULL DEFAULT 0.0,
    total_tokens_in    INTEGER NOT NULL DEFAULT 0,
    total_tokens_out   INTEGER NOT NULL DEFAULT 0,
    providers_used     TEXT NOT NULL DEFAULT '[]',  -- JSON-Array distinct provider names
    language           TEXT NOT NULL DEFAULT 'de',
    wake_keyword       TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_sessions_started ON voice_sessions(started_ms DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_open ON voice_sessions(ended_ms) WHERE ended_ms IS NULL;


CREATE TABLE IF NOT EXISTS voice_turns (
    id                 TEXT PRIMARY KEY,         -- turn_id (UUIDv4-String)
    session_id         TEXT NOT NULL REFERENCES voice_sessions(id) ON DELETE CASCADE,
    idx                INTEGER NOT NULL,         -- 0-basierte Turn-Position innerhalb Session
    started_ms         INTEGER NOT NULL,         -- TurnStart (User beginnt zu sprechen)
    ended_ms           INTEGER,                  -- AudioOutFirst (Jarvis fertig)
    user_text          TEXT NOT NULL DEFAULT '',
    user_lang          TEXT NOT NULL DEFAULT 'de',
    jarvis_text        TEXT NOT NULL DEFAULT '',
    jarvis_lang        TEXT NOT NULL DEFAULT 'de',
    tier               TEXT NOT NULL DEFAULT '', -- router|openclaw|sub_jarvis|trivial|fast|deep|code|''
    provider           TEXT NOT NULL DEFAULT '',
    model              TEXT NOT NULL DEFAULT '',
    tokens_in          INTEGER NOT NULL DEFAULT 0,
    tokens_out         INTEGER NOT NULL DEFAULT 0,
    cost_usd           REAL NOT NULL DEFAULT 0.0,
    latency_total_ms   INTEGER NOT NULL DEFAULT 0,
    -- Aufgeschluesselte Latenzen (vom Recorder via SystemStateChanged-Boundaries):
    --   think_ms : TranscriptFinal -> SystemStateChanged(SPEAKING)
    --              = wie lang Jarvis "nachgedacht" hat (User-Done bis Jarvis-spricht-Start)
    --   speak_ms : SystemStateChanged(SPEAKING) -> SystemStateChanged(LISTENING)
    --              = wie lang Jarvis gesprochen hat (TTS-Playback-Dauer)
    think_ms           INTEGER NOT NULL DEFAULT 0,
    speak_ms           INTEGER NOT NULL DEFAULT 0,
    tool_calls_json    TEXT NOT NULL DEFAULT '[]'  -- JSON-Array of tool-name strings
);

CREATE INDEX IF NOT EXISTS idx_turns_session ON voice_turns(session_id, idx);


CREATE TABLE IF NOT EXISTS voice_events (
    seq                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id         TEXT NOT NULL REFERENCES voice_sessions(id) ON DELETE CASCADE,
    turn_id            TEXT REFERENCES voice_turns(id) ON DELETE SET NULL,
    ts_ms              INTEGER NOT NULL,
    kind               TEXT NOT NULL,            -- Event-Typ (z.B. WakeWordDetected, TranscriptFinal, ToolCallCompleted)
    payload_json       TEXT NOT NULL DEFAULT '{}'  -- selektierte Felder als JSON
);

CREATE INDEX IF NOT EXISTS idx_events_session ON voice_events(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_turn ON voice_events(turn_id, seq);
