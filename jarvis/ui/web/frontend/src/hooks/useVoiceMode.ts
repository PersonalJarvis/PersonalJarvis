import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import {
  REALTIME_TRANSPORT_ISSUE_EVENT,
  requestRealtimeTransportOffer,
  type RealtimeTransportIssue,
  type RealtimeTransportIssueDetail,
} from "@/lib/realtimeTransportIssue";
import { putVoiceMode, type VoiceEngineModeValue } from "@/lib/voiceEngineMode";
import { useEventStore } from "@/store/events";

type VoiceModeResp = {
  mode: string;
  realtime_available: boolean;
  requires_webrtc_offer: boolean;
  /** Longest handshake any eligible realtime provider declares it needs. */
  handshake_budget_s?: number;
  /**
   * Offer-requiring transports only: whether the embedded desktop currently has
   * a WebRTC offer registered, plus the backend's one-line diagnosis.
   *
   * Only the subscription transport sets `requires_webrtc_offer`, so this pair
   * is the ONLY honest explanation for "the call never starts" on that path.
   * The backend has always emitted it and nothing read it, which is how a call
   * stuck waiting for an offer rendered as "no voice session is active".
   */
  transport_offer_ready?: boolean;
  transport_offer_detail?: string | null;
  active_provider: string | null;
  // Sidebar-footer display fields: pretty provider name + the model an idle
  // realtime session would use (configured pin or catalog default).
  active_provider_label: string | null;
  active_model: string | null;
  active_model_label?: string | null;
  session_active: boolean;
  active_session_mode: "pipeline" | "realtime" | null;
  active_session_provider: string;
  active_session_model: string;
  active_session_model_label?: string | null;
  transitioning: boolean;
  /**
   * Why the LAST realtime start attempt failed ({provider, message, at} or
   * null once a session is up). Rendered when the connecting window closes
   * without a session — before this field those attempts fell back to the
   * "no voice session is active" idle line with no reason shown.
   */
  last_start_error?: {
    provider: string;
    message: string;
    at: number;
  } | null;
};

const REALTIME_DISCOVERY_RETRY_WINDOW_MS = 5 * 60_000;
const REALTIME_DISCOVERY_RETRY_MAX_MS = 10_000;
/** Historical surface budget for one realtime start attempt.
 *
 * Kept as the floor so an older backend (or a failed capability probe) behaves
 * exactly as before. A provider that declares it needs LONGER raises it — the
 * subscription transport declares 45 s and documents 15-25 s cold starts, and
 * the fixed 20 s used to report those legitimate negotiations as timeouts.
 */
const REALTIME_START_BUDGET_FLOOR_MS = 20_000;
/** How often this snapshot is re-read while a realtime start is in flight.
 *
 * A cold subscription transport spends 15-25 s spawning its app-server,
 * verifying the account and negotiating WebRTC, and NOTHING is published during
 * that window. Without this poll every status surface sat frozen on its last
 * value, so a call that was legitimately negotiating rendered as "no voice
 * session is active" for half a minute.
 */
const REALTIME_START_POLL_MS = 750;
/** Grace beyond the declared handshake budget before the poll gives up. */
const REALTIME_START_PENDING_SLACK_MS = 5_000;

/** Bus events that change the RUNTIME half of this snapshot.
 *
 * `RealtimeSessionStarting` is the start-of-handshake signal (emitter owned by
 * the realtime session): it makes the connecting state exact instead of
 * inferred. The polling below deliberately does not depend on it, so this
 * surface is already correct against a backend that does not publish it.
 */
const REALTIME_RUNTIME_EVENTS: ReadonlySet<string> = new Set([
  "VoiceSessionStarted",
  "RealtimeSessionStarting",
  "RealtimeSessionReady",
  "VoiceSessionEnded",
]);

/** How far into the (newest-first) event log the runtime-event probe looks.
 *
 * The log holds 500 entries and this scan ran on EVERY websocket frame. A
 * freshly pushed event sits at index 0, so a shallow bounded scan still sees
 * every arrival, including a small batch delivered in one render.
 */
