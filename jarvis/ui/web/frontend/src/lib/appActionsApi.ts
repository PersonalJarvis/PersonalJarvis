/**
 * Typed client for Settings > Jarvis actions (jarvis/ui/web/app_actions_routes.py).
 * The shapes mirror the route's dicts; the backend stays the authority.
 */

export type ActionMode = "allow" | "ask" | "block";
export type ActionTier = "safe" | "monitor" | "ask" | "block";

export interface AppAction {
  id: string;
  method: string;
  path: string;
  area: string;
  title: string;
  description: string;
  dangerous: boolean;
  default_tier: ActionTier;
  mode: ActionMode | null;
  tier: ActionTier;
}

export interface AppActionCatalog {
  actions: AppAction[];
  areas: string[];
  count: number;
}

export interface AppActionHistoryRow {
  action: string;
  title: string;
  outcome: "ran" | "failed" | "blocked";
  detail: string;
  via: string;
  at: number;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as T;
}

export const appActionsApi = {
  list: async (): Promise<AppActionCatalog> => json(await fetch("/api/app-actions")),
  setMode: async (
    id: string,
    mode: ActionMode | null,
  ): Promise<{ id: string; mode: ActionMode | null; tier: ActionTier }> =>
    json(
      await fetch(`/api/app-actions/${encodeURIComponent(id)}/mode`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      }),
    ),
  history: async (limit = 50): Promise<{ history: AppActionHistoryRow[] }> =>
    json(await fetch(`/api/app-actions/history?limit=${limit}`)),
};
