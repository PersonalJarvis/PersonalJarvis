/**
 * REST-Wrapper fuer den Mission-Bus (Backend-Sub-Agent baut die Endpoints
 * unter `/api/missions/*`). Trennen wir bewusst von den WS-Hooks, damit
 * React-Query die List-/Detail-Abfragen cachen kann.
 */
import type {
  CriticVerdictReady,
  EventEnvelope,
  MissionDetail,
  MissionSummary,
  OpenClawWorkerSnapshot,
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
  // Verdicts ggf. aus Events ableiten falls Backend sie nicht separat liefert
  const events: EventEnvelope[] = data.events ?? [];
  const verdicts: CriticVerdictReady[] =
    data.verdicts ??
    events
      .filter((e) => e.payload.event_type === "CriticVerdictReady")
      .map((e) => e.payload as CriticVerdictReady);
  const worker_snapshots: OpenClawWorkerSnapshot[] =
    Array.isArray(data.worker_snapshots) ? data.worker_snapshots : [];
  return { mission: data.mission, events, verdicts, worker_snapshots };
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
