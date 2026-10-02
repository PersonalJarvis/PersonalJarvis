/**
 * Permission event vocabulary — the TypeScript mirror (five-layer pattern L4)
 * of the tuples in `jarvis/core/events.py` (`PERMISSION_FEATURES`,
 * `PERMISSION_NEEDED_REASONS`, `PERMISSION_NEEDED_PHASES`,
 * `PERMISSION_NEEDED_ORIGINS`). A Python parity test
 * (`tests/unit/core/test_permission_events.py`) regex-extracts the tuples below
 * and fails when they drift, so a feature or reason cannot be spelled one way
 * on the bus and another in the UI copy.
 *
 * Declarations only, no logic: `permissionToast.ts` reads these events.
 */

/** Product features that can ask for an OS permission. */
export const PERMISSION_FEATURES = [
  "voice",
  "dictation",
  "wake_word",
  "computer_use",
  "screen_context",
  "appshot",
  "window_control",
  "dictation_insert",
  "global_shortcuts",
  "audio_ducking",
  "browser_voice",
] as const;
export type PermissionFeature = (typeof PERMISSION_FEATURES)[number];

/** Why a feature is waiting on a permission. */
export const PERMISSION_NEEDED_REASONS = [
  "not_determined",
  "denied",
  "restricted",
  "needs_settings",
  "restart_hint",
  "unavailable",
] as const;
export type PermissionNeededReason = (typeof PERMISSION_NEEDED_REASONS)[number];

/** Where an episode is in front of the user: macOS is asking, or the user must act. */
export const PERMISSION_NEEDED_PHASES = ["os_dialog", "blocked"] as const;
export type PermissionNeededPhase = (typeof PERMISSION_NEEDED_PHASES)[number];

/** What caused the episode: a user gesture, or a background consumer. */
export const PERMISSION_NEEDED_ORIGINS = ["user", "background"] as const;
export type PermissionNeededOrigin = (typeof PERMISSION_NEEDED_ORIGINS)[number];

/** Payload of the `PermissionNeeded` WS event (`jarvis.core.events.PermissionNeeded`). */
export interface PermissionNeededPayload {
  /** `PermissionId` values of one coalesced episode (`event_posting` is folded into `accessibility`). */
  permissions: string[];
  feature: PermissionFeature;
  reason: PermissionNeededReason;
  phase: PermissionNeededPhase;
  origin: PermissionNeededOrigin;
  /** Automation target bundle id, else "". */
  target: string;
  can_prompt: boolean;
  can_open_settings: boolean;
  outside_app: boolean;
  /** English sentence for logs and support; the UI renders i18n copy instead. */
  detail: string;
}

/** Payload of the `PermissionResolved` WS event (`jarvis.core.events.PermissionResolved`). */
export interface PermissionResolvedPayload {
  permissions: string[];
  feature: PermissionFeature;
  granted: boolean;
}
