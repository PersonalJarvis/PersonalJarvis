import { useCallback, useEffect, useState } from "react";

/**
 * On-screen overlay style from GET /api/settings/overlay-style.
 * "jarvis_bar" = slim default bar, "mascot" = ghost orb, "none" = hidden.
 */
export type OverlayStyle = "jarvis_bar" | "mascot" | "none";

export interface OverlayStyleConfig {
  style: OverlayStyle;
  options: OverlayStyle[];
}

export interface OverlayStyleSaveResult {
  ok: boolean;
  style: OverlayStyle;
  persisted: boolean;
  applied_live: boolean;
  restart_required: boolean;
  detail?: string;
}

/** Loads the overlay style and exposes saveStyle(). Mirrors useAutostart. */
export function useOverlayStyle() {
  const [config, setConfig] = useState<OverlayStyleConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/overlay-style");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: OverlayStyleConfig = await res.json();
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

  const saveStyle = useCallback(
    async (style: OverlayStyle): Promise<OverlayStyleSaveResult> => {
      const res = await fetch("/api/settings/overlay-style", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ style, persist: true }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      const result = body as OverlayStyleSaveResult;
      setConfig((prev) =>
        prev
          ? { ...prev, style: result.style }
          : { style: result.style, options: ["jarvis_bar", "mascot", "none"] },
      );
      return result;
    },
    [],
  );

  return { config, loading, error, refetch, saveStyle };
}
