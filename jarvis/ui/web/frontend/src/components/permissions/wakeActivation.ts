import { PERMISSION_NEEDED_REASONS, type PermissionNeededReason } from "@/lib/permissionEvents";
import type { PermissionOutcome } from "@/lib/permissionSnapshot";
import type { LocalPermissionState } from "./InlinePermissionNote";

/** The `permission` answer of `POST /api/settings/wake-word/activation`. */
export interface WakeActivationPermission {
  outcome: PermissionOutcome;
  /** A `PermissionNeeded` reason; empty when nothing is needed (granted, not required). */
  reason: PermissionNeededReason | "";
  can_open_settings: boolean;
}

const REASON_FOR_OUTCOME: Record<string, PermissionNeededReason> = {
  pending: "not_determined",
  denied: "denied",
  needs_settings: "needs_settings",
  unavailable: "unavailable",
};

/**
 * What the wake switch's answer means for an inline note, or null when there is
 * nothing to explain (granted, not required on this OS, or an older backend that
 * sends no `permission`).
 *
 * `pending` means macOS is asking right now (the switch was the gesture); every
 * other non-granted outcome needs the person to act. The backend's `reason` is
 * trusted only when it is one of the shared vocabulary, otherwise the outcome
 * decides, so an unknown word never renders a bare key.
 */
export function wakeActivationLocalState(
  permission: WakeActivationPermission | undefined,
): LocalPermissionState | null {
  if (!permission) return null;
  const { outcome } = permission;
  if (outcome === "granted" || outcome === "not_required") return null;
  const known = (PERMISSION_NEEDED_REASONS as readonly string[]).includes(permission.reason);
  const reason = known
    ? (permission.reason as PermissionNeededReason)
    : (REASON_FOR_OUTCOME[outcome] ?? "unavailable");
  return {
    permissions: ["microphone"],
    reason,
    phase: outcome === "pending" ? "os_dialog" : "blocked",
    can_open_settings: permission.can_open_settings,
  };
}
