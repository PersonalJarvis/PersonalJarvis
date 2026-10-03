import { useQuery } from "@tanstack/react-query";

export type BrowserProfileKind = "managed" | "chrome";
export type BrowserBindingMode = "inherit" | "own" | "profile";

export interface BrowserProfile {
  id: string;
  name: string;
  kind: BrowserProfileKind;
  connected: boolean;
  allowed_domains: string[];
  agent_ids: string[];
  is_default: boolean;
}

export interface BrowserProfileBinding {
  mode: BrowserBindingMode;
  profile_id: string | null;
  effective_profile_id: string | null;
}

export interface BrowserProfilesSnapshot {
  profiles: BrowserProfile[];
  default_profile_id: string | null;
  bindings: Record<string, BrowserProfileBinding>;
  agents: Array<{ agent_id: string; name: string }>;
}

export interface BrowserProfilePairing {
  pairing_code: string;
  expires_in: number;
  server_url: string;
}

export class BrowserProfileError extends Error {
  constructor(public status: number) {
    super(`Browser profile request failed (${status})`);
  }
}

export const BROWSER_PROFILES_QUERY = ["society", "browser-profiles"] as const;
export const BROWSER_PROFILE_CHANGED_EVENT = "jarvis:browser-profile-changed";
const base = "/api/society/browser/profiles";

async function request<T>(url: string, method = "GET", value?: unknown): Promise<T> {
  const response = await fetch(url, {
    method,
    ...(value === undefined ? {} : {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(value),
    }),
  });
  // Never expose provider errors or credentials from a response body.
  if (!response.ok) throw new BrowserProfileError(response.status);
  return response.json() as Promise<T>;
}

export function useBrowserProfiles() {
  return useQuery({
    queryKey: BROWSER_PROFILES_QUERY,
    queryFn: () => request<BrowserProfilesSnapshot>(base),
    staleTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function createBrowserProfile(value: { name: string; kind: BrowserProfileKind; allowed_domains: string[] }) {
  return request<BrowserProfile>(base, "POST", value);
}

export function updateBrowserProfile(id: string, value: { name: string; allowed_domains: string[] }) {
  return request<BrowserProfilesSnapshot>(`${base}/${encodeURIComponent(id)}`, "PATCH", value);
}

export function shareBrowserProfile(id: string, value: { scope: "all" | "selected"; agent_ids: string[] }) {
  return request<BrowserProfilesSnapshot>(`${base}/${encodeURIComponent(id)}/sharing`, "PUT", value);
}

export function bindAgentBrowserProfile(agentId: string, mode: BrowserBindingMode, profile_id: string | null) {
  return request<BrowserProfilesSnapshot>(`/api/society/agents/${encodeURIComponent(agentId)}/browser/profile`, "PUT", { mode, profile_id });
}

export function removeBrowserProfile(id: string) {
  return request<BrowserProfilesSnapshot>(`${base}/${encodeURIComponent(id)}`, "DELETE");
}

export function pairBrowserProfile(id: string) {
  return request<BrowserProfilePairing>(`${base}/${encodeURIComponent(id)}/pair`, "POST");
}

export function parseBrowserDomains(value: string): string[] {
  return [...new Set(value.split(/[\s,;]+/).map((domain) => domain.trim().toLowerCase()).filter(Boolean))];
}
