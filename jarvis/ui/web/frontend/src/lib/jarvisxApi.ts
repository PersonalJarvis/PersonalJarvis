/**
 * Client for `/api/jarvisx` — the screenshot and screen-recording tool.
 *
 * Jarvis X never involves the assistant: it captures, keeps a library of the
 * captures on disk, and hands an image to the annotation editor. Every call
 * here is a thin, typed wrapper over one route; a failed call throws an Error
 * carrying the backend's own `detail` when it sent one.
 */

export type JarvisXKind = "image" | "video";
export type JarvisXMode = "region" | "window" | "fullscreen";
export type JarvisXRecordMode = "region" | "fullscreen";

/** The six global shortcuts, in the order the settings page lists them. */
export const JARVISX_HOTKEY_ACTIONS = [
  "region",
  "window",
  "fullscreen",
  "record_region",
  "record_fullscreen",
  "stop_recording",
] as const;

export type JarvisXHotkeyAction = (typeof JARVISX_HOTKEY_ACTIONS)[number];

export interface JarvisXShortcutStatus {
  hotkey: string;
  armed: boolean;
  detail: string;
}

export interface JarvisXSettings {
  enabled: boolean;
  hotkeys: Record<JarvisXHotkeyAction, string>;
  thumbnail_persist: boolean;
  thumbnail_dismiss_s: number;
  save_dir: string;
  copy_to_clipboard: boolean;
  sound: boolean;
  effect: boolean;
  recording_available: boolean;
  recording_detail: string;
  shortcuts: Partial<Record<string, JarvisXShortcutStatus>>;
}

export type JarvisXSettingsPatch = Partial<
  Pick<
    JarvisXSettings,
    | "enabled"
    | "thumbnail_persist"
    | "thumbnail_dismiss_s"
    | "save_dir"
    | "copy_to_clipboard"
    | "sound"
    | "effect"
  >
> & { hotkeys?: Partial<Record<JarvisXHotkeyAction, string>> };

export interface JarvisXItem {
  id: string;
  kind: JarvisXKind;
  mode: JarvisXMode;
  created_at: string;
  width: number;
  height: number;
  duration_s: number | null;
  filename: string;
  url: string;
  thumb_url: string;
  edited_url: string | null;
}

export interface JarvisXRecordStatus {
  recording: boolean;
  mode: JarvisXRecordMode | null;
  elapsed_s: number;
}

export interface JarvisXActionResult {
  ok: boolean;
  message?: string;
}

/** The thumbnail-dismiss range the settings page offers, in seconds. */
export const DISMISS_MIN_S = 3;
export const DISMISS_MAX_S = 300;
export const DISMISS_DEFAULT_S = 30;

/** Clamp a typed or dragged dismiss delay into the offered range. */
export function clampDismissSeconds(value: number): number {
  if (!Number.isFinite(value)) return DISMISS_DEFAULT_S;
  return Math.min(DISMISS_MAX_S, Math.max(DISMISS_MIN_S, Math.round(value)));
}

/** The websocket event names the backend publishes for this feature. */
export const JARVISX_EVENTS = {
  created: "JarvisXItemCreated",
  updated: "JarvisXItemUpdated",
  deleted: "JarvisXItemDeleted",
  recording: "JarvisXRecordingChanged",
} as const;

export function isJarvisXEvent(name: string): boolean {
  return (Object.values(JARVISX_EVENTS) as string[]).includes(name);
}

const BASE = "/api/jarvisx";

/** The readable reason out of a FastAPI error body (string or 422 list). */
export function errorDetail(body: unknown): string {
  if (!body || typeof body !== "object" || !("detail" in body)) return "";
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((entry) =>
        entry && typeof entry === "object" && "msg" in entry ? String((entry as { msg: unknown }).msg) : "",
      )
      .filter(Boolean)
      .join(" ");
  }
  if (detail && typeof detail === "object" && "message" in detail) {
    return String((detail as { message: unknown }).message);
  }
  return "";
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const body = (await response.json().catch(() => null)) as unknown;
  if (!response.ok) {
    throw new Error(errorDetail(body) || `Jarvis X request failed (${response.status}).`);
  }
  return body as T;
}

function jsonInit(method: string, payload?: unknown): RequestInit {
  return payload === undefined
    ? { method }
    : { method, headers: { "content-type": "application/json" }, body: JSON.stringify(payload) };
}

