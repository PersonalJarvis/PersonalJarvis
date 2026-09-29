/**
 * Client for `/api/appshot` — the front window as conversation context.
 *
 * Pixels only ever come from the two image endpoints, which the backend serves
 * with `Cache-Control: no-store`; nothing here keeps a copy.
 */

export type AppshotTarget = "auto" | "message" | "voice";

export interface AppshotShortcutStatus {
  hotkey: string;
  armed: boolean;
  detail: string;
}

export interface AppshotSettings {
  enabled: boolean;
  hotkey: string;
  target: AppshotTarget;
  sound: boolean;
  effect: boolean;
  sound_effects_master: boolean;
  shortcut: AppshotShortcutStatus;
  readiness: {
    capture: boolean;
    capture_detail: string;
    effect: boolean;
    effect_detail: string;
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
  Pick<AppshotSettings, "enabled" | "hotkey" | "target" | "sound" | "effect">
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

export function latestAppshotImageUrl(id: string): string {
  // The id only busts the <img> cache between appshots; the server keeps one.
  return `/api/appshot/latest/image?v=${encodeURIComponent(id)}`;
}

export function takeAppshot(delaySeconds = 0): Promise<
  { ok: true; appshot: AppshotMeta } | { ok: false; reason: string; message: string }
> {
  return request("/api/appshot/take", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ delay_s: delaySeconds }),
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

/** "alt+alt" → "Alt + Alt" (⌥ on a Mac); any other combo, title-cased. */
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
      if (key === "win" || key === "cmd" || key === "super") return isMac ? "⌘" : "Win";
      return key.length === 1 ? key.toUpperCase() : key.charAt(0).toUpperCase() + key.slice(1);
    })
    .join(" + ");
}
