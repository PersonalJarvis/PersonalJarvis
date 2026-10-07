import { useCallback, useEffect, useState } from "react";

/**
 * What keeps running after the desktop window closes — GET/PUT
 * /api/settings/background (jarvis/core/background_service.py).
 */
export interface BackgroundAgentsConfig {
  keep_agents_running: boolean;
  background_only_at_login: boolean;
  autostart_enabled: boolean;
  /** This page is served by the windowless background service itself. */
  running_as_service: boolean;
  /** False on a dev instance: it stops with its window, by design. */
  supported: boolean;
  /** The work a quit would hand to the background service right now. */
  work: { routines: number; running: number; channels: string[] };
}

export type BackgroundAgentsPatch = Partial<
  Pick<BackgroundAgentsConfig, "keep_agents_running" | "background_only_at_login">
>;

/** Loads the background-agents settings and exposes save(). Mirrors useAutostart. */
export function useBackgroundAgents() {
  const [config, setConfig] = useState<BackgroundAgentsConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/background");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setConfig((await res.json()) as BackgroundAgentsConfig);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const save = useCallback(async (patch: BackgroundAgentsPatch) => {
    const res = await fetch("/api/settings/background", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
    setConfig(body as BackgroundAgentsConfig);
    return body as BackgroundAgentsConfig;
  }, []);

  return { config, loading, error, refetch, save };
}
