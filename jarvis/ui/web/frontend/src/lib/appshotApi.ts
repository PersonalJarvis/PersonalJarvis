/**
 * Client for `/api/appshot` — the front window as conversation context.
 *
 * Held pixels come from the two image endpoints, which the backend serves with
 * `Cache-Control: no-store`; nothing here keeps a copy. The gallery's pictures
 * come from `/api/appshot/library`, versioned by their edit time.
 */

export type AppshotTarget = "auto" | "message" | "voice";

export interface AppshotShortcutStatus {
  hotkey: string;
  armed: boolean;
  detail: string;
}

export type AppshotScope = "window" | "region";

export interface AppshotSettings {
  enabled: boolean;
  hotkey: string;
  /** Shortcut for an area appshot: drag a rectangle, that part is captured. */
  region_hotkey: string;
  recording_hotkey?: string;
  recording_resolution?: "720p" | "1080p" | "1440p" | "2160p" | "native";
  recording_fps?: 30 | 60 | 120;
  recording_bitrate_mbps?: number;
  recording_system_audio?: boolean;
  recording_shortcut?: AppshotShortcutStatus;
  target: AppshotTarget;
  sound: boolean;
  effect: boolean;
  /** Seconds the corner card rests; 0 = until the user closes it. */
  card_seconds: number;
  /** Keep every appshot and edit in the gallery. Absent on an older backend. */
  library?: boolean;
  /** Keep only the newest N screenshots and N recordings; 0 = keep all. Absent on an older backend. */
  keep_newest?: number;
  /** Copy shortcut and button appshots to the clipboard. Absent on an older backend. */
  copy_to_clipboard?: boolean;
  sound_effects_master: boolean;
  shortcut: AppshotShortcutStatus;
  region_shortcut: AppshotShortcutStatus;
  readiness: {
    capture: boolean;
    capture_detail: string;
    effect: boolean;
    effect_detail: string;
    region: boolean;
    region_detail: string;
  };
}

export interface AppshotMeta {
  id: string;
  width: number;
  height: number;
  label: string;
  app_name: string;
  trigger: string;
  taken_at: number;
  delivered_to: string;
}

export type AppshotSettingsPatch = Partial<
  Pick<
    AppshotSettings,
    "enabled" | "hotkey" | "region_hotkey" | "recording_hotkey" | "target" | "sound" | "effect" | "card_seconds" | "library"
    | "copy_to_clipboard" | "keep_newest"
    | "recording_resolution" | "recording_fps" | "recording_bitrate_mbps" | "recording_system_audio"
  >
>;

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const body = (await response.json().catch(() => null)) as T | { detail?: unknown } | null;
  if (!response.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body && typeof body.detail === "string"
        ? body.detail
        : "";
    throw new Error(detail || `Appshot request failed (${response.status}).`);
  }
  return body as T;
}

export function fetchAppshotSettings(): Promise<AppshotSettings> {
  return request<AppshotSettings>("/api/appshot/settings");
}

export function saveAppshotSettings(patch: AppshotSettingsPatch): Promise<AppshotSettings> {
  return request<AppshotSettings>("/api/appshot/settings", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(patch),
  });
}

export function fetchLatestAppshot(): Promise<{ appshot: AppshotMeta | null }> {
  return request<{ appshot: AppshotMeta | null }>("/api/appshot/latest");
}

export function latestAppshotImageUrl(id: string, revision = 0): string {
  // The id picks that kept appshot (an older card in the corner stack); with
  // the edit revision it also busts the <img> cache.
  const v = revision ? `${id}-${revision}` : id;
  return `/api/appshot/latest/image?id=${encodeURIComponent(id)}&v=${encodeURIComponent(v)}`;
}

export function takeAppshot(
  delaySeconds = 0,
  scope: AppshotScope = "window",
): Promise<{ ok: true; appshot: AppshotMeta } | { ok: false; reason: string; message: string }> {
  return request("/api/appshot/take", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ delay_s: delaySeconds, scope }),
  });
}

export function forgetAppshots(): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>("/api/appshot", { method: "DELETE" });
}

export function fetchPendingAppshot(): Promise<{ appshot: AppshotMeta | null }> {
  return request<{ appshot: AppshotMeta | null }>("/api/appshot/pending");
}

/** Take the waiting appshot out of the backend, as a file for the composer. */
export async function claimPendingAppshot(meta: AppshotMeta): Promise<File | null> {
  const response = await fetch("/api/appshot/pending/claim", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ id: meta.id }),
  });
  if (!response.ok) return null;
  const blob = await response.blob();
  const stamp = new Date(meta.taken_at * 1000)
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\..*$/, "")
    .replace("T", "-");
  const extension = blob.type === "image/png" ? "png" : "jpg";
  return new File([blob], `appshot-${stamp}.${extension}`, { type: blob.type || "image/jpeg" });
}

/** "alt+alt" → "Alt + Alt" (⌥ on a Mac), "shift+shift" → "Shift + Shift"; any other combo, title-cased. */
export function formatAppshotHotkey(hotkey: string, isMac: boolean): string {
  if (!hotkey) return "";
  if (hotkey === "alt+alt") return isMac ? "⌥ + ⌥" : "Alt + Alt";
  return hotkey
    .split("+")
    .map((part) => {
      const key = part.trim();
      if (key === "ctrl") return isMac ? "⌃" : "Ctrl";
      if (key === "alt") return isMac ? "⌥" : "Alt";
      if (key === "shift") return isMac ? "⇧" : "Shift";
      if (key === "right_alt") return isMac ? "⌥" : "AltGr";
      if (key === "win" || key === "cmd" || key === "super") return isMac ? "⌘" : "Win";
      return key.length === 1 ? key.toUpperCase() : key.charAt(0).toUpperCase() + key.slice(1);
    })
    .join(" + ");
}

