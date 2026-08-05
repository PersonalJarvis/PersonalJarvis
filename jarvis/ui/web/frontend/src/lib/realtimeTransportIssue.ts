/**
 * Why the embedded desktop cannot supply the WebRTC offer a subscription
 * realtime transport needs.
 *
 * The backend reports the SERVER side of this ("no offer is registered", via
 * `transport_offer_detail`). It cannot report the client side, because a broker
 * that never reached the socket produced nothing for the backend to observe —
 * and that failure used to be a bare `return` with no log, no retry and no
 * surface, so the call simply hung with the UI claiming nothing was wrong.
 *
 * A closed union rather than free text: every member maps to one
 * `sidebar.realtime_transport_*` key in en/de/es, pinned by
 * `realtime-transport-issue-parity.test.ts`.
 */
export type RealtimeTransportIssue =
  /** The desktop process capability never arrived — the broker cannot authenticate. */
  | "capability_missing"
  /** This engine has no usable RTCPeerConnection, so no offer can be produced. */
  | "offer_unsupported"
  /** The broker socket kept failing; retries are exhausted. */
  | "socket_unreachable";

export const REALTIME_TRANSPORT_ISSUES: readonly RealtimeTransportIssue[] = [
  "capability_missing",
  "offer_unsupported",
  "socket_unreachable",
];

/** i18n key for one issue. Keep in step with the locale files. */
export function realtimeTransportIssueKey(issue: RealtimeTransportIssue): string {
  return `sidebar.realtime_transport_${issue}`;
}

/** Window event carrying the broker's current issue (`null` = healthy). */
export const REALTIME_TRANSPORT_ISSUE_EVENT = "jarvis:realtime-transport-issue";

/**
 * Asks any mounted broker to publish an offer NOW, before a switch reconnects
 * the live call.
 *
 * A provider switch tears the open call down and reopens it synchronously, but
 * the client only learns that the new transport needs an offer after a status
 * round trip — so the reopened session used to start with nothing registered.
 * Dispatched for EVERY realtime switch, never keyed on a provider name (AP-21):
 * a transport that does not need the offer simply lets it expire.
 */
export const REALTIME_TRANSPORT_PREPARE_EVENT = "jarvis:realtime-transport-prepare";

/** How long one prepare request keeps the broker mounted. */
export const REALTIME_TRANSPORT_PREPARE_TTL_MS = 60_000;

export interface RealtimeTransportIssueDetail {
  issue: RealtimeTransportIssue | null;
}

/** True when this host can fan an event out to listeners at all.
 *
 * Both helpers below are pure notifications: a host without an event target
 * (SSR, a worker, a test double that stubs only `window.location`) simply has
 * nobody to tell, and must not take the caller down for it. */
function canDispatch(): boolean {
  return (
    typeof window !== "undefined" && typeof window.dispatchEvent === "function"
  );
}

/** Announce the broker's health to whichever status surface is listening. */
export function publishRealtimeTransportIssue(
  issue: RealtimeTransportIssue | null,
): void {
  if (!canDispatch()) return;
  window.dispatchEvent(
    new CustomEvent<RealtimeTransportIssueDetail>(REALTIME_TRANSPORT_ISSUE_EVENT, {
      detail: { issue },
    }),
  );
}

/** Ask a mounted broker to publish a fresh offer before a switch lands. */
export function requestRealtimeTransportOffer(): void {
  if (!canDispatch()) return;
  window.dispatchEvent(new Event(REALTIME_TRANSPORT_PREPARE_EVENT));
}
