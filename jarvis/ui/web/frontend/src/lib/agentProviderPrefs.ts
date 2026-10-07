/**
 * Which providers and models the assistant's agents may run on — mirror of
 * `jarvis/agent_chat/agent_provider_prefs.py` (GET/PUT /api/society/provider-prefs).
 *
 * Every connected provider is on unless listed in `disabled`; a dual row in
 * `api_only` runs on its key instead of the subscription; `hidden_models`
 * leaves models out of every model picker — the agents', the threads', the
 * coding panes' and the front page's (each catalog row carries its own list).
 */
import { create } from "zustand";
import type { CuratedModel } from "@/lib/agentChatApi";

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

/**
 * The hidden-model lists this window last saved on the API Keys page, or null
 * before any save. Newer than a catalog a picker already holds: those are
 * read once and kept, so without this a model switched off would stay listed
 * until the next thread or chat reloaded its catalog.
 */
export const useSavedHiddenModels = create<{ hidden: Record<string, string[]> | null }>(() => ({ hidden: null }));

/**
 * The models a picker lists for a catalog row: `models` minus the ones hidden
 * on the API Keys page (`saved`, when this window saved since, else the row's
 * own list). `keep` (the current pick) stays listed even when hidden, so a
 * session already on it still shows what it runs on.
 */
export function offeredModels(
  row: { id: string; hidden_models?: string[] },
  models: CuratedModel[],
  keep = "",
  saved: Record<string, string[]> | null = null,
): CuratedModel[] {
  const ids = saved ? saved[row.id] : row.hidden_models;
  if (!ids?.length) return models;
  const hidden = new Set(ids);
  hidden.delete(keep);
  const shown = models.filter((model) => !hidden.has(model.id));
  return shown.length === models.length ? models : shown;
}

/** `ids` added to (`on` = false) or taken out of (`on` = true) a list, order kept. */
export function toggled(list: readonly string[], ids: readonly string[], on: boolean): string[] {
  const drop = new Set(ids);
  const kept = list.filter((id) => !drop.has(id));
  return on ? kept : [...kept, ...ids];
}
