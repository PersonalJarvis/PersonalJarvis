/**
 * The permission REST routes (`jarvis/ui/web/permissions_routes.py`) as plain
 * functions, shared by the Privacy page (`usePermissions`), the prompt layer
 * and the store seed.
 *
 * Reads never prompt. The three POSTs are user gestures: `request` asks macOS
 * (at most once per episode, backend side), `openSettings` opens the pane,
 * `reset` drops this app's own record so macOS can ask again. A 429 and a 409
 * come back as {@link PermissionApiError} so a caller can say something honest
 * instead of showing a status code.
 */
import {
  parsePermissionRow,
  parsePermissionSnapshot,
  type PermissionEnsurePayload,
  type PermissionId,
  type PermissionOperationPayload,
  type PermissionRow,
  type PermissionSnapshot,
} from "./permissionSnapshot";

export class PermissionApiError extends Error {
  readonly status: number;
  /** Seconds to wait, for a 429; 0 otherwise. */
  readonly retryAfterS: number;
  /** The body of the failed answer, when it parsed. */
  readonly body: unknown;

  constructor(message: string, status: number, retryAfterS = 0, body: unknown = null) {
    super(message);
    this.name = "PermissionApiError";
    this.status = status;
    this.retryAfterS = retryAfterS;
    this.body = body;
  }
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

async function readBody(res: Response): Promise<unknown> {
  return res.json().catch(() => null);
}

function failure(res: Response, body: unknown): PermissionApiError {
  const data = record(body);
  if (res.status === 429) {
    const wait = Number(data?.retry_after_s);
    return new PermissionApiError("rate_limited", 429, Number.isFinite(wait) ? wait : 5, body);
  }
  const message =
    typeof data?.message === "string"
      ? data.message
      : typeof data?.detail === "string"
        ? data.detail
        : `HTTP ${res.status}`;
  return new PermissionApiError(message, res.status, 0, body);
}

async function call(url: string, init?: RequestInit): Promise<unknown> {
  const res = await fetch(url, init);
  const body = await readBody(res);
  if (!res.ok) throw failure(res, body);
  return body;
}

/** Options of the two reads. */
export interface PermissionReadOptions {
  /**
   * True on the FIRST refetch after the window regained focus: the backend then
   * notes "the app was refocused" (promotes a prompt-once episode to blocked and
   * forces the Screen Recording window-title oracle) before it answers. Without
   * it a grant made in System Settings reads stale until the ~15 s fallback.
   */
  activated?: boolean;
}

/** The query string of a read: `?activated=1` only when the hint applies. */
function readQuery(options: PermissionReadOptions): string {
  return options.activated ? "?activated=1" : "";
}

/** `GET /api/permissions/status`. Null when the answer is not a v2 snapshot. */
export async function fetchPermissionSnapshot(
  options: PermissionReadOptions = {},
): Promise<PermissionSnapshot | null> {
  return parsePermissionSnapshot(await call(`/api/permissions/status${readQuery(options)}`));
}

/** `GET /api/permissions/{id}`: one cheap row (the card and the Privacy page poll this). */
export async function fetchPermissionRow(
  id: PermissionId,
  options: PermissionReadOptions = {},
): Promise<PermissionRow | null> {
  return parsePermissionRow(await call(`/api/permissions/${id}${readQuery(options)}`));
}

export interface PermissionRequestOptions {
  /** True ONLY after the person confirmed the grantee (the app that started Jarvis). */
  allow_outside_app?: boolean;
  /** `PERMISSION_FEATURES` token the ask is attributed to. */
  feature?: string;
  /** Automation only: the player's bundle id. */
  target?: string;
}

function postInit(body?: unknown): RequestInit {
  return body === undefined
    ? { method: "POST" }
    : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      };
}

/** `POST /api/permissions/{id}/request`. */
export async function requestPermission(
  id: PermissionId,
  options: PermissionRequestOptions = {},
): Promise<PermissionEnsurePayload> {
  const body: Record<string, unknown> = {};
  if (options.allow_outside_app) body.allow_outside_app = true;
  if (options.feature) body.feature = options.feature;
  if (options.target) body.target = options.target;
  const answer = await call(
    `/api/permissions/${id}/request?dry_run=false`,
    postInit(Object.keys(body).length > 0 ? body : undefined),
  );
  return answer as PermissionEnsurePayload;
}

/** `POST /api/permissions/{id}/open-settings`. */
export async function openPermissionSettings(
  id: PermissionId,
): Promise<PermissionOperationPayload> {
  return (await call(
    `/api/permissions/${id}/open-settings?dry_run=false`,
    postInit(),
  )) as PermissionOperationPayload;
}

/** `POST /api/permissions/{id}/reset` ("Ask again"). A 409 means nothing to reset. */
export async function resetPermission(id: PermissionId): Promise<PermissionOperationPayload> {
  return (await call(
    `/api/permissions/${id}/reset?dry_run=false`,
    postInit(),
  )) as PermissionOperationPayload;
}
