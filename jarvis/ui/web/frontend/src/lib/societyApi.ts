/**
 * TypeScript twins of the society enums (jarvis/society/events.py) and, later,
 * the typed client for /api/society. The const arrays are pinned to the Python
 * enums and the SQL CHECK lists by tests/unit/society/test_society_enum_parity.py
 * (AP-4): add a member in Python first, then here, or the parity test fails.
 */

export const MSG_TYPES = [
  "ASSIGN",
  "CLAIM",
  "RESULT",
  "QUERY",
  "ANSWER",
  "HOLD",
  "RELEASE",
  "PROPOSE",
  "VETO",
  "DIGEST",
  "SAY",
  "ROOM_OPEN",
  "ROOM_SETTLE",
] as const;
export type MsgType = (typeof MSG_TYPES)[number];

export const TIERS = ["lead", "orchestrator", "specialist"] as const;
export type Tier = (typeof TIERS)[number];

export const AGENT_STATES = ["active", "paused", "archived"] as const;
export type AgentState = (typeof AGENT_STATES)[number];

export const RUN_STATES = ["idle", "working", "waiting", "paused"] as const;
export type RunState = (typeof RUN_STATES)[number];

export const CHECKPOINTS = ["desk", "meeting", "archive", "gate", "idle"] as const;
export type Checkpoint = (typeof CHECKPOINTS)[number];

export const PERMISSION_CEILINGS = ["safe", "monitor", "ask"] as const;
export type PermissionCeiling = (typeof PERMISSION_CEILINGS)[number];

export const BROWSER_MODES = ["own", "attach"] as const;
export type BrowserMode = (typeof BROWSER_MODES)[number];

export const GRANT_MODES = ["all", "allowlist"] as const;
export type GrantMode = (typeof GRANT_MODES)[number];

export const KNOWLEDGE_SCOPES = ["shared", "own"] as const;
export type KnowledgeScope = (typeof KNOWLEDGE_SCOPES)[number];

export const KNOWLEDGE_ORIGINS = ["user", "tool", "web", "agent"] as const;
export type KnowledgeOrigin = (typeof KNOWLEDGE_ORIGINS)[number];

export const ROOM_STATES = ["queued", "running", "settled", "failed"] as const;
export type RoomState = (typeof ROOM_STATES)[number];

export const APPROVAL_STATES = ["pending", "approved", "denied", "expired", "blocked"] as const;
export type ApprovalState = (typeof APPROVAL_STATES)[number];

/** One row of the board, as GET /api/society/events returns it. */
export interface SocietyEnvelope {
  seq: number;
  event_id: string;
  msg_type: MsgType;
  from_agent: string;
  to_agent: string | null;
  trace_id: string;
  parent_event_id: string | null;
  ts_ms: number;
  cost_usd: number;
  payload: Record<string, unknown>;
}

/** Approval rules — Grok-style, require wins (agent-definition §3.4). */
export interface ApprovalRules {
  require_approval: string[];
  always_allow: string[];
}

/** One roster row as GET /api/society/agents returns it (agent-definition §2). */
export interface SocietyAgentRow {
  agent_id: string;
  name: string;
  title: string;
  description: string;
  tier: Tier;
  parent_agent_id: string | null;
  state: AgentState;
  avatar: Record<string, unknown>;
  checkpoint: Checkpoint;
  provider: string;
  model: string;
  effort: string;
  /** Subscription seat (agent-accounts id) for a CLI-seated agent; "" = active account. */
  account_id: string;
  grant_mode: GrantMode;
  grants: string[];
  focus: string[];
  denies: string[];
  skills: string[] | null;
  workspace_dir: string;
  wiki_namespace: string;
  knowledge_scope: KnowledgeScope;
  permission_ceiling: PermissionCeiling;
  approval_rules: ApprovalRules;
  daily_budget_usd: number;
  max_concurrent_runs: number;
  browser_mode: BrowserMode;
  browser_allowed_domains: string[];
  session_id: string;
  created_ms: number;
  updated_ms: number;
  stats: { runs: number; total_cost_usd: number; last_active_ms: number | null };
  run_state?: RunState;
}
