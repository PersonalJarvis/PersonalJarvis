/**
 * "Autopilot toasts" — the "{name} opened X" note that appears when a spoken
 * command drives the sidebar (the `NavigateSidebar` event in useWebSocket).
 *
 * A per-browser preference, not a backend setting: it only decides whether
 * THIS window shows a note about something that already happened, so nothing
 * on the server needs to know. Default ON — the note is the only feedback a
 * user gets that "go to settings" was heard when they are not looking at the
 * sidebar. Storage failures (private mode, blocked site data) fall back to
 * the default silently; a missing preference must never break navigation.
 */
const STORAGE_KEY = "ui.autopilotToasts.v1";
const CHANGE_EVENT = "autopilot-toasts-changed";

export function isAutopilotToastsEnabled(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) !== "off";
  } catch {
    return true;
  }
}

export function setAutopilotToastsEnabled(enabled: boolean): void {
  try {
    if (enabled) window.localStorage.removeItem(STORAGE_KEY);
    else window.localStorage.setItem(STORAGE_KEY, "off");
  } catch {
    /* storage blocked — the switch still reflects the choice for this render */
  }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

/** Subscribe to changes made in another component of this window. */
export function onAutopilotToastsChange(listener: () => void): () => void {
  window.addEventListener(CHANGE_EVENT, listener);
  return () => window.removeEventListener(CHANGE_EVENT, listener);
}
