/**
 * What one row of Settings > Privacy shows: its pill and its (at most one) action.
 * Pure, so the vocabulary and the "one action per row" rule have a test of their own.
 *
 * | the row reads                           | pill            | its one action        |
 * |-----------------------------------------|-----------------|-----------------------|
 * | not asked yet (not_determined, or an     | Not asked yet   | Ask now (can_request) |
 * |   unanswered not_granted macOS can ask)  |                 |                       |
 * | off (denied, or not_granted after an ask)| Off             | Open System Settings  |
 * | granted                                  | Allowed         | none                  |
 * | restricted / unavailable / not_required  | same words      | none                  |
 * | a real failed use (restart_hint)         | Restart needed  | Quit and reopen       |
 * | Keychain, declined                       | Off             | Try again             |
 *
 * "Ask again" (a reset) and the stale-grant hint are NOT an action of a fresh Mac:
 * they come up only after the person opened System Settings from the row, came
 * back, and the row still reads off (`backFromSettings`).
 */
import { isReadyState, type PermissionRow } from "./permissionSnapshot";

/** The `permissions.status.*` key of the pill. */
export type PrivacyPill =
  | "granted"
  | "not_determined"
  | "denied"
  | "not_granted"
  | "restricted"
  | "unavailable"
  | "not_required"
  | "restart_pending";

export type PrivacyAction = "restart" | "ask" | "try_again" | "open_settings";

export interface PrivacyRowContext {
  /** The person already asked for this permission (from this page, or an open episode says so). */
  asked: boolean;
  /** The person opened System Settings from this row, returned, and the row was read again. */
  backFromSettings: boolean;
}

export interface PrivacyRowView {
  pill: PrivacyPill;
  /** The state is "off": a decision exists (or a request was made) and it does not allow access. */
  off: boolean;
  /** The one button of the row; null when the row needs nothing. */
  action: PrivacyAction | null;
  /** The textual pane path is worth showing (the off state only). */
  showPath: boolean;
  /** "Ask again" (reset) is offered. */
  showReset: boolean;
  /** The hint that an enabled switch in System Settings may belong to an older build. */
  showStaleHint: boolean;
}

export function privacyRowView(row: PermissionRow, context: PrivacyRowContext): PrivacyRowView {
  const keychain = row.id === "credential_store";
  const ready = isReadyState(row.status);
  const restart = row.restart_hint;
  const decided = row.status === "restricted" || row.status === "unavailable";

  // A binary permission reads "not granted" both before and after macOS was asked: it has
  // not been asked yet only while macOS can still be asked and nothing says it was.
  const neverAsked =
    row.status === "not_determined" ||
    (row.status === "not_granted" && !keychain && row.can_request && !context.asked);

  let pill: PrivacyPill;
  if (restart) pill = "restart_pending";
  else if (neverAsked) pill = "not_determined";
  else pill = row.status;

  const off = !restart && !ready && !decided && !neverAsked;

  let action: PrivacyAction | null = null;
  if (restart) action = "restart";
  else if (keychain) action = !ready && row.can_request ? "try_again" : null;
  else if (neverAsked) action = row.can_request ? "ask" : null;
  else if (off) action = row.can_open_settings ? "open_settings" : row.can_request ? "ask" : null;

  const stranded = off && !keychain && context.backFromSettings;
  return {
    pill,
    off,
    action,
    showPath: off && !keychain,
    showReset: stranded && row.can_reset,
    // No prompt left AND the grant is missing: a checkmark shown in System Settings belongs to
    // an older signature of the app (BUG-159). The backend decides when a reset helps.
    showStaleHint: stranded && row.can_reset && !row.can_request,
  };
}
