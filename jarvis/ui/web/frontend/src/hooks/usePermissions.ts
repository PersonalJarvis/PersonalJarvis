import { useCallback, useEffect, useMemo, useState } from "react";

export type PermissionId =
  | "microphone"
  | "screen_recording"
  | "accessibility"
  | "input_monitoring"
  | "event_posting"
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
  restart_required: boolean;
  detail?: string | null;
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
  restart_required: boolean;
}

const EMPTY_SNAPSHOT: PermissionSnapshot = {
  platform: "unknown",
  supported: false,
  headless: false,
  app_identity: {},
  permissions: [],
  features: {},
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

  const refetch = useCallback(async () => {
    try {
      const payload = await readJson(await fetch("/api/permissions/status"));
      setSnapshot(normalizeSnapshot(payload));
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, []);

  const mutate = useCallback(
    async (id: PermissionId, action: "request" | "open-settings") => {
      setPendingId(id);
      try {
        const payload = await readJson(
          await fetch(`/api/permissions/${id}/${action}?dry_run=false`, {
            method: "POST",
          }),
        );
        setSnapshot(normalizeSnapshot(payload));
        setError(null);
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : String(exc));
        throw exc;
      } finally {
        setPendingId(null);
      }
    },
    [],
  );

  useEffect(() => {
    void refetch();
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

  return {
    snapshot,
    loading,
    error,
    pendingId,
    refetch,
    request: (id: PermissionId) => mutate(id, "request"),
    openSettings: (id: PermissionId) => mutate(id, "open-settings"),
  };
}