const RUNTIME_EVENT_SCAN_DEPTH = 8;

export function useVoiceMode() {
  const qc = useQueryClient();
  const events = useEventStore((state) => state.events);
  const voiceState = useEventStore((state) => state.voiceState);
  const realtimeDiscoveryStartedAt = useRef<number | null>(null);
  // Attempts inside the CURRENT discovery window. The query-lifetime
  // `dataUpdateCount` grows with every unrelated refetch, so after a dozen of
  // them a FRESH window's first re-check already waited the full cap — the
  // stale-counter trap (AP-19).
  const realtimeDiscoveryAttempts = useRef(0);
  // Wall-clock deadline until which a realtime start is believed in flight.
  // Held in STATE, not only a ref: `refetchInterval` stops polling the moment
  // the window closes, so a ref alone would leave the last render showing
  // "connecting" with nothing left to re-render it — a stuck indicator instead
  // of a stuck call, which is barely an improvement.
  const [startPendingUntil, setStartPendingUntil] = useState(0);
  const startPendingUntilRef = useRef(0);
  startPendingUntilRef.current = startPendingUntil;
  // The CLIENT side of "no WebRTC offer is available". The backend can only
  // report that none is registered; it cannot see a broker that never reached
  // the socket, which is exactly the failure that used to be invisible.
  const [transportIssue, setTransportIssue] =
    useState<RealtimeTransportIssue | null>(null);
  const q = useQuery<VoiceModeResp>({
    queryKey: ["voice-mode"],
    queryFn: async ({ signal }) =>
      (
        await fetch("/api/settings/voice-mode", {
          cache: "no-store",
          signal,
        })
      ).json(),
    refetchInterval: (query) => {
      const data = query.state.data;
      const now = Date.now();

      // 1. A realtime START is in flight — either the backend says so
      //    (`transitioning`), or this client asked for one / saw a voice
      //    session open that this snapshot does not know about yet. The
      //    accepted-session case is cleared by an effect below, so this stays
      //    a pure read.
      if (data?.transitioning === true || now < startPendingUntilRef.current) {
        return REALTIME_START_POLL_MS;
      }

      // 2. Provider discovery is still pending (cold boot, login in flight).
      const discoveryPending =
        data?.mode === "realtime" && data.realtime_available === false;
      if (!discoveryPending) {
        realtimeDiscoveryStartedAt.current = null;
        realtimeDiscoveryAttempts.current = 0;
        return false;
      }
      realtimeDiscoveryStartedAt.current ??= now;
      if (
        now - realtimeDiscoveryStartedAt.current >=
        REALTIME_DISCOVERY_RETRY_WINDOW_MS
      ) {
        return false;
      }
      // Codex discovery can briefly report unavailable while its isolated
      // login/runtime probe is still in flight during cold boot. Retry quickly
      // once, then back off while a browser OAuth flow is still open.
      const exponent = realtimeDiscoveryAttempts.current;
      realtimeDiscoveryAttempts.current += 1;
      return Math.min(1_000 * 2 ** exponent, REALTIME_DISCOVERY_RETRY_MAX_MS);
    },
    refetchIntervalInBackground: false,
  });

  /** Arm the connecting poll for one realtime start attempt. */
  const markRealtimeStartPending = useCallback(() => {
    const cached = qc.getQueryData<VoiceModeResp>(["voice-mode"]);
    const budgetMs = Math.max(
      REALTIME_START_BUDGET_FLOOR_MS,
      Math.round((cached?.handshake_budget_s ?? 0) * 1000),
    );
    setStartPendingUntil(Date.now() + budgetMs + REALTIME_START_PENDING_SLACK_MS);
    // Kick the query so react-query re-evaluates `refetchInterval` now instead
    // of whenever the previous schedule happened to land.
    void qc.invalidateQueries({ queryKey: ["voice-mode"] });
  }, [qc]);

  const m = useMutation({
    mutationFn: async (mode: string) =>
      putVoiceMode(mode as VoiceEngineModeValue),
    // Optimistic: flip the cached mode BEFORE the PUT resolves, so the
    // Pipeline|Realtime segment's filled "live" state follows the click
    // instantly (the persist can take seconds on a busy backend, and the UI
    // used to only update after PUT + a full refetch — the switch felt dead).
    // A failed PUT rolls back to the previous server truth.
    onMutate: async (mode: string) => {
      await qc.cancelQueries({ queryKey: ["voice-mode"] });
      const prev = qc.getQueryData<VoiceModeResp>(["voice-mode"]);
      if (prev) qc.setQueryData<VoiceModeResp>(["voice-mode"], { ...prev, mode });
      // Selecting realtime restarts an OPEN call through the full provider
      // handshake. When idle, this only changes the engine preference: arming
      // the provider budget there would fabricate a 20-140 s "connecting"
      // state for a call that does not exist.
      if (mode === "realtime") {
        requestRealtimeTransportOffer();
        const callIsOpen =
          prev?.session_active === true ||
          prev?.transitioning === true ||
          voiceState !== "idle";
        if (callIsOpen) markRealtimeStartPending();
        else setStartPendingUntil(0);
      }
      return { prev };
    },
    onError: (_err, _mode, ctx) => {
      if (ctx?.prev) qc.setQueryData(["voice-mode"], ctx.prev);
      setStartPendingUntil(0);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["voice-mode"] }),
  });

  // A realtime provider card changes the provider, never `[voice].mode`.
  // Refresh because this response also carries the selected provider/model
  // shown in the runtime status; the engine mode itself remains user-controlled.
  // A switch also reconnects an OPEN call, so the connecting poll is armed only
  // when runtime state proves there is one. The event is also emitted for idle
  // provider selection and subscription login/logout; those actions refresh
  // availability but must never impersonate a voice-session handshake.
  // The same event is dispatched when a subscription login connects or
  // disconnects, which is what makes `realtime_available` flip without waiting
  // out the discovery backoff.
  useEffect(() => {
    function onRealtimeSwitched() {
      const cached = qc.getQueryData<VoiceModeResp>(["voice-mode"]);
      const callIsOpen =
        cached?.session_active === true ||
        cached?.transitioning === true ||
        voiceState !== "idle";
      if (callIsOpen) {
        markRealtimeStartPending();
        return;
      }
      setStartPendingUntil(0);
      void qc.invalidateQueries({ queryKey: ["voice-mode"] });
    }
    window.addEventListener("jarvis:realtime-switched", onRealtimeSwitched);
    return () =>
      window.removeEventListener("jarvis:realtime-switched", onRealtimeSwitched);
  }, [markRealtimeStartPending, qc, voiceState]);

  useEffect(() => {
    function onTransportIssue(event: Event) {
      const detail = (event as CustomEvent<RealtimeTransportIssueDetail>).detail;
      setTransportIssue(detail?.issue ?? null);
    }
    window.addEventListener(REALTIME_TRANSPORT_ISSUE_EVENT, onTransportIssue);
    return () =>
      window.removeEventListener(REALTIME_TRANSPORT_ISSUE_EVENT, onTransportIssue);
  }, []);

  // Configured mode and effective in-flight mode are distinct. Refresh the
  // runtime snapshot on voice boundaries and on the accepted realtime
  // handshake so the switch never labels an old classic call as Realtime.
  const lastRuntimeEvent = useMemo(() => {
    const depth = Math.min(events.length, RUNTIME_EVENT_SCAN_DEPTH);
    for (let index = 0; index < depth; index++) {
      if (REALTIME_RUNTIME_EVENTS.has(events[index].name)) return events[index];
    }
    return null;
  }, [events]);
  useEffect(() => {
    if (lastRuntimeEvent === null) return;
    if (lastRuntimeEvent.name === "RealtimeSessionStarting") {
      // Start of the handshake: arm the poll (which also refetches).
      markRealtimeStartPending();
      return;
    }
    qc.invalidateQueries({ queryKey: ["voice-mode"] });
  }, [lastRuntimeEvent, markRealtimeStartPending, qc]);

  const mode = q.data?.mode ?? "pipeline";
  const sessionActive = q.data?.session_active ?? false;

  // A voice session is audibly running (the pipeline left IDLE) while this
  // snapshot still reports no realtime session: that gap IS the handshake.
  // Derived from state the client already has, so the connecting indicator is
  // never blocked on a backend event landing first.
  useEffect(() => {
    if (mode !== "realtime" || voiceState === "idle" || sessionActive) return;
    markRealtimeStartPending();
  }, [markRealtimeStartPending, mode, sessionActive, voiceState]);

  const transitioning = q.data?.transitioning ?? false;
  const startPending = startPendingUntil > 0;

  // The handshake landed — drop the window instead of waiting it out.
  useEffect(() => {
    if (!startPending) return;
    if (sessionActive && q.data?.active_session_mode === "realtime" && !transitioning) {
      setStartPendingUntil(0);
    }
  }, [q.data?.active_session_mode, sessionActive, startPending, transitioning]);

  // …and if it never lands, close the window on a timer so the indicator
  // cannot outlive the budget it was given.
  useEffect(() => {
    if (startPendingUntil === 0) return;
    const remaining = startPendingUntil - Date.now();
    if (remaining <= 0) {
      setStartPendingUntil(0);
      return;
    }
    const timer = window.setTimeout(() => setStartPendingUntil(0), remaining);
    return () => window.clearTimeout(timer);
  }, [startPendingUntil]);

  return {
    mode,
    realtimeAvailable: q.data?.realtime_available ?? false,
    requiresWebRtcOffer: q.data?.requires_webrtc_offer ?? false,
    startBudgetMs: Math.max(
      REALTIME_START_BUDGET_FLOOR_MS,
      Math.round((q.data?.handshake_budget_s ?? 0) * 1000),
    ),
    /** A realtime call is negotiating right now — neither idle nor running. */
    connecting: (transitioning || startPending) && !sessionActive,
    /** Offer-requiring transports: is a browser offer registered right now?
     *  A client-side broker failure counts as "no", because it IS the reason. */
    transportOfferReady:
      transportIssue !== null ? false : q.data?.transport_offer_ready ?? null,
    /** Backend one-liner naming WHY no offer is available. Rendered verbatim. */
    transportOfferDetail: (q.data?.transport_offer_detail ?? "") || "",
    /** Client-side blocker, translated by the renderer. `null` = the broker is
     *  fine and the backend detail above is the whole story. */
    transportIssue,
    // Distinguishes "the server SAID no realtime key" from "we never heard
    // back" (timeout/loading). Without it a failed status fetch showed the
    // false claim "Realtime needs provider setup" and looked like a locked
    // toggle on a machine that merely had a slow/broken backend moment.
    statusKnown: q.isSuccess,
    activeProvider: q.data?.active_provider ?? null,
    activeProviderLabel: q.data?.active_provider_label ?? null,
    activeModel: q.data?.active_model_label ?? q.data?.active_model ?? null,
    sessionActive,
    activeSessionMode: q.data?.active_session_mode ?? null,
    activeSessionProvider: q.data?.active_session_provider ?? "",
    activeSessionModel:
      q.data?.active_session_model_label ?? q.data?.active_session_model ?? "",
    transitioning,
    /** Failure detail of the last start attempt; null while connecting or live. */
    lastStartError:
      !sessionActive && !(transitioning || startPending)
        ? q.data?.last_start_error ?? null
        : null,
    setMode: m.mutate,
    isLoading: q.isLoading,
    isSaving: m.isPending,
  };
}
