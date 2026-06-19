import type { Run, RunListItem } from "./types";

export async function fetchRuns(limit = 100): Promise<RunListItem[]> {
  const res = await fetch(`/api/runs?limit=${limit}`);
  if (!res.ok) throw new Error(`HTTP ${res.status} — runs list`);
  return (await res.json()) as RunListItem[];
}

export async function fetchRunDetail(id: string): Promise<Run> {
  const res = await fetch(`/api/runs/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status} — run detail`);
  return (await res.json()) as Run;
}

export function runExportUrl(id: string): string {
  // Reuse the sessions JSON export for the raw dump (same session_id).
  return `/api/sessions/${encodeURIComponent(id)}/export?format=json`;
}
