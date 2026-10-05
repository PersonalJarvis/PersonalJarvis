/**
 * Which providers and models the assistant's agents may run on — mirror of
 * `jarvis/agent_chat/agent_provider_prefs.py` (GET/PUT /api/society/provider-prefs).
 *
 * Every connected provider is on unless listed in `disabled`; a dual row in
 * `api_only` runs on its key instead of the subscription; `hidden_models`
 * leaves models out of the agents' pickers.
 */
export interface AgentProviderPrefs {
  disabled: string[];
  api_only: string[];
  hidden_models: Record<string, string[]>;
}

export const EMPTY_AGENT_PROVIDER_PREFS: AgentProviderPrefs = { disabled: [], api_only: [], hidden_models: {} };

export const AGENT_PROVIDER_PREFS_KEY = ["agent-provider-prefs"] as const;

export async function fetchAgentProviderPrefs(): Promise<AgentProviderPrefs> {
  const res = await fetch("/api/society/provider-prefs", { cache: "no-store" });
  // An older backend has no such route: everything on, nothing hidden.
  if (res.status === 404) return EMPTY_AGENT_PROVIDER_PREFS;
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as AgentProviderPrefs;
}

export async function saveAgentProviderPrefs(patch: Partial<AgentProviderPrefs>): Promise<AgentProviderPrefs> {
  const res = await fetch("/api/society/provider-prefs", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error((detail && typeof detail.detail === "string" && detail.detail) || `HTTP ${res.status}`);
  }
  return (await res.json()) as AgentProviderPrefs;
}

/** `ids` added to (`on` = false) or taken out of (`on` = true) a list, order kept. */
export function toggled(list: readonly string[], ids: readonly string[], on: boolean): string[] {
  const drop = new Set(ids);
  const kept = list.filter((id) => !drop.has(id));
  return on ? kept : [...kept, ...ids];
}
