/** Wire projection of jarvis/core/swarm_types.py. Exact counters stay strings. */
export type Counter = string;
export type TeamState = "created" | "running" | "paused" | "blocked" | "succeeded" | "failed" | "canceled" | "archived";
export type TaskState = "ready" | "running" | "blocked" | "succeeded" | "failed" | "canceled";
export type AgentRole = "lead" | "coordinator" | "worker";
export type AgentState = "idle" | "running" | "waiting" | "stopped" | "failed" | "completed";
export interface BudgetLimits {
  token_budget: Counter; monetary_limit_microusd: Counter | null; concurrency: number;
  worker_limit: Counter; runtime_seconds: number; max_attempts: number;
  max_output_tokens: number; max_tool_calls: number;
}
export interface CapabilityPolicy {
  tools: string[]; internet: boolean; allowed_domains: string[]; dependency_registries: string[];
  max_network_bytes: Counter; max_artifact_bytes: Counter; allow_dependencies: boolean;
}
export interface TeamRecord {
  storage_generation?: string;
  id: string; name: string; goal: string; acceptance: string; lead_id: string; state: TeamState; version: number;
  created_at: number; updated_at: number; started_at: number | null; reason: string;
  limits: BudgetLimits; policy: CapabilityPolicy; tokens_used: Counter; tokens_reserved: Counter;
  cost_microusd: Counter; cost_reserved_microusd: Counter; network_bytes: Counter;
  mode: string; checkpoint: Record<string, unknown>;
}
/** Catalog identity remains readable when the team's own storage is unavailable. */
export interface TeamUnavailable {
  id: string; name: string; created_at: number; available: false; error: string;
}
export type TeamListItem = TeamRecord | TeamUnavailable;
export function isTeamUnavailable(team: TeamListItem | undefined): team is TeamUnavailable {
  return team !== undefined && "available" in team && team.available === false;
}
export interface AgentRecord {
  source_agent_id?: string | null;
  id: string; team_id: string; name: string; role: AgentRole; state: AgentState;
  domain: string; group_id: string; task_id: string | null; generation: number; level: number;
  reliability: number; verified_tasks: Counter; tool_activity: string | null;
}
export interface TaskSpec {
  id: string; title: string; description: string; acceptance: string; dependencies: string[];
  domain: string; milestone: string; difficulty: number; priority: number;
  verification: "review" | "javascript"; verification_script: string; required_tools: string[];
  independent_verification: boolean;
}
export interface TaskRecord extends TaskSpec {
  team_id: string; state: TaskState; owner_id: string | null; attempt_count: number; fence: number;
  version: number; result: string; evidence: string[]; reason: string; created_at: number; updated_at: number;
}
export interface ActivityRecord {
  id: string; team_id: string; seq: Counter; kind: string; summary: string;
  agent_id: string | null; task_id: string | null; trace_id: string; created_at: number;
  data: Record<string, unknown>;
}
export interface WorldGroup { id: string; title: string; counts: Record<string, Counter>; agents: Counter; level: number }
export interface WorldSnapshot {
  team: TeamRecord; revision: Counter; agents: AgentRecord[]; tasks: TaskRecord[];
  groups: WorldGroup[]; activity: ActivityRecord[]; counts: Record<string, Counter>;
  aggregated: boolean; has_more: boolean;
}
export interface TeamCreate {
  preparation_required?: boolean;
  name: string; goal: string; acceptance: string; limits: BudgetLimits; policy: CapabilityPolicy; tasks: TaskSpec[];
  request_key: string; mode: "local" | "distributed";
}
export type Control = "start" | "pause" | "resume" | "stop" | "cancel" | "archive";
export type RecordKind = "tasks" | "agents" | "messages" | "events" | "artifacts" | "reputation" | "publications" | "decisions" | "checkpoints";
export interface CheckpointSnapshot {
  id: string; team_id: string; checkpoint_id: string; kind: "checkpoint_snapshot";
  version: Counter; digest: string; source: string; created_at: number;
  checkpoint: Record<string, unknown>;
}
export interface Capabilities { [key: string]: unknown }
export type ScopedRecord = Record<string, unknown> & { team_id?: string };

export const terminalState = (state: TeamState) => ["succeeded", "failed", "canceled", "archived"].includes(state);
export function allowedControls(state: TeamState): Control[] {
  switch (state) {
    case "created": return ["start", "cancel"];
    case "running": return ["pause", "stop", "cancel"];
    case "paused": case "blocked": return ["resume", "stop", "cancel"];
    case "succeeded": case "failed": case "canceled": return ["archive"];
    default: return [];
  }
}
export function exactCount(value: string): string {
  return /^\d{1,31}$/.test(value) ? BigInt(value).toLocaleString() : "—";
}
export function budgetPercent(used: Counter, reserved: Counter, limit: Counter): number {
  const cap = BigInt(limit);
  return cap > 0n ? Number(((BigInt(used) + BigInt(reserved)) * 10000n) / cap > 10000n ? 10000n : ((BigInt(used) + BigInt(reserved)) * 10000n) / cap) / 100 : 0;
}
export function microUsd(value: Counter): string {
  const n = BigInt(value);
  return `$${(n / 1000000n).toLocaleString()}.${(n % 1000000n).toString().padStart(6, "0")}`;
}
