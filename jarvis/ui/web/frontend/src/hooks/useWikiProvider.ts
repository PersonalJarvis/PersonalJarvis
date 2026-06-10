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
}

export interface WikiProviderState {
  provider: string;
  model: string;
  available: WikiProviderOption[];
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
