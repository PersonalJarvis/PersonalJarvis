/**
 * Typed client for the Socials REST API (jarvis/ui/web/socials_routes.py).
 * Plain fetch — the views use useState/useEffect (no React Query), matching the
 * pattern in TelephonyView/ProfileView.
 */
export interface SocialEntry {
  id: string;
  platform: string;
  label: string;
  url: string;
  enabled: boolean;
  order: number;
}

export interface SocialInput {
  platform: string;
  label: string;
  url: string;
  enabled?: boolean;
}

const BASE = "/api/socials";

async function asError(res: Response): Promise<never> {
  let detail = `${res.status} ${res.statusText}`;
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    /* non-JSON body — keep the status line */
  }
  throw new Error(detail);
}

export async function listSocials(): Promise<SocialEntry[]> {
  const res = await fetch(BASE);
  if (!res.ok) return asError(res);
  const data = (await res.json()) as { entries?: SocialEntry[] };
  return data.entries ?? [];
}

export async function createSocial(input: SocialInput): Promise<SocialEntry> {
  const res = await fetch(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) return asError(res);
  return (await res.json()) as SocialEntry;
}

export async function updateSocial(
  id: string,
  patch: Partial<SocialInput>,
): Promise<SocialEntry> {
  const res = await fetch(`${BASE}/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) return asError(res);
  return (await res.json()) as SocialEntry;
}

export async function deleteSocial(id: string): Promise<void> {
  const res = await fetch(`${BASE}/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!res.ok) return asError(res);
}