/**
 * Open the editor on an appshot in its own desktop window. `false` where the
 * shell cannot (a browser, a headless host): then the page shows its editor.
 */
export async function openAppshotEditorWindow(id: string): Promise<boolean> {
  try {
    const response = await fetch("/api/appshot/open-editor", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ id }),
    });
    if (!response.ok) return false;
    const body = (await response.json().catch(() => null)) as { window?: unknown } | null;
    return body?.window === true;
  } catch {
    return false;
  }
}

/** How long the corner card may rest, in seconds; 0 = until closed. */
export const CARD_SECONDS_CHOICES = [3, 6, 10, 30, 60, 300, 0] as const;

/** Choices for "Delete old captures"; 0 = never. */
export const KEEP_NEWEST_CHOICES = [0, 10, 20, 50, 100] as const;

// -- library: the gallery of every kept appshot and edit ----------------------

export type AppshotLibraryVariant = "original" | "edited";

export interface AppshotLibraryItem {
  id: string;
  variant: AppshotLibraryVariant;
  /** Where the picture lies on the machine running the backend. */
  path: string;
  mime: string;
  width: number;
  height: number;
  label: string;
  app_name: string;
  trigger: string;
  taken_at: number;
  /** When the edit was saved; 0 for an original. */
  edited_at: number;
  /** An original that also has an edited version. */
  has_edit: boolean;
  /** Finalized recording duration; absent for screenshots and older backends. */
  duration_s?: number;
}

export function fetchAppshotLibrary(): Promise<{ items: AppshotLibraryItem[]; max_entries: number }> {
  return request("/api/appshot/library");
}

/** The picture's URL; `thumb` asks for the gallery's small JPEG. */
export function appshotLibraryImageUrl(item: AppshotLibraryItem, thumb = false): string {
  const params = new URLSearchParams({
    variant: item.variant,
    v: String(item.edited_at || item.taken_at),
  });
  if (thumb) params.set("thumb", "1");
  return `/api/appshot/library/${encodeURIComponent(item.id)}/image?${params.toString()}`;
}

/** The picture's own file name (`appshot-20261003-114747(-edited).png`), for a drag out. */
export function appshotLibraryFileName(item: AppshotLibraryItem): string {
  return item.path.split(/[\\/]/).pop() || `appshot-${item.id}.png`;
}

/**
 * Hold a kept picture again and open the editor on it. `window: false` means
 * the page has to show its own editor on `id`.
 */
export function openAppshotLibraryItem(
  item: AppshotLibraryItem,
): Promise<{ id: string; window: boolean }> {
  return request(`/api/appshot/library/${encodeURIComponent(item.id)}/open`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ variant: item.variant }),
  });
}

/** An edited tile deletes only the edit; an original deletes the whole appshot. */
export function deleteAppshotLibraryItem(item: AppshotLibraryItem): Promise<{ ok: boolean }> {
  return request(
    `/api/appshot/library/${encodeURIComponent(item.id)}?variant=${encodeURIComponent(item.variant)}`,
    { method: "DELETE" },
  );
}

export function clearAppshotLibrary(): Promise<{ ok: boolean; removed: number }> {
  return request("/api/appshot/library", { method: "DELETE" });
}

export interface AppshotRecording {
  phase: "idle" | "selecting" | "recording" | "stopping" | "saved" | "cancelled" | "error";
  id: string;
  message: string;
  duration_s?: number;
  width?: number;
  height?: number;
  fps?: number;
  bitrate_mbps?: number;
  system_audio?: boolean;
  display_name?: string;
  refresh_hz?: number;
  dropped_frames?: number;
  capability?: {
    available: boolean; detail: string; permission_required: boolean;
    system_audio?: { available: boolean; detail: string };
  };
  recent?: { id: string; created_at: number }[];
}

export function fetchAppshotRecording(signal?: AbortSignal): Promise<AppshotRecording> {
  return request("/api/appshot/recording", { signal, cache: "no-store" });
}

export function controlAppshotRecording(action: "start" | "stop"): Promise<AppshotRecording> {
  return request(`/api/appshot/recording/${action}`, { method: "POST" });
}

export function appshotRecordingUrl(id: string): string {
  return `/api/appshot/recording/${encodeURIComponent(id)}/video`;
}

export function requestRecordingPermission(): Promise<unknown> {
  return request("/api/permissions/screen_recording/request?dry_run=false", {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ feature: "appshot" }),
  });
}

// -- video editor: play, trim and speed up a finished recording ---------------

/** Speeds the video editor offers; mirrored by `SPEEDS` in `jarvis.appshot.video_edit`. */
export const RECORDING_SPEEDS = [0.5, 1, 1.5, 2] as const;

/** Write the trim and speed as a new recording; returns its id (the same id when unchanged). */
export function exportAppshotRecording(
  id: string,
  clip: { start_s: number; end_s: number; speed: number },
): Promise<{ id: string }> {
  return request(`/api/appshot/recording/${encodeURIComponent(id)}/export`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(clip),
  });
}

/** Copy a recording into Downloads on the machine running Jarvis (desktop only). */
export function saveAppshotRecording(id: string): Promise<{ path: string; filename: string }> {
  return request(`/api/appshot/recording/${encodeURIComponent(id)}/save`, { method: "POST" });
}

/** Open the video editor window on a recording; `false` where no window can open. */
export async function openAppshotRecordingEditor(id: string): Promise<boolean> {
  try {
    const body = await request<{ window?: unknown }>(
      `/api/appshot/recording/${encodeURIComponent(id)}/open-editor`,
      { method: "POST" },
    );
    return body?.window === true;
  } catch {
    return false;
  }
}