const itemPath = (id: string) => `${BASE}/items/${encodeURIComponent(id)}`;

export function fetchJarvisXSettings(): Promise<JarvisXSettings> {
  return request<JarvisXSettings>(`${BASE}/settings`);
}

export function saveJarvisXSettings(patch: JarvisXSettingsPatch): Promise<JarvisXSettings> {
  return request<JarvisXSettings>(`${BASE}/settings`, jsonInit("PUT", patch));
}

export async function fetchJarvisXItems(limit = 100): Promise<JarvisXItem[]> {
  const body = await request<{ items?: JarvisXItem[] }>(`${BASE}/items?limit=${limit}`);
  return sortNewestFirst(Array.isArray(body.items) ? body.items : []);
}

export function fetchJarvisXItem(id: string): Promise<JarvisXItem> {
  return request<JarvisXItem>(itemPath(id));
}

/** Upload the flattened annotation result as the item's edited version. */
export function saveJarvisXEdited(id: string, png: Blob): Promise<JarvisXItem> {
  return request<JarvisXItem>(`${itemPath(id)}/edited`, {
    method: "PUT",
    headers: { "content-type": "image/png" },
    body: png,
  });
}

export function copyJarvisXItem(id: string, edited: boolean): Promise<JarvisXActionResult> {
  return request<JarvisXActionResult>(`${itemPath(id)}/copy`, jsonInit("POST", { edited }));
}

export function revealJarvisXItem(id: string): Promise<JarvisXActionResult> {
  return request<JarvisXActionResult>(`${itemPath(id)}/reveal`, jsonInit("POST"));
}

export function deleteJarvisXItem(id: string): Promise<JarvisXActionResult> {
  return request<JarvisXActionResult>(itemPath(id), jsonInit("DELETE"));
}

export function openJarvisXEditor(id: string): Promise<JarvisXActionResult> {
  return request<JarvisXActionResult>(`${itemPath(id)}/open-editor`, jsonInit("POST"));
}

export function captureJarvisX(
  mode: JarvisXMode,
  delaySeconds?: number,
): Promise<{ ok: boolean; item?: JarvisXItem | null; message?: string }> {
  const payload = delaySeconds ? { mode, delay_s: delaySeconds } : { mode };
  return request(`${BASE}/capture`, jsonInit("POST", payload));
}

export function startJarvisXRecording(mode: JarvisXRecordMode): Promise<JarvisXActionResult> {
  return request<JarvisXActionResult>(`${BASE}/record/start`, jsonInit("POST", { mode }));
}

export function stopJarvisXRecording(): Promise<JarvisXActionResult> {
  return request<JarvisXActionResult>(`${BASE}/record/stop`, jsonInit("POST"));
}

export async function fetchJarvisXRecordStatus(): Promise<JarvisXRecordStatus> {
  const body = await request<Partial<JarvisXRecordStatus>>(`${BASE}/record/status`);
  return normalizeRecordStatus(body);
}

/** Coerce a status body or a JarvisXRecordingChanged payload into one shape. */
export function normalizeRecordStatus(raw: unknown): JarvisXRecordStatus {
  const body = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const mode = body.mode === "region" || body.mode === "fullscreen" ? body.mode : null;
  const elapsed = typeof body.elapsed_s === "number" && Number.isFinite(body.elapsed_s) ? body.elapsed_s : 0;
  return { recording: body.recording === true, mode, elapsed_s: Math.max(0, elapsed) };
}

function createdMs(item: JarvisXItem): number {
  const ms = Date.parse(item.created_at);
  return Number.isNaN(ms) ? 0 : ms;
}

export function sortNewestFirst(items: readonly JarvisXItem[]): JarvisXItem[] {
  return [...items].sort((a, b) => createdMs(b) - createdMs(a));
}

/** The editor window's own URL for one capture. */
export function jarvisXEditorUrl(id: string): string {
  return `/?view=jarvisx-editor&solo=1&item=${encodeURIComponent(id)}`;
}

/** "83.4" → "1:23"; an hour or longer → "1:02:03". */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "";
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/**
 * Cache-bust a media URL with a version token. The edited image keeps its URL
 * across saves, so without this an `<img>` shows the previous edit.
 */
export function withVersion(url: string, version: string | number): string {
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}v=${encodeURIComponent(String(version))}`;
}
