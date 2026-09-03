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

export const CHECKPOINTS = [
  "desk",
  "meeting",
  "archive",
  "gate",
  "idle",
  "gallery",
  "hub:plugins",
  "hub:skills",
  "hub:mcp",
  "hub:cli",
  "hub:comms",
  "hub:desktop",
  "hub:web",
  "hub:models",
] as const;
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

export const QUEST_STATES = ["open", "assigned", "running", "done", "failed", "cancelled"] as const;
export type QuestState = (typeof QUEST_STATES)[number];

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

/**
 * One stored subscription login of a vendor CLI, display-safe
 * (jarvis/agent_accounts.py AccountSnapshot — never a token).
 */
export interface SocietyAccount {
  id: string;
  label: string;
  connected: boolean;
  /** "subscription" | "api_key" | "expired" | "unknown" */
  mode: string;
  message: string;
  email: string | null;
  tier: string | null;
  warning: string | null;
}

/** One row of GET /api/society/providers: which runner answers on this box and its seats. */
export interface SocietyProviderRow {
  id: string;
  label: string;
  family: string;
  runner: string;
  /** The runner is a vendor CLI on a plan, not an API behind a key. */
  subscription: boolean;
  keyless: boolean;
  /** The agent-accounts platform of that CLI; null on an API row. */
  platform: string | null;
  accounts: SocietyAccount[];
}

export async function fetchSocietyProviders(): Promise<SocietyProviderRow[]> {
  const res = await fetch("/api/society/providers");
  if (!res.ok) throw new Error(`society providers ${res.status}`);
  const body = (await res.json()) as { providers?: SocietyProviderRow[] };
  return Array.isArray(body.providers) ? body.providers : [];
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
  stats: {
    runs: number;
    total_cost_usd: number;
    /** Since midnight UTC — the window the scheduler's daily-budget gate uses. */
    spent_today_usd?: number;
    last_active_ms: number | null;
  };
  run_state?: RunState;
}

/** One quest as GET /api/society/quests returns it (jarvis/society/quests.py). */
export interface SocietyQuestRow {
  quest_id: string;
  title: string;
  text: string;
  state: QuestState;
  created_by: string;
  /** The taker, once routed; null while nobody could take it. */
  agent_id: string | null;
  trace_id: string;
  assign_event_id: string | null;
  run_id: string;
  /** How the taker was chosen: reason ("focus-match" | "keyword-match" | "generalist" |
   *  "orchestrator" | "forged:<capability>"), score, focus, forged. */
  routing: { reason?: string; score?: number; focus?: string[]; forged?: boolean; agent_id?: string };
  /** The handoff (status, done, output, open, text) or the typed refusal (reason, retry). */
  result: {
    status?: string;
    done?: string;
    output?: string[];
    open?: string[];
    text?: string;
    reason?: string;
    retry?: string;
    /** "waiting" while a busy taker is knocked again; why (blocker: approval | busy). */
    blocker?: string;
    attempts?: number;
    /** Live while running: the latest tool steps and the agent's latest sentence. */
    progress?: string[];
    live?: string;
  };
  created_ms: number;
  updated_ms: number;
  done_ms: number | null;
}

async function questJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail && typeof body.detail === "object" && "detail" in body.detail)
        detail = String((body.detail as { detail: unknown }).detail);
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export async function fetchSocietyQuests(): Promise<SocietyQuestRow[]> {
  const body = await questJson<{ quests?: SocietyQuestRow[] }>("/api/society/quests");
  return Array.isArray(body.quests) ? body.quests : [];
}

export async function postSocietyQuest(text: string, title = ""): Promise<SocietyQuestRow> {
  const body = await questJson<{ quest: SocietyQuestRow }>("/api/society/quests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, title }),
  });
  return body.quest;
}

export async function cancelSocietyQuest(questId: string): Promise<SocietyQuestRow> {
  const body = await questJson<{ quest: SocietyQuestRow }>(
    `/api/society/quests/${encodeURIComponent(questId)}/cancel`,
    { method: "POST" },
  );
  return body.quest;
}

export async function retrySocietyQuest(questId: string): Promise<SocietyQuestRow> {
  const body = await questJson<{ quest: SocietyQuestRow }>(
    `/api/society/quests/${encodeURIComponent(questId)}/retry`,
    { method: "POST" },
  );
  return body.quest;
}
