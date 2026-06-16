import { useCallback, useEffect, useState } from "react";

/** Voice silence window (the "think buffer") from GET /api/settings/silence-window. */
export interface SilenceWindowConfig {
  ms: number;
  default: number;
  min: number;
  max: number;
}

export interface SilenceWindowSaveResult {
  ok: boolean;
  ms: number;
  default: number;
  persisted: boolean;
  applied_live: boolean;
  restart_required: boolean;
}

/** Loads /api/settings/silence-window and exposes setMs(). Mirrors useAutostart. */
export function useSilenceWindow() {
  const [config, setConfig] = useState<SilenceWindowConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/silence-window");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: SilenceWindowConfig = await res.json();
      setConfig(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const setMs = useCallback(
    async (ms: number): Promise<SilenceWindowSaveResult> => {
      const res = await fetch("/api/settings/silence-window", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ms, persist: true }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
      const result = body as SilenceWindowSaveResult;
      setConfig((prev) => (prev ? { ...prev, ms: result.ms } : prev));
      return result;
    },
    [],
  );

  return { config, loading, error, refetch, setMs };
}
