import { useCallback, useEffect, useState } from "react";

/** Optional browser lock (ui.require_browser_login). When ON, opening the UI
 * in a browser on this machine asks for the Control Key. OFF by default — the
 * local user walks straight in. Non-loopback access (another device, a VPS)
 * always requires the key regardless of this switch. */
export interface BrowserLockResult {
  ok: boolean;
  enabled: boolean;
  persisted: boolean;
  applied_live: boolean;
  /** True when the backend attached a fresh session cookie so the browser
   * that just enabled the lock stays signed in. */
  session_minted: boolean;
}

export function useBrowserLock() {
  const [enabled, setEnabledState] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/browser-login");
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
    async (next: boolean): Promise<BrowserLockResult> => {
      const res = await fetch("/api/settings/browser-login", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
      setEnabledState(Boolean(body.enabled));
      return body as BrowserLockResult;
    },
    [],
  );

  return { enabled, loading, error, refetch, setEnabled };
}
