import type { SectionHealth } from "@/hooks/useProviders";

/**
 * Lets another part of the app open the API Keys page on one tab — the
 * first-run guide uses it to land on the Agents tab for subscriptions.
 *
 * The request is remembered, not just announced: the page loads lazily, so
 * it may mount after the event fired. It stays in force until the user picks
 * a tab themselves or the requester clears it.
 */
export const APIKEYS_TAB_EVENT = "jarvis:apikeys-tab";

/** The API Keys page's tabs, in display order. */
export const API_KEYS_TABS = ["realtime", "subagents", "jarvis-key", "advanced"] as const;
export type ApiKeysTab = (typeof API_KEYS_TABS)[number];

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

/**
 * Whether a section shown on the API Keys page is set up but failing — the
 * red pip the navigation puts on "API Keys".
 *
 * Only the page's own tabs count. A failure in a section the page does not
 * show (the retired pipeline tiers, voice input configured in the voice
 * section) would otherwise light a pip that leads to a page with nothing to
 * fix on it.
 */
export function apiKeysHealthError(sections: Record<string, SectionHealth | undefined>): boolean {
  return API_KEYS_TABS.some((tab) => sections[tab]?.status === "error");
}
