import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { bootSettled } from "@/lib/bootStagger";

export type PermissionId =
  | "microphone"
  | "screen_recording"
  | "accessibility"
  | "input_monitoring"
  | "event_posting"
  | "automation"
  | "credential_store";

export type PermissionState =
  | "granted"
  | "not_determined"
  | "denied"
  | "restricted"
  | "not_granted"
  | "unavailable"
  | "not_required";

export interface PermissionItem {
  id: PermissionId;
  status: PermissionState;
  required: string[];
  can_request: boolean;
  can_open_settings: boolean;
  /**
   * Whether dropping this app's own TCC record is a sensible next step. Always
   * read this instead of testing `status === "denied"`: the Screen Recording
   * and Accessibility preflights report a grant stranded on an older app
   * signature as plain "not_granted", so the denial test hid the reset from
   * exactly the two rows that need it most (BUG-159).
   */
  can_reset: boolean;
  restart_required: boolean;
  detail?: string | null;
}

/** Set when a rebuild changed the app signature and macOS discarded the grants. */
export interface PermissionIdentityReset {
  reason: string;
  services: string[];
}

export interface PermissionFeature {
  ready: boolean;
  missing: PermissionId[];
}

export interface PermissionSnapshot {
  platform: string;
  supported: boolean;
  headless: boolean;
  app_identity: {
    app_name?: string;
    expected_bundle_id?: string;
    bundle_id?: string | null;
    bundle_path?: string | null;
    launched_as_bundle?: boolean;
    stable?: boolean;
    foreground?: boolean;
  };
  permissions: PermissionItem[];
  features: Record<string, PermissionFeature>;
  identity_reset?: PermissionIdentityReset | null;
  restart_required: boolean;
}

/**
 * The guided flow asks in this order: the pure dialogs first (a click each),
 * then the rows that end in a System Settings switch, so the user is never
 * bounced between Settings and the app more than once per row.
 */
export const SETUP_ORDER: readonly PermissionId[] = [
  "microphone",
  "automation",
  "accessibility",
  "input_monitoring",
  "screen_recording",
  "event_posting",
  "credential_store",
];

export interface SetupProgress {
  id: PermissionId;
  index: number;
  total: number;
  /** "prompt" while the native dialog is up, "settings" once only a switch in System Settings is left. */
  phase: "prompt" | "settings";
}

export type SetupOutcome = "complete" | "cancelled" | "timeout" | "restart";

const SETTLED_STATES = new Set(["granted", "not_required"]);

/** A row the guided flow still has to deal with. */
export function needsSetup(item: PermissionItem): boolean {
  return (
    item.required.length > 0 &&
    !SETTLED_STATES.has(item.status) &&
    !item.restart_required &&
    item.status !== "unavailable" &&
    item.status !== "restricted"
  );
}

function settled(item: PermissionItem | undefined): boolean {
  return !item || !needsSetup(item);
}

const EMPTY_SNAPSHOT: PermissionSnapshot = {
  platform: "unknown",
  supported: false,
  headless: false,
  app_identity: {},
  permissions: [],
  features: {},
  identity_reset: null,
  restart_required: false,
};

