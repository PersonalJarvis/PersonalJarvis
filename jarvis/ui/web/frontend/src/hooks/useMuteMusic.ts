import { useCallback, useEffect, useState } from "react";

import type { PermissionNeededReason } from "@/lib/permissionEvents";
import type { PermissionOutcome } from "@/lib/permissionSnapshot";

/**
 * The permission answer for ONE media player (`PlayerPermission.as_dict()` in
 * `jarvis/audio/ducking/protocol.py`). `outcome` is a `PermissionOutcome` value,
 * `reason` a `PermissionNeeded` reason; `detail` is an English support sentence
 * the UI never renders (it writes its own, naming `player`).
 */
export interface MuteMusicPlayerPermission {
  player: string;
  target: string;
  outcome: PermissionOutcome;
  /** A `PermissionNeeded` reason; empty when nothing is needed (granted). */
  reason: PermissionNeededReason | "";
  can_open_settings: boolean;
  asked: boolean;
  outside_installed_app: boolean;
  detail: string;
}

/**
 * What switching the feature ON found out (`DuckPermissionReport.as_dict()`).
 * macOS answers for a player only while it RUNS, so `players` holds just the
 * running ones and `not_running` names the rest.
 */
export interface MuteMusicPermission {
  feature: string;
  checked: boolean;
  asked: boolean;
  note: string;
  not_running: string[];
  players: MuteMusicPlayerPermission[];
}

/** "Mute music while dictating" (ducking.enabled). */
export interface MuteMusicResult {
  ok: boolean;
  enabled: boolean;
  persisted: boolean;
  applied_live: boolean;
  /** Only on macOS, only when switching on; absent elsewhere and from an older backend. */
  permission?: MuteMusicPermission;
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
