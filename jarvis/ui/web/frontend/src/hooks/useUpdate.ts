import { useCallback, useEffect, useState } from "react";

/**
 * Result of GET /api/update/status. ``managed`` is false on any checkout that is
 * not an installer-managed install (a dev tree, a manual clone) — the update
 * button never renders there. ``update_available`` is only ever true on a
 * managed install with a newer published GitHub Release.
 */
export interface UpdateStatus {
  managed: boolean;
  current: string;
  latest: string | null;
  update_available: boolean;
  notes: string | null;
  published_at: string | null;
  release_url?: string | null;
  check_failed?: boolean;
}

// Slow poll: a new release is a rare event, so 6h keeps the button fresh
// without hammering GitHub. A window focus also triggers a check.
const POLL_INTERVAL_MS = 6 * 60 * 60 * 1000;

async function fetchStatus(force = false): Promise<UpdateStatus | null> {
  try {
    const res = await fetch(`/api/update/status${force ? "?force=true" : ""}`);
    if (!res.ok) return null;
    return (await res.json()) as UpdateStatus;
  } catch {
    // A failed status check must never disrupt the app — the button just
    // stays hidden until the next successful poll.
    return null;
  }
}

/**
 * Polls /api/update/status so the top bar can surface an "Update available"
 * button on its own, like a native auto-updater. Applying + restarting is done
 * by the caller (TopBar), which owns the toast + mission-guard (409) UX.
 */
export function useUpdate() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);

  const refresh = useCallback(async (force = false) => {
    const data = await fetchStatus(force);
    if (data) setStatus(data);
    return data;
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
    const onFocus = () => void refresh();
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, [refresh]);

  return { status, refresh };
}
