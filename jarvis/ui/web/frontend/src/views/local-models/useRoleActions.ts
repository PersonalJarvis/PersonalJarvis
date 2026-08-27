/**
 * The two writes a model card can make, and the progress line they report.
 *
 * Picking a model is one PUT. "Use recommended" is a small flow — download
 * the pick when it is missing, assign it, then write the suggested options
 * silently and read back one sentence — and it has to survive the card
 * unmounting mid-download, so it lives here rather than inside the card.
 *
 * Extracted from the old roles ledger unchanged in behaviour: the cards
 * replaced the rows, the writes did not change.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  setRole,
  useInvalidateLocalModels,
  useSetRole,
  type LocalModelRole,
  type RoleRow,
} from "@/hooks/useLocalModels";

import { canonical, waitForPull } from "./localSetup";
import { useSilentTune } from "./useLocalSetup";

export type RolePhase =
  | "idle"
  | "pulling"
  | "assigning"
  | "tuning"
  | "done"
  | "error";

export interface RoleProgress {
  phase: RolePhase;
  percent?: number;
  message?: string;
  /** The one-sentence readback after the silent tune. */
  readback?: string;
}

export const IDLE: RoleProgress = { phase: "idle" };

/** The roles a user may write from a card. */
const WRITABLE: readonly LocalModelRole[] = [
  "chat",
  "voice",
  "tools_screen",
  "deep",
  "embedding",
];

export interface RoleActions {
  /** Progress per role id; missing means idle. */
  progress: Record<string, RoleProgress>;
  /** True while that role's flow runs — the card's controls wait. */
  isBusy: (roleId: string) => boolean;
  /** Assign a model the user picked. */
  pick: (roleId: string, model: string) => void;
  /** Download the shortlist's pick when missing, assign it, tune it. */
  useRecommended: (row: RoleRow) => void;
  /** A failed PUT, as one sentence; "" when the last write went through. */
  writeError: string;
}

export function useRoleActions(
  providerId: string,
  installedNames: ReadonlySet<string>,
): RoleActions {
  const [progress, setProgress] = useState<Record<string, RoleProgress>>({});
  const setRoleMutation = useSetRole(providerId);
  const invalidate = useInvalidateLocalModels(providerId);
  const tuneSilently = useSilentTune(providerId);

  // A download outlives a tab switch; nothing is written to state afterwards.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const alive = useCallback(() => mounted.current, []);

  const patch = useCallback((roleId: string, next: RoleProgress) => {
    if (!mounted.current) return;
    setProgress((prev) => ({ ...prev, [roleId]: next }));
  }, []);

  const isInstalled = useCallback(
    (name: string) => installedNames.has(canonical(name)),
    [installedNames],
  );

  const pick = useCallback(
    (roleId: string, model: string) => {
      setRoleMutation.mutate({ role: roleId as LocalModelRole, model });
    },
    [setRoleMutation],
  );

  const useRecommended = useCallback(
    (row: RoleRow) => {
      const model = row.recommended;
      if (!model || !WRITABLE.includes(row.id as LocalModelRole)) return;
      void (async () => {
        try {
          if (!isInstalled(model)) {
            patch(row.id, { phase: "pulling", percent: 0, message: "" });
            await waitForPull(
              providerId,
              model,
              (p) => patch(row.id, { phase: "pulling", ...p }),
              alive,
            );
            if (!alive()) return;
          }
          patch(row.id, { phase: "assigning" });
          await setRole(providerId, row.id as LocalModelRole, model);
          patch(row.id, { phase: "tuning" });
          const readback = await tuneSilently(model);
          patch(row.id, { phase: "done", readback });
        } catch (err) {
          patch(row.id, {
            phase: "error",
            message: err instanceof Error ? err.message : String(err),
          });
        } finally {
          invalidate();
        }
      })();
    },
    [alive, invalidate, isInstalled, patch, providerId, tuneSilently],
  );

  const isBusy = useCallback(
    (roleId: string) => {
      const phase = progress[roleId]?.phase;
      const running =
        phase === "pulling" || phase === "assigning" || phase === "tuning";
      const saving =
        setRoleMutation.isPending && setRoleMutation.variables?.role === roleId;
      return running || saving;
    },
    [progress, setRoleMutation.isPending, setRoleMutation.variables],
  );

  const writeError = setRoleMutation.isError
    ? setRoleMutation.error instanceof Error
      ? setRoleMutation.error.message
      : String(setRoleMutation.error)
    : "";

  return { progress, isBusy, pick, useRecommended, writeError };
}
