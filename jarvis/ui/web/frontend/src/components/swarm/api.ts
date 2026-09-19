import type { Capabilities, Control, RecordKind, ScopedRecord, TeamCreate, TeamListItem, TeamRecord, WorldSnapshot } from "./types";

export const teamPath = (id: string) => `/api/swarm/teams/${encodeURIComponent(id)}`;
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { const body = await response.json(); if (typeof body.detail === "string") detail = body.detail; }
    catch { /* An HTML gateway failure still has a useful HTTP status. */ }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}
const post = (body: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
export const capabilities = (signal?: AbortSignal) => request<Capabilities>("/api/swarm/capabilities", { signal });
export async function listTeams(offset = 0, signal?: AbortSignal): Promise<TeamListItem[]> {
  const rows = await request<unknown>(`/api/swarm/teams?limit=50&offset=${offset}`, { signal });
  if (!Array.isArray(rows) || rows.length > 50 || !rows.every(validTeamListItem)) throw new Error("Invalid or unbounded Swarm team list");
  return rows;
}
export const createTeam = (spec: TeamCreate) => request<TeamRecord>("/api/swarm/teams", post(spec));
export const controlTeam = (team: TeamRecord, action: Control) => request<TeamRecord>(`${teamPath(team.id)}/${action}`, post({ expected_version: team.version, expected_storage_generation: team.storage_generation ?? "" }));
/** Retrying an uncertain launch reuses the creation key and never restarts finished work. */
export async function createAndStartTeam(spec: TeamCreate): Promise<TeamRecord> {
  const team = await createTeam(spec);
  return team.state === "created" ? controlTeam(team, "start") : team;
}
export async function world(id: string, group = "", signal?: AbortSignal): Promise<WorldSnapshot> {
  const snapshot = await request<WorldSnapshot>(`${teamPath(id)}/world?group=${encodeURIComponent(group)}`, { signal });
  if (!validSnapshot(snapshot, id)) throw new Error("Invalid or mismatched Swarm world response");
  return snapshot;
}
export async function records(id: string, kind: RecordKind, offset = 0, signal?: AbortSignal): Promise<ScopedRecord[]> {
  const rows = await request<ScopedRecord[]>(`${teamPath(id)}/${kind}?limit=50&offset=${offset}`, { signal });
  if (!Array.isArray(rows) || rows.length > 50 || rows.some(row => row.team_id !== undefined && row.team_id !== id)) throw new Error("Mismatched or unbounded Swarm records");
  return rows;
}
export async function record(id: string, kind: "tasks" | "agents" | "decisions" | "checkpoints", recordId: string, signal?: AbortSignal): Promise<ScopedRecord> {
  const row = await request<ScopedRecord>(`${teamPath(id)}/${kind}/record/${encodeURIComponent(recordId)}`, { signal });
  if (row.team_id !== id || row.id !== recordId) throw new Error("Mismatched Swarm record");
  return row;
}

/** An unavailable record carries identity and an error, never an invented lifecycle. */
export function validTeamListItem(value: unknown): value is TeamListItem {
  if (!value || typeof value !== "object") return false;
  const row = value as Record<string, unknown>;
  if (typeof row.id !== "string" || !row.id || typeof row.name !== "string" || typeof row.created_at !== "number" || !Number.isFinite(row.created_at)) return false;
  if ("available" in row) return row.available === false && typeof row.error === "string" && Object.keys(row).every(key => ["id", "name", "created_at", "available", "error"].includes(key));
  return typeof row.state === "string" && ["created", "running", "paused", "blocked", "succeeded", "failed", "canceled", "archived"].includes(row.state);
}

/** Reject the whole projection on a scope violation; never merge foreign nodes. */
export function validSnapshot(value: unknown, id: string): value is WorldSnapshot {
  if (!value || typeof value !== "object") return false;
  const s = value as WorldSnapshot;
  const counter = (v: unknown) => typeof v === "string" && /^\d{1,31}$/.test(v);
  if (s.team?.id !== id || !counter(s.revision) || !Array.isArray(s.agents) || !Array.isArray(s.tasks) || !Array.isArray(s.activity) || !Array.isArray(s.groups)) return false;
  if (s.team.storage_generation !== undefined && (typeof s.team.storage_generation !== "string" || s.team.storage_generation.length > 64)) return false;
  if (s.agents.length > 100 || s.groups.length > 50 || s.tasks.length > 100 || s.activity.length > 100) return false;
  if (![s.team.tokens_used, s.team.tokens_reserved, s.team.limits?.token_budget, s.team.cost_microusd, s.team.cost_reserved_microusd].every(counter)) return false;
  if (!s.counts || !Object.values(s.counts).every(counter) || !counter(s.team.network_bytes) || !counter(s.team.limits.worker_limit)) return false;
  if (s.team.limits.monetary_limit_microusd !== null && !counter(s.team.limits.monetary_limit_microusd)) return false;
  if (s.groups.some(group => !counter(group.agents) || !group.counts || !Object.values(group.counts).every(counter))) return false;
  if (!s.team.policy || !Array.isArray(s.team.policy.allowed_domains) || !s.team.checkpoint || typeof s.team.checkpoint !== "object") return false;
  return [...s.agents, ...s.tasks, ...s.activity].every(row => row.team_id === id);
}
