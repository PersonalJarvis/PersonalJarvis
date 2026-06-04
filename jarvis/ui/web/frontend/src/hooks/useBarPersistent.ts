import { useCallback, useEffect, useState } from "react";

/** "Show bar at all times" (bar_persistent). Off = the bar only pops up on wake. */
export interface BarPersistentResult {
  ok: boolean;
  enabled: boolean;
  persisted: boolean;
  applied_live: boolean;
  restart_required: boolean;
}

export function useBarPersistent() {
  const [enabled, setEnabledState] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/bar-persistent");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setEnabledState(Boolean(data.enabled));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const setEnabled = useCallback(
    async (next: boolean): Promise<BarPersistentResult> => {
      const res = await fetch("/api/settings/bar-persistent", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
      setEnabledState(Boolean(body.enabled));
      return body as BarPersistentResult;
    },
    [],
  );

  return { enabled, loading, error, refetch, setEnabled };
}
