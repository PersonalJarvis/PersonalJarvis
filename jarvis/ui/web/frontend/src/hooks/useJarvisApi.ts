import { useCallback, useEffect, useState } from "react";

/**
 * Per-user Jarvis Control API key from GET /api/control/api-key.
 * `key` is the clear value (loopback-permitted so the desktop panel can render
 * it); `masked` is the jctl_…last4 form shown by default.
 */
export interface ControlApiKey {
  key: string | null;
  masked: string;
}

interface RotateResult {
  ok: boolean;
  key: string;
  masked: string;
}

/** Loads /api/control/api-key and exposes rotate() + setKey(). Mirrors useAutostart. */
export function useJarvisApi() {
  const [data, setData] = useState<ControlApiKey | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/control/api-key");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body: ControlApiKey = await res.json();
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

  const rotate = useCallback(async (): Promise<RotateResult> => {
    const res = await fetch("/api/control/api-key/rotate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body.detail ?? `HTTP ${res.status}`);
    }
    const result = body as RotateResult;
    setData({ key: result.key, masked: result.masked });
    return result;
  }, []);

  /**
   * Replace the key with a user-chosen value (PUT /api/control/api-key).
   * The response carries only the masked form; the clear value is what the
   * user just typed, so the local state can be updated without a refetch.
   */
  const setKey = useCallback(async (value: string): Promise<void> => {
    const res = await fetch("/api/control/api-key", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value, confirm: true }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body.detail ?? `HTTP ${res.status}`);
    }
    setData({ key: value.trim(), masked: (body as { masked?: string }).masked ?? "…" });
  }, []);

  return { data, loading, error, refetch, rotate, setKey };
}
