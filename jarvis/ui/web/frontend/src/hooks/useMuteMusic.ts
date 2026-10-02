import { useCallback, useEffect, useState } from "react";

/** "Mute music while dictating" (ducking.enabled). */
export interface MuteMusicResult {
  ok: boolean;
  enabled: boolean;
  persisted: boolean;
  applied_live: boolean;
}

export function useMuteMusic() {
  const [enabled, setEnabledState] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/mute-music");
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
    async (next: boolean): Promise<MuteMusicResult> => {
      const res = await fetch("/api/settings/mute-music", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
      setEnabledState(Boolean(body.enabled));
      return body as MuteMusicResult;
    },
    [],
  );

  return { enabled, loading, error, refetch, setEnabled };
}
