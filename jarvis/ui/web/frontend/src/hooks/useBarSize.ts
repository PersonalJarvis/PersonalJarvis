import { useCallback, useEffect, useState } from "react";

/**
 * On-screen "Bar size" multiplier from GET /api/settings/bar-size. The value is
 * a 0.5–2.0 proportional factor (1.0 = the signed-off default look); the UI
 * renders it as a percent slider. Mirrors useTtsVolume, but setScale takes a
 * `persist` flag so the slider can stream LIVE (persist=false) resizes while
 * dragging and write jarvis.toml once on release (persist=true).
 */
export interface BarSizeConfig {
  scale: number;
  default: number;
  min: number;
  max: number;
}

export interface BarSizeSaveResult {
  ok: boolean;
  scale: number;
  default: number;
  persisted: boolean;
  applied_live: boolean;
  restart_required: boolean;
}

/** Loads /api/settings/bar-size and exposes setScale(scale, persist?). */
export function useBarSize() {
  const [config, setConfig] = useState<BarSizeConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/bar-size");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: BarSizeConfig = await res.json();
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

  const setScale = useCallback(
    async (scale: number, persist = true): Promise<BarSizeSaveResult> => {
      const res = await fetch("/api/settings/bar-size", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scale, persist }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
      const result = body as BarSizeSaveResult;
      setConfig((prev) => (prev ? { ...prev, scale: result.scale } : prev));
      return result;
    },
    [],
  );

  return { config, loading, error, refetch, setScale };
}
