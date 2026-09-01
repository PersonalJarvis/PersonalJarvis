-- Agent Society store. Idempotent (CREATE IF NOT EXISTS). Lives under
-- data/society.db with its own lifecycle, mirroring missions_schema.sql.
-- Every CHECK list below is pinned to the Python enums in jarvis/society/events.py
-- and the TypeScript consts in src/lib/societyApi.ts by
-- tests/unit/society/test_society_enum_parity.py (AP-4).

-- The roster: one row per agent = the model card. Deliberately NO chat-session
-- column: the canonical chat id is a pure function of agent_id (society:<id>),
-- so identity can never drift from its conversation.
CREATE TABLE IF NOT EXISTS society_agents (
    agent_id            TEXT PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    title               TEXT NOT NULL DEFAULT '',
    description         TEXT NOT NULL DEFAULT '',
    tier                TEXT NOT NULL CHECK (tier IN ('lead', 'orchestrator', 'specialist')),
    parent_agent_id     TEXT,
    state               TEXT NOT NULL DEFAULT 'active'
                        CHECK (state IN ('active', 'paused', 'archived')),
    avatar_json         TEXT NOT NULL DEFAULT '{}',
    checkpoint          TEXT NOT NULL DEFAULT 'idle'
                        CHECK (checkpoint IN ('desk', 'meeting', 'archive', 'gate', 'idle')),
    provider            TEXT NOT NULL DEFAULT '',
    model               TEXT NOT NULL DEFAULT '',
    effort              TEXT NOT NULL DEFAULT '',
    grant_mode          TEXT NOT NULL DEFAULT 'all'
                        CHECK (grant_mode IN ('all', 'allowlist')),
    grants_json         TEXT NOT NULL DEFAULT '[]',
    focus_json          TEXT NOT NULL DEFAULT '[]',
    denies_json         TEXT NOT NULL DEFAULT '[]',
    skills_json         TEXT,                       -- NULL = every active skill
    workspace_dir       TEXT NOT NULL DEFAULT '',
    wiki_namespace      TEXT NOT NULL DEFAULT '',
    knowledge_scope     TEXT NOT NULL DEFAULT 'shared'
                        CHECK (knowledge_scope IN ('shared', 'own')),
    permission_ceiling  TEXT NOT NULL DEFAULT 'monitor'
                        CHECK (permission_ceiling IN ('safe', 'monitor', 'ask')),
    approval_rules_json TEXT NOT NULL DEFAULT '{}',
    daily_budget_usd    REAL NOT NULL DEFAULT 2.0,
    max_concurrent_runs INTEGER NOT NULL DEFAULT 1,
    created_ms          INTEGER NOT NULL,
    updated_ms          INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_society_agents_state ON society_agents(state);

-- The board: append-only, typed, one stream for chat, world, ledger and cost.
CREATE TABLE IF NOT EXISTS society_events (
    seq                 INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id            TEXT NOT NULL UNIQUE,
    msg_type            TEXT NOT NULL CHECK (msg_type IN (
                            'ASSIGN', 'CLAIM', 'RESULT', 'QUERY', 'ANSWER', 'HOLD',
                            'RELEASE', 'PROPOSE', 'VETO', 'DIGEST', 'SAY',
                            'ROOM_OPEN', 'ROOM_SETTLE')),
    from_agent          TEXT NOT NULL,
    to_agent            TEXT,                       -- NULL = broadcast
    trace_id            TEXT NOT NULL,
    parent_event_id     TEXT,
    ts_ms               INTEGER NOT NULL,
    cost_usd            REAL NOT NULL DEFAULT 0.0,
    payload_json        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_society_events_inbox ON society_events(to_agent, seq);
CREATE INDEX IF NOT EXISTS idx_society_events_trace ON society_events(trace_id, seq);
CREATE INDEX IF NOT EXISTS idx_society_events_from ON society_events(from_agent, seq);

-- Bounded group discussions: state lives here, history in society_events
-- (msg_type ROOM_OPEN / SAY / ROOM_SETTLE sharing the room's trace_id).
CREATE TABLE IF NOT EXISTS society_rooms (
    room_id             TEXT PRIMARY KEY,
    trace_id            TEXT NOT NULL UNIQUE,
    opened_by           TEXT NOT NULL,
    topic               TEXT NOT NULL DEFAULT '',
    members_json        TEXT NOT NULL,
    round               INTEGER NOT NULL DEFAULT 0,
    message_count       INTEGER NOT NULL DEFAULT 0,
    state               TEXT NOT NULL DEFAULT 'queued'
                        CHECK (state IN ('queued', 'running', 'settled', 'failed')),
    settle_reason       TEXT NOT NULL DEFAULT '',
    created_ms          INTEGER NOT NULL,
    updated_ms          INTEGER NOT NULL
);

-- Knowledge staging: provenance and review state for pages agents wrote into
-- the vault. The vault holds the knowledge; this table only knows its taint.
CREATE TABLE IF NOT EXISTS knowledge (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id            TEXT NOT NULL,
    wiki_path           TEXT NOT NULL,
    origin              TEXT NOT NULL CHECK (origin IN ('user', 'tool', 'web', 'agent')),
    source_event        TEXT,
    trace_id            TEXT,
    reviewed            INTEGER NOT NULL DEFAULT 0,
    summary             TEXT NOT NULL DEFAULT '',
    created_ms          INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_knowledge_agent ON knowledge(agent_id, created_ms);
CREATE INDEX IF NOT EXISTS idx_knowledge_reviewed ON knowledge(reviewed);

-- The unattended ask-queue (MASTERPLAN §2.9). Expiry parks work as blocked,
-- never drops it.
CREATE TABLE IF NOT EXISTS approvals (
    id                  TEXT PRIMARY KEY,
    agent_id            TEXT NOT NULL,
    trace_id            TEXT NOT NULL,
    capability          TEXT NOT NULL,
    action_json         TEXT NOT NULL,
    summary             TEXT NOT NULL DEFAULT '',
    state               TEXT NOT NULL DEFAULT 'pending'
                        CHECK (state IN ('pending', 'approved', 'denied', 'expired', 'blocked')),
    created_ms          INTEGER NOT NULL,
    expires_ms          INTEGER NOT NULL,
    resolved_ms         INTEGER,
    note                TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_approvals_state ON approvals(state, created_ms);
CREATE INDEX IF NOT EXISTS idx_approvals_agent ON approvals(agent_id, state);

-- Kill switch, schema version and other single values.
CREATE TABLE IF NOT EXISTS society_meta (
    key                 TEXT PRIMARY KEY,
    value               TEXT NOT NULL
);
