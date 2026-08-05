import { useCallback, useEffect, useState } from "react";

/**
 * On-screen overlay style from GET /api/settings/overlay-style.
 * "jarvis_bar" = slim default bar, "mascot" = ghost mascot, "voice_orb" = the
 * procedural voice orb, "none" = hidden.
 *
 * Mirror of the backend's one list (``jarvis/ui/overlay_styles.py``); the two
 * are pinned together by tests/unit/ui/test_overlay_style_parity.py, which
 * reads THIS union and the i18n labels. Keep the order identical — it is the
 * order both pickers render.
 */
export type OverlayStyle = "jarvis_bar" | "mascot" | "voice_orb" | "none";

/** Client-side fallback while GET is in flight (same order as the backend). */
export const OVERLAY_STYLES: OverlayStyle[] = [
  "jarvis_bar",
  "mascot",
  "voice_orb",
  "none",
];

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
          : { style: result.style, options: [...OVERLAY_STYLES] },
      );
      return result;
    },
    [],
  );

  return { config, loading, error, refetch, saveStyle };
}
