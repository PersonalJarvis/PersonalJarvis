/**
 * The TypeScript twin (five-layer pattern L4) of the macOS permission snapshot
 * served by `jarvis/ui/web/permissions_routes.py` (`GET /api/permissions/status`,
 * `GET /api/permissions/{id}`, `POST /api/permissions/{id}/request|open-settings|reset`).
 *
 * The four interfaces `PermissionSnapshot`, `PermissionAppIdentity`,
 * `PermissionRow` and `PermissionNeededEpisode` mirror the TypedDicts of the
 * route module key for key. `tests/unit/ui/web/test_permissions_snapshot.py`
 * regex-reads them (flat `name: type;` fields, one per line, no nested object
 * literals) and fails when a key drifts, so keep them flat.
 *
 * Nothing here talks to the network: `lib/permissionsApi.ts` fetches, and
 * `lib/permissionPrompts.ts` reduces the episodes.
 */
import type {
  PermissionFeature,
  PermissionNeededOrigin,
  PermissionNeededPhase,
  PermissionNeededReason,
} from "./permissionEvents";

/** The rows a person sees on the Privacy page, in the order the backend lists them. */
export const PERMISSION_ROW_IDS = [
  "microphone",
  "screen_recording",
  "accessibility",
  "input_monitoring",
  "automation",
  "credential_store",
] as const;
export type PermissionRowId = (typeof PERMISSION_ROW_IDS)[number];

/** Every id the routes accept; `event_posting` is an alias of `accessibility` and has no row. */
export type PermissionId = PermissionRowId | "event_posting";

/** The live state of one permission (`jarvis.platform.permissions.PermissionState`). */
export type PermissionState =
  | "granted"
  | "not_determined"
  | "denied"
  | "restricted"
  | "not_granted"
  | "unavailable"
  | "not_required";

/** Who this process is to macOS. */
export interface PermissionAppIdentity {
  /** The product name the UI shows in copy ("Personal Jarvis"); never a hardcoded string. */
  app_name: string;
  bundle_id: string | null;
  bundle_path: string | null;
  launched_as_bundle: boolean;
  /** True for the installed app bundle under an accepted bundle id: decides who may RESET. */
  stable: boolean;
}

/** One permission row. */
export interface PermissionRow {
  id: PermissionRowId;
  /** English fallback label; the UI renders its own i18n per row id. */
  label: string;
  status: PermissionState;
  /** Features (`PERMISSION_FEATURES` vocabulary) that use this permission. */
  used_for: string[];
  can_request: boolean;
  can_open_settings: boolean;
  can_reset: boolean;
  restart_hint: boolean;
  /** English sentence for logs and support; never rendered raw. */
  detail: string;
  /** The textual System Settings path (English); null off macOS. */
  settings_path: string | null;
}

/** One open episode: a feature waiting on a permission (`Episode.as_dict()`). */
export interface PermissionNeededEpisode {
  permissions: string[];
  feature: PermissionFeature;
  reason: PermissionNeededReason;
  phase: PermissionNeededPhase;
  origin: PermissionNeededOrigin;
  target: string;
  can_prompt: boolean;
  can_open_settings: boolean;
  outside_app: boolean;
  detail: string;
  trace_id: string;
  opened_at_ns: number;
}

/** `GET /api/permissions/status`. */
export interface PermissionSnapshot {
  platform: string;
  supported: boolean;
  headless: boolean;
  app_identity: PermissionAppIdentity;
  outside_installed_app: boolean;
  permissions: PermissionRow[];
  needed: PermissionNeededEpisode[];
}

/** What a request came to: `EnsureResult.outcome` of the just-in-time service. */
export type PermissionOutcome =
  | "granted"
  | "pending"
  | "denied"
  | "needs_settings"
  | "unavailable"
  | "not_required";

/** The answer of `POST /api/permissions/{id}/request`. */
export interface PermissionEnsurePayload {
  permission: string;
  outcome: PermissionOutcome;
  granted: boolean;
  state: PermissionState;
  asked: boolean;
  outside_installed_app: boolean;
  reason: string;
  can_prompt: boolean;
  can_open_settings: boolean;
  target: string;
  user_detail: string;
  agent_detail: string;
}

/** The answer of `/open-settings` and `/reset` (a 409 carries the same body with `ok: false`). */
export interface PermissionOperationPayload {
  ok: boolean;
  permission_id: string;
  action: string;
  performed: boolean;
  dry_run: boolean;
  message: string;
  permission: PermissionRow | null;
}

/** The body of a 429 from `/request` and `/open-settings`. */
export interface PermissionRateLimitedPayload {
  error: string;
  scope: string;
  action: string;
  permission_id: string;
  retry_after_s: number;
}

const READY_STATES: ReadonlySet<string> = new Set(["granted", "not_required"]);

/** True for a state that needs nothing from the user. */
export function isReadyState(state: string): boolean {
  return READY_STATES.has(state);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/**
 * Read a snapshot defensively: an older or newer backend must never crash the
 * UI. Returns null when the payload is not a v2 snapshot at all (for example a
 * v1 answer without `needed[]`), so callers keep what they had.
 */
export function parsePermissionSnapshot(payload: unknown): PermissionSnapshot | null {
  if (!isRecord(payload) || typeof payload.platform !== "string") return null;
  if (!Array.isArray(payload.permissions)) return null;
  // `needed` is what makes it a v2 snapshot: a backend that predates it cannot
  // say which episodes are open, and "none" would wipe the ones the socket delivered.
  if (!Array.isArray(payload.needed)) return null;
  const identity = isRecord(payload.app_identity) ? payload.app_identity : {};
  return {
    platform: payload.platform,
    supported: payload.supported === true,
    headless: payload.headless === true,
    app_identity: {
      app_name: typeof identity.app_name === "string" ? identity.app_name : "",
      bundle_id: typeof identity.bundle_id === "string" ? identity.bundle_id : null,
      bundle_path: typeof identity.bundle_path === "string" ? identity.bundle_path : null,
      launched_as_bundle: identity.launched_as_bundle === true,
      stable: identity.stable === true,
    },
    outside_installed_app: payload.outside_installed_app === true,
    permissions: payload.permissions.filter(isRecord).map(parsePermissionRow).filter(notNull),
    needed: payload.needed.filter(isRecord) as unknown as PermissionNeededEpisode[],
  };
}

function notNull<T>(value: T | null): value is T {
  return value !== null;
}

/** Read one row (also the answer of `GET /api/permissions/{id}`). Null when it has no id. */
export function parsePermissionRow(raw: unknown): PermissionRow | null {
  if (!isRecord(raw) || typeof raw.id !== "string") return null;
  return {
    id: raw.id as PermissionRowId,
    label: typeof raw.label === "string" ? raw.label : raw.id,
    status: (typeof raw.status === "string" ? raw.status : "unavailable") as PermissionState,
    used_for: Array.isArray(raw.used_for)
      ? raw.used_for.filter((entry): entry is string => typeof entry === "string")
      : [],
    can_request: raw.can_request === true,
    can_open_settings: raw.can_open_settings === true,
    can_reset: raw.can_reset === true,
    restart_hint: raw.restart_hint === true,
    detail: typeof raw.detail === "string" ? raw.detail : "",
    settings_path: typeof raw.settings_path === "string" ? raw.settings_path : null,
  };
}
