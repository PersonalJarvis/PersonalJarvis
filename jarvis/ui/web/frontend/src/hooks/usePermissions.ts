import { useCallback, useEffect, useRef, useState } from "react";

import { onSharedReturnToWindow } from "@/lib/focusRefresh";
import {
  fetchPermissionSnapshot,
  openPermissionSettings,
  requestPermission,
  resetPermission,
  type PermissionRequestOptions,
} from "@/lib/permissionsApi";
import type {
  PermissionEnsurePayload,
  PermissionId,
  PermissionOperationPayload,
  PermissionSnapshot,
} from "@/lib/permissionSnapshot";
import { usePermissionsStore } from "@/store/permissions";

export type {
  PermissionId,
  PermissionRow,
  PermissionSnapshot,
  PermissionState,
} from "@/lib/permissionSnapshot";

/**
 * A passive read of the macOS permission snapshot for the Privacy page, plus
 * the three gestures a row offers.
 *
 * Passive means: it reads on mount, when the person comes back to the window
 * (one coalesced, jittered refetch, see `lib/focusRefresh`) and after an
 * action it ran itself. There is no interval, no wizard queue and no restart
 * machinery here: nothing polls while the person looks at the page, and
 * nothing asks macOS until they press a button.
 */
export function usePermissions() {
  const [snapshot, setSnapshot] = useState<PermissionSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<PermissionId | null>(null);
  const inflight = useRef<Promise<void> | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const activatedNext = useRef(false);

  const refetch = useCallback((options: { activated?: boolean } = {}): Promise<void> => {
    if (options.activated) activatedNext.current = true;
    // Single-flight: a focus event during a read joins it instead of starting another.
    // An `activated` hint that arrives mid-read is not lost: one more read follows it.
    if (inflight.current) {
      return options.activated ? inflight.current.then(() => refetch()) : inflight.current;
    }
    const activated = activatedNext.current;
    activatedNext.current = false;
    const run = (async () => {
      try {
        const next = await fetchPermissionSnapshot({ activated });
        if (!mounted.current) return;
        if (next) {
          setSnapshot(next);
          usePermissionsStore.getState().setSnapshot(next);
          setError(null);
        } else {
          setError("unreadable");
        }
      } catch (exc) {
        if (mounted.current) setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        inflight.current = null;
        if (mounted.current) setLoading(false);
      }
    })();
    inflight.current = run;
    return run;
  }, []);

  useEffect(() => {
    void refetch();
    return onSharedReturnToWindow(() => void refetch({ activated: true }));
  }, [refetch]);

  /** Run a gesture, then read the page again so the row shows what macOS now says. */
  const act = useCallback(
    async <T>(id: PermissionId, call: () => Promise<T>): Promise<T> => {
      setPendingId(id);
      try {
        return await call();
      } finally {
        if (mounted.current) setPendingId(null);
        await refetch();
      }
    },
    [refetch],
  );

  return {
    snapshot,
    loading,
    error,
    pendingId,
    refetch,
    request: (id: PermissionId, options?: PermissionRequestOptions): Promise<PermissionEnsurePayload> =>
      act(id, () => requestPermission(id, options)),
    openSettings: (id: PermissionId): Promise<PermissionOperationPayload> =>
      act(id, () => openPermissionSettings(id)),
    reset: (id: PermissionId): Promise<PermissionOperationPayload> =>
      act(id, () => resetPermission(id)),
  };
}
