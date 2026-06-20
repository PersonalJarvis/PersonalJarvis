-- keyproxy schema — per-user tokens + best-effort usage metering.
--
-- Two tables, additive (CREATE TABLE IF NOT EXISTS) so the schema can be
-- re-applied on every boot. Real vendor keys are NEVER stored here; only the
-- SHA-256 of each issued token is persisted (the plaintext is shown once at
-- issue time and then unrecoverable).

CREATE TABLE IF NOT EXISTS tokens (
    id           TEXT PRIMARY KEY,         -- uuid4
    label        TEXT NOT NULL,            -- human label, e.g. "alice-laptop"
    token_sha256 TEXT NOT NULL UNIQUE,     -- hex sha256 of the plaintext token
    created_at   INTEGER NOT NULL,         -- unix seconds
    revoked_at   INTEGER                   -- unix seconds; NULL => active
);

CREATE INDEX IF NOT EXISTS idx_tokens_sha256 ON tokens (token_sha256);

CREATE TABLE IF NOT EXISTS usage (
    id                TEXT PRIMARY KEY,     -- uuid4
    token_id          TEXT,                 -- FK -> tokens.id (NULL if untracked)
    provider_id       TEXT NOT NULL,        -- wire-contract provider id
    model             TEXT,                 -- model name if parsed, else NULL
    prompt_tokens     INTEGER,              -- NULL on a parse miss
    completion_tokens INTEGER,              -- NULL on a parse miss
    total_tokens      INTEGER,              -- NULL on a parse miss
    est_cost          REAL,                 -- NULL when model price unknown
    ts                INTEGER NOT NULL      -- unix seconds
);

CREATE INDEX IF NOT EXISTS idx_usage_token_ts ON usage (token_id, ts);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage (ts);
