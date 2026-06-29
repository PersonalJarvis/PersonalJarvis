// REST-Aufrufe zu /api/sessions. Alle relativ — der Vite-Dev-Proxy bzw.
// das pywebview-Fenster routen automatisch zur FastAPI auf 127.0.0.1:47821.

import type { SessionDetail, SessionListItem } from "./types";

export async function fetchSessions(limit = 100): Promise<SessionListItem[]> {
  const res = await fetch(`/api/sessions?limit=${limit}`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} — sessions list`);
  }
  return (await res.json()) as SessionListItem[];
}

export async function fetchSessionDetail(id: string): Promise<SessionDetail> {
  const res = await fetch(`/api/sessions/${encodeURIComponent(id)}`);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} — session detail`);
  }
  return (await res.json()) as SessionDetail;
}

export async function fetchSessionExport(
  id: string,
  format: "markdown" | "plain" | "json" = "markdown",
): Promise<string> {
  const res = await fetch(
    `/api/sessions/${encodeURIComponent(id)}/export?format=${format}`,
  );
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} — session export`);
  }
  return await res.text();
}

/** URL of the raw export — opened in a new browser tab on a headless/VPS host
 *  where there are no local apps to launch. */
export function sessionExportUrl(
  id: string,
  format: "markdown" | "plain" | "json",
): string {
  return `/api/sessions/${encodeURIComponent(id)}/export?format=${format}`;
}

/** Open the session transcript (in the given format) in a local app on the
 *  desktop. ``opener`` is a closed opener id (default | browser | editor key),
 *  resolved + launched by the backend. Returns whether a launcher fired. */
export async function openSessionWith(
  id: string,
  format: "markdown" | "plain" | "json",
  opener: string,
): Promise<boolean> {
  const res = await fetch(
    `/api/sessions/${encodeURIComponent(id)}/open-with?format=${format}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ opener }),
    },
  );
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} — session open-with`);
  }
  const data = (await res.json()) as { opened?: boolean };
  return data.opened ?? false;
}
