/**
 * REST wrapper for the mission bus (a backend Jarvis-Agent builds the
 * endpoints under `/api/missions/*`). Kept deliberately separate from the
 * WS hooks so React Query can cache the list/detail queries.
 */
import type {
  CapacityDecision,
  CapacityDecisionResponse,
  CriticVerdictReady,
  EventEnvelope,
  MissionChanges,
  MissionBilling,
  MissionDetail,
  MissionResult,
  MissionSummary,
  MissionToolApprovalDecision,
  MissionToolApprovalsResponse,
  JarvisAgentWorkerSnapshot,
  PaidOfferResponse,
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

/** The signed outcome plus the deliverables' bounded contents. */
export async function fetchMissionResult(id: string): Promise<MissionResult> {
  return requestJson(`${API_BASE}/${encodeURIComponent(id)}/result`);
}

/** The per-file ledger of what the workers changed (from the archived diff). */
export async function fetchMissionChanges(id: string): Promise<MissionChanges> {
  return requestJson(`${API_BASE}/${encodeURIComponent(id)}/changes`);
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

export function paidOfferQueryKey(missionId: string | null) {
  return ["missions", "paid-offer", missionId] as const;
}

/** The paid-API alternative for a mission waiting for capacity (read-only). */
export async function fetchPaidOffer(missionId: string): Promise<PaidOfferResponse> {
  return requestJson(`${API_BASE}/${encodeURIComponent(missionId)}/paid-offer`);
}

/**
 * Wait, approve paid API use for this one mission, or cancel. An approval
 * echoes the provider and model of the offer the user saw; the server
 * rejects it (409) if the offer changed in between.
 */
export async function decideCapacity(
  missionId: string,
  decision: CapacityDecision,
  offer?: { provider: string; model: string },
): Promise<CapacityDecisionResponse> {
  return requestJson(
    `${API_BASE}/${encodeURIComponent(missionId)}/capacity-decision`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        decision,
        provider: offer?.provider ?? null,
        model: offer?.model ?? null,
      }),
    },
  );
}

export const MISSION_BILLING_KEY = ["mission-billing"] as const;

function isMissionBilling(body: unknown): body is MissionBilling {
  return (
    !!body &&
    typeof body === "object" &&
    typeof (body as MissionBilling).paid_api_fallback === "boolean" &&
    typeof (body as MissionBilling).subscription_mode === "boolean"
  );
}

/**
 * Whether missions may continue on a paid API key once every subscription is
 * used up. `null` when the backend does not offer the setting (an older
 * server answers 404), so the page leaves the row out instead of guessing.
 */
export async function fetchMissionBilling(): Promise<MissionBilling | null> {
  const res = await fetch("/api/mission-billing", { cache: "no-store" });
  if (res.status === 404) return null;
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? (body as { detail: unknown }).detail : null;
    throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`);
  }
  return isMissionBilling(body) ? body : null;
}

/** Switch the paid fallback; the answer is the state the server now holds. */
export async function saveMissionBilling(paidApiFallback: boolean): Promise<MissionBilling> {
  const body = await requestJson<unknown>("/api/mission-billing", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paid_api_fallback: paidApiFallback }),
  });
  if (!isMissionBilling(body)) throw new Error("Unexpected answer from the server");
  return body;
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
