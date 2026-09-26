-- Version 2. Each file belongs to exactly one explicitly authorized team.
CREATE TABLE IF NOT EXISTS team (singleton INTEGER PRIMARY KEY CHECK(singleton=1), record TEXT NOT NULL CHECK(json_valid(record)));
CREATE TABLE IF NOT EXISTS controller (singleton INTEGER PRIMARY KEY CHECK(singleton=1), instance_id TEXT NOT NULL, fence INTEGER NOT NULL, token_hash TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('lead','coordinator','worker')), state TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, token_hash TEXT NOT NULL, record TEXT NOT NULL CHECK(json_valid(record)));
CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, state TEXT NOT NULL CHECK(state IN ('ready','running','blocked','succeeded','failed','canceled')), owner_id TEXT REFERENCES agents(id), fence INTEGER NOT NULL DEFAULT 0, record TEXT NOT NULL CHECK(json_valid(record)));
CREATE TABLE IF NOT EXISTS dependencies (task_id TEXT NOT NULL REFERENCES tasks(id), depends_on TEXT NOT NULL REFERENCES tasks(id), PRIMARY KEY(task_id,depends_on), CHECK(task_id<>depends_on));
CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), agent_id TEXT NOT NULL REFERENCES agents(id), fence INTEGER NOT NULL, controller_fence INTEGER NOT NULL, state TEXT NOT NULL, heartbeat_at REAL NOT NULL, started_at REAL NOT NULL, finished_at REAL, reason TEXT NOT NULL DEFAULT '', UNIQUE(task_id,fence));
CREATE UNIQUE INDEX IF NOT EXISTS one_active_attempt ON attempts(task_id) WHERE state='running';
CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY, request_key TEXT NOT NULL, agent_id TEXT NOT NULL REFERENCES agents(id), task_id TEXT, task_fence INTEGER NOT NULL, controller_fence INTEGER NOT NULL, tokens TEXT NOT NULL, cost_microusd TEXT NOT NULL, actual_tokens TEXT, actual_cost_microusd TEXT, state TEXT NOT NULL, created_at REAL NOT NULL, closed_at REAL, UNIQUE(agent_id,request_key));
CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, sender_id TEXT NOT NULL REFERENCES agents(id), task_id TEXT NOT NULL REFERENCES tasks(id), request_key TEXT NOT NULL, intent TEXT NOT NULL, topic TEXT NOT NULL, priority INTEGER NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL, record TEXT NOT NULL CHECK(json_valid(record)), UNIQUE(sender_id,request_key));
CREATE INDEX IF NOT EXISTS messages_task ON messages(task_id,created_at);
CREATE TABLE IF NOT EXISTS deliveries (message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE, recipient_id TEXT NOT NULL REFERENCES agents(id), acknowledged_at REAL, lease_until REAL NOT NULL DEFAULT 0, deliveries INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(message_id,recipient_id));
CREATE INDEX IF NOT EXISTS deliveries_recipient ON deliveries(recipient_id,acknowledged_at,lease_until);
CREATE TABLE IF NOT EXISTS subscriptions (agent_id TEXT NOT NULL REFERENCES agents(id), task_id TEXT NOT NULL, topic TEXT NOT NULL, PRIMARY KEY(agent_id,task_id,topic));
CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES agents(id), task_id TEXT NOT NULL REFERENCES tasks(id), object_key TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes TEXT NOT NULL, request_key TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0, record TEXT NOT NULL CHECK(json_valid(record)), UNIQUE(owner_id,request_key));
CREATE TABLE IF NOT EXISTS verifications (id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), fence INTEGER NOT NULL, accepted INTEGER NOT NULL, record TEXT NOT NULL CHECK(json_valid(record)), UNIQUE(task_id,fence));
CREATE TABLE IF NOT EXISTS ratings (id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id), task_id TEXT NOT NULL REFERENCES tasks(id), evidence_key TEXT NOT NULL UNIQUE, record TEXT NOT NULL CHECK(json_valid(record)));
CREATE INDEX IF NOT EXISTS ratings_agent ON ratings(agent_id);
CREATE TABLE IF NOT EXISTS reputation (agent_id TEXT NOT NULL REFERENCES agents(id), domain TEXT NOT NULL, record TEXT NOT NULL CHECK(json_valid(record)), PRIMARY KEY(agent_id,domain));
CREATE TABLE IF NOT EXISTS team_tools (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, owner_id TEXT NOT NULL REFERENCES agents(id), state TEXT NOT NULL, record TEXT NOT NULL CHECK(json_valid(record)));
CREATE TABLE IF NOT EXISTS destinations (id TEXT PRIMARY KEY, record TEXT NOT NULL CHECK(json_valid(record)));
CREATE TABLE IF NOT EXISTS publications (id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, destination_id TEXT NOT NULL REFERENCES destinations(id), artifact_id TEXT NOT NULL REFERENCES artifacts(id), state TEXT NOT NULL, record TEXT NOT NULL CHECK(json_valid(record)));
CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, record TEXT NOT NULL CHECK(json_valid(record)));
CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, agent_id TEXT, task_id TEXT, trace_id TEXT NOT NULL, created_at REAL NOT NULL, record TEXT NOT NULL CHECK(json_valid(record)));
CREATE INDEX IF NOT EXISTS events_task ON events(task_id,seq);

