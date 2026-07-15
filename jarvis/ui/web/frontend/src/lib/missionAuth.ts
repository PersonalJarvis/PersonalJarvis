const MISSION_TOKEN_ENDPOINT = "/api/missions/auth/token";

export async function fetchMissionToken(): Promise<string> {
  const response = await fetch(MISSION_TOKEN_ENDPOINT, {
    cache: "no-store",
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new Error(`Mission authorization failed (${response.status})`);
  }
  const payload = (await response.json()) as { token?: unknown };
  const token = typeof payload.token === "string" ? payload.token.trim() : "";
  if (!token) throw new Error("Mission authorization returned no token");
  return token;
}

export function buildMissionSocketUrl(path: string): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}${path}`;
}
