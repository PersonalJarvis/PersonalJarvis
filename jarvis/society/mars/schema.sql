CREATE TABLE IF NOT EXISTS mars_meta (
    key TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS mars_commands (
    command_id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL CHECK (world_id = 'mars:ordinary'),
    station_id TEXT NOT NULL CHECK (station_id = 'communications-console'),
    capability_id TEXT NOT NULL CHECK (capability_id = 'communication-draft'),
    agent_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    trace_id TEXT NOT NULL UNIQUE,
    draft TEXT NOT NULL CHECK (length(draft) BETWEEN 1 AND 4000),
    state TEXT NOT NULL CHECK (
        state IN ('queued','active','completed','failed','canceled','interrupted','unknown')
    ),
    created_ms INTEGER NOT NULL,
    updated_ms INTEGER NOT NULL,
    fence INTEGER NOT NULL DEFAULT 0,
    task_ref TEXT,
    result_ref TEXT,
    reason TEXT NOT NULL DEFAULT '',
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    cancel_dispatched INTEGER NOT NULL DEFAULT 0 CHECK (cancel_dispatched IN (0, 1)),
    UNIQUE(world_id, agent_id, request_id)
);
CREATE INDEX IF NOT EXISTS mars_commands_pending ON mars_commands(state, created_ms);

-- The monotonically increasing generation survives every release and process restart.
CREATE TABLE IF NOT EXISTS mars_station (
    station_id TEXT PRIMARY KEY CHECK (station_id = 'communications-console'),
    generation INTEGER NOT NULL DEFAULT 0,
    command_id TEXT REFERENCES mars_commands(command_id),
    expires_ms INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mars_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('queued','active','completed','failed','canceled','interrupted','unknown')
    ),
    fence INTEGER NOT NULL,
    task_ref TEXT,
    result_ref TEXT,
    reason TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL,
    ts_ms INTEGER NOT NULL
);
