import type { SectionHealth } from "@/hooks/useProviders";

/**
 * Lets another part of the app open the API Keys page on one tab, and
 * optionally on one setting of it — a mission waiting for capacity links to
 * the paid-fallback switch on the Agents tab this way.
 *
 * The request is remembered, not just announced: the page loads lazily, so
 * it may mount after the event fired. The page takes the tab when it mounts
 * (or right away when it is already open); the setting takes its anchor once
 * it has rendered.
 */
export const APIKEYS_TAB_EVENT = "jarvis:apikeys-tab";

/** The API Keys page's tabs, in display order. */
export const API_KEYS_TABS = ["realtime", "subagents", "jarvis-key", "advanced"] as const;
export type ApiKeysTab = (typeof API_KEYS_TABS)[number];

/** The paid-fallback setting on the Agents tab (views/apikeys/MissionBilling). */
export const MISSION_BILLING_ANCHOR = "mission-billing";

let requested: string | null = null;
let requestedAnchor: string | null = null;

/**
 * Ask the API Keys page to show `tab` (`null` means its default tab), and
 * optionally to bring the setting whose element id is `anchor` into view.
 */
export function requestApiKeysTab(tab: string | null, anchor: string | null = null): void {
  requested = tab;
  requestedAnchor = anchor;
  window.dispatchEvent(new CustomEvent<string | null>(APIKEYS_TAB_EVENT, { detail: tab }));
}

/** True once for the setting `anchor` names, if it was asked for; clears it. */
export function takeApiKeysAnchor(anchor: string): boolean {
  if (requestedAnchor !== anchor) return false;
  requestedAnchor = null;
  return true;
}

/** The tab another part of the app asked for, if any. */
export function requestedApiKeysTab(): string | null {
  return requested;
}

/** The tab another part of the app asked for, if any; the page takes it once. */
export function takeRequestedApiKeysTab(): string | null {
  const tab = requested;
  requested = null;
  return tab;
}

/** Forget a request without moving the page (the user chose a tab). */
export function clearApiKeysTabRequest(): void {
  requested = null;
  requestedAnchor = null;
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