async function readJson(res: Response): Promise<unknown> {
  const payload = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = payload && typeof payload === "object"
      ? "detail" in payload
        ? String((payload as { detail: unknown }).detail)
        : "message" in payload
          ? String((payload as { message: unknown }).message)
          : `HTTP ${res.status}`
      : `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return payload;
}

function normalizeSnapshot(payload: unknown): PermissionSnapshot {
  if (!payload || typeof payload !== "object") return EMPTY_SNAPSHOT;
  const outer = payload as { snapshot?: unknown };
  const raw = outer.snapshot ?? payload;
  if (!raw || typeof raw !== "object") return EMPTY_SNAPSHOT;
  const value = raw as Partial<PermissionSnapshot>;
  return {
    ...EMPTY_SNAPSHOT,
    ...value,
    app_identity: value.app_identity ?? {},
    permissions: Array.isArray(value.permissions) ? value.permissions : [],
    features: value.features ?? {},
  };
}

export function usePermissions() {
  const [snapshot, setSnapshot] = useState<PermissionSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<PermissionId | null>(null);
  const [setupProgress, setSetupProgress] = useState<SetupProgress | null>(null);
  const setupCancelled = useRef(false);

  const fetchSnapshot = useCallback(async (): Promise<PermissionSnapshot> => {
    const next = normalizeSnapshot(await readJson(await fetch("/api/permissions/status")));
    setSnapshot(next);
    return next;
  }, []);

  const refetch = useCallback(async () => {
    try {
      await fetchSnapshot();
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [fetchSnapshot]);

  const mutate = useCallback(
    async (
      id: PermissionId,
      action: "request" | "open-settings" | "reset",
    ): Promise<PermissionSnapshot> => {
      setPendingId(id);
      try {
        const payload = await readJson(
          await fetch(`/api/permissions/${id}/${action}?dry_run=false`, {
            method: "POST",
          }),
        );
        const next = normalizeSnapshot(payload);
        setSnapshot(next);
        setError(null);
        return next;
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
        throw exc;
      } finally {
        setPendingId(null);
      }
    },
    [],
  );

  /**
   * The one-click flow: walk every missing row, fire its dialog (or open its
   * Settings pane) and wait for macOS to report the grant before moving on.
   * Ends with one automatic restart when a granted row only applies to a
   * fresh process (Screen Recording, Input Monitoring, Accessibility) and
   * `autoRestart` is set — onboarding owns its own final restart instead.
   */
  const setupAll = useCallback(
    async (options: { autoRestart?: boolean; pollMs?: number; rowTimeoutMs?: number } = {}) => {
      const { autoRestart = false, pollMs = 1500, rowTimeoutMs = 180_000 } = options;
      setupCancelled.current = false;
      let latest = await fetchSnapshot();
      const queue = SETUP_ORDER.filter((id) =>
        latest.permissions.some((item) => item.id === id && needsSetup(item)),
      );
      let outcome: SetupOutcome = "complete";
      try {
        for (const [index, id] of queue.entries()) {
          const item = latest.permissions.find((entry) => entry.id === id);
          if (settled(item)) continue;
          setSetupProgress({ id, index: index + 1, total: queue.length, phase: "prompt" });
          if (item?.can_request) {
            latest = await mutate(id, "request");
          } else if (item?.can_open_settings) {
            latest = await mutate(id, "open-settings");
          } else {
            continue;
          }
          const deadline = Date.now() + rowTimeoutMs;
          let current = latest.permissions.find((entry) => entry.id === id);
          while (!settled(current)) {
            if (setupCancelled.current) {
              outcome = "cancelled";
              return outcome;
            }
            if (Date.now() > deadline) {
              outcome = "timeout";
              return outcome;
            }
            setSetupProgress({ id, index: index + 1, total: queue.length, phase: "settings" });
            await new Promise((resolve) => window.setTimeout(resolve, pollMs));
            latest = await fetchSnapshot();
            current = latest.permissions.find((entry) => entry.id === id);
          }
        }
        if (autoRestart && latest.restart_required) {
          outcome = "restart";
          const response = await fetch("/api/settings/restart-app", { method: "POST" });
          if (!response.ok) {
            throw new Error(
              response.status === 409 ? "restart-missions-running" : `restart-failed:${response.status}`,
            );
          }
        }
        return outcome;
      } finally {
        setSetupProgress(null);
      }
    },
    [fetchSnapshot, mutate],
  );

  const cancelSetup = useCallback(() => {
    setupCancelled.current = true;
  }, []);

  useEffect(() => {
    // Non-critical: the banner can appear a few seconds late; the first-mount
    // burst must not spend a connection on it (see bootStagger).
    void bootSettled().then(refetch);
  }, [refetch]);

  useEffect(() => {
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") void refetch();
    };
    window.addEventListener("focus", refreshWhenVisible);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.removeEventListener("focus", refreshWhenVisible);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [refetch]);

  const waitingForSystemSettings = useMemo(
    () =>
      snapshot?.permissions.some(
        (permission) =>
          permission.required.length > 0 &&
          !["granted", "not_required", "unavailable"].includes(permission.status),
      ) ?? false,
    [snapshot],
  );

  useEffect(() => {
    if (!waitingForSystemSettings) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refetch();
    }, 2500);
    return () => window.clearInterval(timer);
  }, [refetch, waitingForSystemSettings]);

  const setupNeeded = useMemo(
    () => snapshot?.permissions.some(needsSetup) ?? false,
    [snapshot],
  );

  return {
    snapshot,
    loading,
    error,
    pendingId,
    refetch,
    request: async (id: PermissionId) => {
      await mutate(id, "request");
    },
    openSettings: async (id: PermissionId) => {
      await mutate(id, "open-settings");
    },
    reset: async (id: PermissionId) => {
      await mutate(id, "reset");
    },
    setupAll,
    cancelSetup,
    setupProgress,
    setupNeeded,
  };
}
