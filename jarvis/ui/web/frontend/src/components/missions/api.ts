/**
 * REST wrapper for the mission bus (a backend Jarvis-Agent builds the
 * endpoints under `/api/missions/*`). Kept deliberately separate from the
 * WS hooks so React Query can cache the list/detail queries.
 */
import type {
  CriticVerdictReady,
  EventEnvelope,
  MissionDetail,
  MissionSummary,
  MissionToolApprovalDecision,
  MissionToolApprovalsResponse,
  JarvisAgentWorkerSnapshot,
} from "@/types/missions";

export interface MissionsListResponse {
  missions: MissionSummary[];
  total: number;
}

export interface MissionAuthTokenResponse {
  token: string;
}

const API_BASE = "/api/missions";

export async function fetchMissions(): Promise<MissionsListResponse> {
  const res = await fetch(`${API_BASE}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function fetchMissionDetail(id: string): Promise<MissionDetail> {
  const res = await fetch(`${API_BASE}/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  // Derive verdicts from events if the backend doesn't return them separately
  const events: EventEnvelope[] = data.events ?? [];
  const verdicts: CriticVerdictReady[] =
    data.verdicts ??
    events
      .filter((e) => e.payload.event_type === "CriticVerdictReady")
      .map((e) => e.payload as CriticVerdictReady);
  const worker_snapshots: JarvisAgentWorkerSnapshot[] =
    Array.isArray(data.worker_snapshots) ? data.worker_snapshots : [];
  return { mission: data.mission, events, verdicts, worker_snapshots };
}

export function missionToolApprovalsQueryKey(missionId: string | null) {
  return ["missions", "tool-approvals", missionId] as const;
}

export async function fetchMissionToolApprovals(
  missionId: string,
): Promise<MissionToolApprovalsResponse> {
  return requestJson(
    `${API_BASE}/${encodeURIComponent(missionId)}/tool-approvals`,
  );
}

export async function approveMissionToolCall(
  missionId: string,
  traceId: string,
): Promise<MissionToolApprovalDecision> {
  return requestJson(
    `${API_BASE}/${encodeURIComponent(missionId)}/tool-approvals/${encodeURIComponent(traceId)}/approve`,
    { method: "POST" },
  );
}

export async function denyMissionToolCall(
  missionId: string,
  traceId: string,
  reason = "user_denied",
): Promise<MissionToolApprovalDecision> {
  return requestJson(
    `${API_BASE}/${encodeURIComponent(missionId)}/tool-approvals/${encodeURIComponent(traceId)}/deny`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    },
  );
}

export async function cancelMission(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function cancelAllMissions(): Promise<void> {
  const res = await fetch(`${API_BASE}/cancel`, { method: "POST" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function fetchAuthToken(): Promise<string | null> {
  try {
    const res = await fetch(`${API_BASE}/auth/token`);
    if (!res.ok) return null;
    const data: MissionAuthTokenResponse = await res.json();
    return data.token ?? null;
  } catch {
    return null;
  }
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const body = (await res.json().catch(() => null)) as
    | { detail?: unknown }
    | T
    | null;
  if (!res.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? body.detail
        : null;
    throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`);
  }
  return body as T;
}