CREATE INDEX IF NOT EXISTS tasks_state_owner ON tasks(state,owner_id);
CREATE INDEX IF NOT EXISTS dependencies_parent ON dependencies(depends_on,task_id);
CREATE INDEX IF NOT EXISTS attempts_recovery ON attempts(state,controller_fence,heartbeat_at);
CREATE INDEX IF NOT EXISTS agents_eligible ON agents(active,state,role);
CREATE INDEX IF NOT EXISTS publications_pending ON publications(state);
CREATE UNIQUE INDEX IF NOT EXISTS team_tools_operation ON team_tools(owner_id,json_extract(record,'$.request_key'));
CREATE TRIGGER IF NOT EXISTS team_contract_insert BEFORE INSERT ON team
WHEN json_extract(NEW.record,'$.state') NOT IN ('created','running','paused','blocked','succeeded','failed','canceled','archived') OR json_type(NEW.record,'$.state') IS NOT 'text' OR json_type(NEW.record,'$.id') IS NOT 'text'
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS team_contract_update BEFORE UPDATE ON team
WHEN json_extract(NEW.record,'$.state') NOT IN ('created','running','paused','blocked','succeeded','failed','canceled','archived') OR json_type(NEW.record,'$.state') IS NOT 'text' OR json_type(NEW.record,'$.id') IS NOT 'text'
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS agents_contract_insert BEFORE INSERT ON agents
WHEN NEW.state NOT IN ('idle','running','waiting','stopped','failed','completed') OR NEW.active NOT IN (0,1) OR NEW.state IS NOT json_extract(NEW.record,'$.state') OR NEW.role IS NOT json_extract(NEW.record,'$.role') OR NEW.id IS NOT json_extract(NEW.record,'$.id') OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS agents_contract_update BEFORE UPDATE ON agents
WHEN NEW.state NOT IN ('idle','running','waiting','stopped','failed','completed') OR NEW.active NOT IN (0,1) OR NEW.state IS NOT json_extract(NEW.record,'$.state') OR NEW.role IS NOT json_extract(NEW.record,'$.role') OR NEW.id IS NOT json_extract(NEW.record,'$.id') OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS tasks_contract_insert BEFORE INSERT ON tasks
WHEN NEW.state NOT IN ('ready','running','blocked','succeeded','failed','canceled') OR NEW.state IS NOT json_extract(NEW.record,'$.state') OR NEW.id IS NOT json_extract(NEW.record,'$.id') OR NEW.fence IS NOT json_extract(NEW.record,'$.fence') OR NEW.owner_id IS NOT json_extract(NEW.record,'$.owner_id') OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS tasks_contract_update BEFORE UPDATE ON tasks
WHEN NEW.state NOT IN ('ready','running','blocked','succeeded','failed','canceled') OR NEW.state IS NOT json_extract(NEW.record,'$.state') OR NEW.id IS NOT json_extract(NEW.record,'$.id') OR NEW.fence IS NOT json_extract(NEW.record,'$.fence') OR NEW.owner_id IS NOT json_extract(NEW.record,'$.owner_id') OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS attempts_contract_insert BEFORE INSERT ON attempts
WHEN NEW.state NOT IN ('running','succeeded','failed','canceled','interrupted') OR NEW.fence < 1 OR NEW.controller_fence < 1
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS attempts_contract_update BEFORE UPDATE ON attempts
WHEN NEW.state NOT IN ('running','succeeded','failed','canceled','interrupted') OR NEW.fence < 1 OR NEW.controller_fence < 1
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS reservations_contract_insert BEFORE INSERT ON reservations
WHEN NEW.state NOT IN ('reserved','unknown','closed') OR length(NEW.tokens) NOT BETWEEN 1 AND 31 OR NEW.tokens GLOB '*[^0-9]*' OR length(NEW.cost_microusd) NOT BETWEEN 1 AND 31 OR NEW.cost_microusd GLOB '*[^0-9]*'
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS reservations_contract_update BEFORE UPDATE ON reservations
WHEN NEW.state NOT IN ('reserved','unknown','closed') OR length(NEW.tokens) NOT BETWEEN 1 AND 31 OR NEW.tokens GLOB '*[^0-9]*' OR length(NEW.cost_microusd) NOT BETWEEN 1 AND 31 OR NEW.cost_microusd GLOB '*[^0-9]*'
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS messages_contract_insert BEFORE INSERT ON messages
WHEN NEW.intent NOT IN ('COORD_STATUS','REQUEST_PROGRESS','REPORT_PROGRESS','REQUEST_HELP','OFFER_HELP','SHARE_FINDING','CLAIM_WORK','RELEASE_WORK','CONFLICT') OR NEW.intent IS NOT json_extract(NEW.record,'$.intent') OR NEW.sender_id IS NOT json_extract(NEW.record,'$.sender_id') OR NEW.task_id IS NOT json_extract(NEW.record,'$.task_id') OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS messages_contract_update BEFORE UPDATE ON messages
WHEN NEW.intent NOT IN ('COORD_STATUS','REQUEST_PROGRESS','REPORT_PROGRESS','REQUEST_HELP','OFFER_HELP','SHARE_FINDING','CLAIM_WORK','RELEASE_WORK','CONFLICT') OR NEW.intent IS NOT json_extract(NEW.record,'$.intent') OR NEW.sender_id IS NOT json_extract(NEW.record,'$.sender_id') OR NEW.task_id IS NOT json_extract(NEW.record,'$.task_id') OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS publications_contract_insert BEFORE INSERT ON publications
WHEN NEW.state NOT IN ('pending','published','revoked') OR NEW.state IS NOT json_extract(NEW.record,'$.state') OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS publications_contract_update BEFORE UPDATE ON publications
WHEN NEW.state NOT IN ('pending','published','revoked') OR NEW.state IS NOT json_extract(NEW.record,'$.state') OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS team_tools_contract_insert BEFORE INSERT ON team_tools
WHEN NEW.state NOT IN ('validated','revoked') OR NEW.state IS NOT json_extract(NEW.record,'$.state') OR NEW.name IS NOT json_extract(NEW.record,'$.name') OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS team_tools_contract_update BEFORE UPDATE ON team_tools
WHEN NEW.state NOT IN ('validated','revoked') OR NEW.state IS NOT json_extract(NEW.record,'$.state') OR NEW.name IS NOT json_extract(NEW.record,'$.name') OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS artifacts_contract_insert BEFORE INSERT ON artifacts
WHEN 0 OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS artifacts_contract_update BEFORE UPDATE ON artifacts
WHEN 0 OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS verifications_contract_insert BEFORE INSERT ON verifications
WHEN 0 OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS verifications_contract_update BEFORE UPDATE ON verifications
WHEN 0 OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS ratings_contract_insert BEFORE INSERT ON ratings
WHEN 0 OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS ratings_contract_update BEFORE UPDATE ON ratings
WHEN 0 OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS decisions_contract_insert BEFORE INSERT ON decisions
WHEN 0 OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS decisions_contract_update BEFORE UPDATE ON decisions
WHEN 0 OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS events_contract_insert BEFORE INSERT ON events
WHEN 0 OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS events_contract_update BEFORE UPDATE ON events
WHEN 0 OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS destinations_contract_insert BEFORE INSERT ON destinations
WHEN 0 OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
CREATE TRIGGER IF NOT EXISTS destinations_contract_update BEFORE UPDATE ON destinations
WHEN 0 OR json_extract(NEW.record,'$.team_id') IS NOT (SELECT json_extract(record,'$.id') FROM team WHERE singleton=1)
BEGIN
SELECT RAISE(ABORT, 'Swarm storage contract violation');
END;
