/**
 * Lets another part of the app open the API Keys page on one tab — the
 * first-run guide uses it to land on the Agents tab for subscriptions.
 *
 * The request is remembered, not just announced: the page loads lazily, so
 * it may mount after the event fired. It stays in force until the user picks
 * a tab themselves or the requester clears it, so a later engine-mode sync
 * of the page does not yank it back to the first tab.
 */
export const APIKEYS_TAB_EVENT = "jarvis:apikeys-tab";

let requested: string | null = null;

/** Ask the API Keys page to show `tab`; `null` means its default tab. */
export function requestApiKeysTab(tab: string | null): void {
  requested = tab;
  window.dispatchEvent(new CustomEvent<string | null>(APIKEYS_TAB_EVENT, { detail: tab }));
}

/** The tab another part of the app asked for, if any. */
export function requestedApiKeysTab(): string | null {
  return requested;
}

/** Forget a request without moving the page (the user chose a tab). */
export function clearApiKeysTabRequest(): void {
  requested = null;
}
