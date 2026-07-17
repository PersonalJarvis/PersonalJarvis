import { useCallback, useEffect, useState } from "react";

/**
 * Dedicated Wiki-curator provider/model selection.
 *
 * Reads/writes `GET/PUT /api/settings/wiki-provider`, which exposes the
 * `[memory.wiki.curator].provider` / `.model` config pair. An empty provider
 * means "follow brain.primary"; an empty model means "use that provider's
 * cheap/fast model" (the ack-brain follow_brain pattern). `available` lists the
 * Brain providers the backend will accept as objects carrying each provider's
 * id plus its selectable model ids; the empty "follow primary" / "cheap
 * default" sentinels are rendered by the UI as dedicated options.
 */
export interface WikiProviderOption {
  provider: string;
  models: string[];
  /** "agent" = OAuth-CLI Jarvis-Agent provider (Codex/Antigravity), "api" otherwise. */
  kind?: "api" | "agent";
  /** Whether the wiki fallback chain sees a usable credential for this provider. */
  ready?: boolean;
}

/**
 * What the NEXT maintenance run will actually use — resolved by the backend
 * through the same helper the runtime uses, so this is fact, not a guess.
 * `ready: false` means the key-aware chain will cross to another provider.
 */
export interface WikiResolvedState {
  provider: string;
  model: string;
  ready: boolean;
}

export interface WikiProviderState {
  provider: string;
  model: string;
  available: WikiProviderOption[];
  resolved?: WikiResolvedState;
  brain_primary?: string;
  // PUT-only echo fields (the save response reuses this shape).
  persisted?: boolean;
  applied_live?: boolean;
  restart_required?: boolean;
}

const ENDPOINT = "/api/settings/wiki-provider";

export function useWikiProvider() {
  const [data, setData] = useState<WikiProviderState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch(ENDPOINT);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body: WikiProviderState = await res.json();
      setData(body);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  return { data, loading, error, refetch };
}

/**
 * Persists the Wiki curator provider/model. Empty strings are valid and mean
 * "follow brain.primary" (provider) / "cheap default" (model). Returns the
 * server's resolved state so the UI reflects what the backend actually applied.
 */
export async function saveWikiProvider(
  provider: string,
  model: string,
): Promise<WikiProviderState> {
  const res = await fetch(ENDPOINT, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, model }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return body as WikiProviderState;
}
