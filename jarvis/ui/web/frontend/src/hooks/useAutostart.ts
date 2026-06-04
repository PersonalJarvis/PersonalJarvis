import { useCallback, useEffect, useState } from "react";

/**
 * Login-autostart state from GET /api/settings/autostart.
 * `supported` is false on a headless host (no display) — the toggle persists
 * the intent but cannot create an OS entry there.
 */
export interface AutostartConfig {
  enabled: boolean;
  supported: boolean;
  installed: boolean;
  matches_spec: boolean;
  platform: string;
  resolved_command: string;
  entry_path: string | null;
  detail: string;
}

export interface AutostartSaveResult extends AutostartConfig {
  ok: boolean;
  applied_live: boolean;
  persisted: boolean;
  restart_required: boolean;
}

/** Loads /api/settings/autostart and exposes setEnabled(). Mirrors useHotkey. */
export function useAutostart() {
  const [config, setConfig] = useState<AutostartConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/autostart");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: AutostartConfig = await res.json();
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

  const setEnabled = useCallback(
    async (enabled: boolean): Promise<AutostartSaveResult> => {
      const res = await fetch("/api/settings/autostart", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled, persist: true }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      const result = body as AutostartSaveResult;
      // Reflect the authoritative server state locally.
      setConfig(result);
      return result;
    },
    [],
  );

  return { config, loading, error, refetch, setEnabled };
}
