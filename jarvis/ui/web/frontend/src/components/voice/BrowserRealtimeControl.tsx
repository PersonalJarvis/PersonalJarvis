import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Loader2, Mic, MicOff, RotateCcw } from "lucide-react";

import { VoiceWaveform, type WaveformPhase } from "@/components/overlay/VoiceWaveform";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useCapabilities } from "@/hooks/useCapabilities";
import { useVoiceMode } from "@/hooks/useVoiceMode";
import { useT } from "@/i18n";
import {
  registerBrowserVoiceCallOwner,
  setBrowserVoiceCallLive,
} from "@/lib/browserVoiceCall";
import { hasEmbeddedDesktopBridge, isEmbeddedMacWindow } from "@/lib/embeddedDesktop";
import {
  browserRealtimeSupportIssue,
  RealtimeAudioClient,
  RealtimeAudioSupportError,
  type BrowserRealtimeSupportIssue,
} from "@/lib/realtimeAudio";
import { useEventStore, type VoiceState } from "@/store/events";
import { setReloadHold } from "@/lib/reloadHold";
import { cn } from "@/lib/utils";
import {
  clearVoiceInputLevel,
  setBrowserVoiceInputOwnership,
  setVoiceInputLevel,
} from "@/lib/voiceInputLevel";
import {
  clearVoiceOutputLevel,
  setBrowserVoiceOutputOwnership,
  setBrowserPlaybackActive,
  setVoiceOutputLevel,
} from "@/lib/voiceOutputLevel";

type ConnectionState = "idle" | "connecting" | "connected" | "error";

// Defined in lib/embeddedDesktop.ts (a module with no UI imports, so the
// permission toast can ask it cheaply); re-exported here because this is where
// the rest of the shell has always imported it from.
export { hasEmbeddedDesktopBridge };

/** Map the socket state plus the shared voice state onto one visualizer look.
 *
 * Kept as a pure function so the mapping is testable and lives in exactly one
 * place: which look the pill shows is a claim about what the microphone and
 * the session are doing, and a second copy of that logic would eventually
 * claim something different from this one. */
export function waveformPhase(
  state: ConnectionState,
  voiceState: VoiceState,
): WaveformPhase {
  if (state === "error") return "error";
  if (state === "connecting") return "connecting";
  if (state !== "connected") return "idle";
  if (voiceState === "error") return "error";
  // The turn was committed: the transcription and then the reply are in
  // flight, and the microphone feed has stopped — so there is nothing left to
  // measure and the pill switches from the waveform to the activity sweep.
  if (voiceState === "thinking") return "working";
  if (voiceState === "speaking") return "speaking";
  return "listening";
}

/**
 * Tell the backend that the person pressed Start and macOS refused the
 * microphone, so it opens a user-origin episode and the permission toast is
 * shown. Best effort: the sentence under the button already says what happened,
 * so a failure here is logged and nothing else.
 */
async function reportHostMicrophoneDenied(): Promise<void> {
  try {
    const res = await fetch("/api/permissions/microphone/request?dry_run=false", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ feature: "browser_voice" }),
    });
    if (!res.ok) console.warn(`Microphone permission report failed: HTTP ${res.status}`);
  } catch (error) {
    console.warn("Microphone permission report failed:", error);
  }
}

/** Browser-owned microphone control for remote/headless installations.
 *
 * The desktop shell already owns the physical microphone through
 * SpeechPipeline, so this control is rendered only when the capability route
 * says native desktop actions are unavailable. That prevents two concurrent
 * capture streams while still making a headless VPS usable entirely in-app.
 *
 * Two ways a call starts here. The desktop hands a realtime call to this
 * document (`BrowserVoiceRequested`). Or, on a host with no speech pipeline
 * at all, the person presses Start and useVoiceCall asks this control to hold
 * the call itself (lib/browserVoiceCall) — in either voice mode, because the
 * server picks a realtime session or the classic STT -> brain -> TTS chain.
 */
export function BrowserRealtimeControl({ controlOnly = false }: { controlOnly?: boolean } = {}) {
  const t = useT();
  const capabilities = useCapabilities();
  const { mode, realtimeAvailable, requiresWebRtcOffer, webRtcStartEventRequired, startBudgetMs, browserAudio } =
    useVoiceMode();
  const setVoice = useEventStore((store) => store.setVoice);
  const setTranscription = useEventStore((store) => store.setTranscription);
  const voiceState = useEventStore((store) => store.voiceState);
  const transcriptionFinal = useEventStore((store) => store.transcriptionFinal);
  const transcription = useEventStore((store) => store.transcription);
  const pushToast = useEventStore((store) => store.pushToast);
  const [state, setState] = useState<ConnectionState>("idle");
  const [effectiveProvider, setEffectiveProvider] = useState("");
  const [error, setError] = useState("");
  // A non-fatal note from the provider (a recoverable warning, an unusable
  // WebRTC answer). Distinct from `error`, which means the call is over.
  const [notice, setNotice] = useState("");
  // The last surface-spoken reply, kept so a browser that cannot synthesise
  // speech still SHOWS the answer instead of swallowing the whole turn.
  const [spokenText, setSpokenText] = useState("");
  // The microphone level lands in a ref, not in state: it arrives ~30 times a
  // second and only the animation loop consumes it. Routing it through
  // setState re-rendered this control (and everything it renders) at 30 Hz to
  // repaint a five-segment meter.
  const levelRef = useRef(0);
  const clientRef = useRef<RealtimeAudioClient | null>(null);
  const events = useEventStore((store) => store.events);
  const solo = useEventStore((store) => store.solo);
  const activeSection = useEventStore((store) => store.activeSection);
  const detachedViews = useEventStore((store) => store.detachedViews);
  const embedded = hasEmbeddedDesktopBridge();
  // Match the desktop's media owner across main and detached windows. An
  // external tab must never compete with the desktop for a wake request.
  const wakeOwner = controlOnly && (embedded
    ? (solo ? activeSection === "chats" : !detachedViews.includes("chats"))
    : capabilities.data?.native_file_actions === false);
  const canStartInBackground = embedded && wakeOwner;
  const handledRequest = useRef<string | null>(null);
  // A wake that lands while the tab is hidden must not be consumed: the
  // desktop is already waiting for this call, and dropping the request
  // leaves it waiting out the full handshake budget for nothing.
  const pendingStart = useRef<{ id: string; ts: number } | null>(null);
  const connectionGenerationRef = useRef(0);
  // A progress/preamble surface line is not the end of the turn. After the
  // browser finishes speaking it, tts_end must restore thinking — not
  // listening — or the pill looks ready while the Tool Model is still
  // working.
  const resumeThinkingAfterSpeechRef = useRef(false);
  // A call the person started here because the host has no speech pipeline.
  // State for rendering, a ref for the callbacks that outlive a render.
  const [localCall, setLocalCall] = useState(false);
  const localCallRef = useRef(false);
  const browserSurface = Boolean(
    capabilities.data &&
      (browserAudio || capabilities.data.native_file_actions === false || !hasEmbeddedDesktopBridge()),
  );
  const visible = browserSurface && mode === "realtime";
  // A locally started call is live in either voice mode; leaving realtime
  // mode must not hang it up.
  const callSurface = visible || localCall;
  const supportIssue = visible ? browserRealtimeSupportIssue() : null;

  const supportMessage = useCallback(
    (issue: BrowserRealtimeSupportIssue) =>
      t(
        issue === "secure_context"
          ? "sidebar.realtime_https_required"
          : issue === "microphone_unavailable"
            ? "sidebar.realtime_microphone_unavailable"
            : "sidebar.realtime_audio_worklet_unavailable",
      ),
    [t],
  );

  const stop = useCallback(async () => {
    connectionGenerationRef.current += 1;
    const client = clientRef.current;
    clientRef.current = null;
    if (localCallRef.current) {
      localCallRef.current = false;
      setLocalCall(false);
      setBrowserVoiceCallLive(false);
    }
    setState("idle");
    setEffectiveProvider("");
    setError("");
    setNotice("");
    setSpokenText("");
    levelRef.current = 0;
    clearVoiceInputLevel("browser");
    clearVoiceOutputLevel("browser");
    setBrowserVoiceOutputOwnership(false);
    setBrowserVoiceInputOwnership(false);
    setVoice("idle");
    await client?.disconnect();
  }, [setVoice]);

  const start = useCallback(async (options?: { fromGesture?: boolean; local?: boolean }) => {
    // A local start does not wait for realtime: on a host with no speech
    // pipeline the server answers with a realtime session or the classic
    // chain, whichever it can build.
    const local = options?.local === true;
    if ((!local && !realtimeAvailable) || clientRef.current || state === "connecting") return;
    const generation = connectionGenerationRef.current + 1;
    connectionGenerationRef.current = generation;
    if (local) {
      localCallRef.current = true;
      setLocalCall(true);
      setBrowserVoiceCallLive(true);
      // Every Start/Stop surface reads this; it also keeps a second press
      // from starting a second call while the microphone opens.
      setVoice("connecting");
    }
    setState("connecting");
    setError("");
    setEffectiveProvider("");
    levelRef.current = 0;
    clearVoiceInputLevel("browser");
    clearVoiceOutputLevel("browser");
    // A local call in pipeline mode has no realtime transport, so none of its
    // media options apply: the classic bridge streams plain PCM both ways. A
    // call the desktop hands over keeps exactly the options it always had.
    const realtimeTransport = !local || mode === "realtime";
    const callBrowserAudio = realtimeTransport && browserAudio;
    // A classic call has no realtime provider to test, so its fallback line
    // points at the three providers that chain runs on instead.
    const startFailed = t(
      realtimeTransport ? "sidebar.realtime_error" : "sidebar.browser_voice_error",
    );
    // A call the person started here reports a failure as a toast and frees
    // the controls again; there is no card on the page to hold an error line.
    const endLocalCall = (message: string) => {
      pushToast("error", message);
      void stop();
    };
    let client: RealtimeAudioClient;
    const isCurrent = () =>
      connectionGenerationRef.current === generation && clientRef.current === client;
    client = new RealtimeAudioClient(
      {
        onTranscript: (text, isFinal, role) => {
          if (!isCurrent()) return;
          // Live adapters project all speaker snapshots onto the shared bus.
          // Keeping a second local caption would overwrite the conversation.
          if (callBrowserAudio) return;
          if (role === "user") setTranscription(text, isFinal);
          if (role === "user" && isFinal) setVoice("thinking");
        },
        onAudio: () => {
          if (!isCurrent()) return;
          setError("");
          setVoice("speaking");
        },
        onPlaybackState: (active) => {
          if (isCurrent()) setBrowserPlaybackActive(active);
        },
        onInputLevel: (value) => {
          if (!isCurrent()) return;
          levelRef.current = value;
          setVoiceInputLevel(value, "browser");
        },
        onOutputLevel: (value) => {
          if (!isCurrent()) return;
          if (value === null) clearVoiceOutputLevel("browser");
          else setVoiceOutputLevel(value, "browser");
        },
        onStatus: (status, payload) => {
          if (!isCurrent()) return;
          // The backend authors one precise English sentence per failure —
          // "automatic usage-billed fallback is disabled for this provider",
          // the exact transport error, the socket close reason. Replacing all
          // of them with one generic line sent users to test a credential
          // that was never the problem.
          const backendDetail =
            (typeof payload.error === "string" ? payload.error.trim() : "") ||
            (typeof payload.reason === "string" ? payload.reason.trim() : "");
          if (status === "audio_ready") {
            setState("connected");
            const provider =
              typeof payload.provider === "string" ? payload.provider : "";
            if (provider) setEffectiveProvider(provider);
            setState("connected");
            setNotice("");
            setVoice("listening");
          } else if (status === "mode_fallback") {
            setEffectiveProvider(t("sidebar.realtime_pipeline_fallback"));
          } else if (status === "provider_fallback") {
            // The call just moved to a DIFFERENT provider family — which can
            // mean different billing. Saying nothing here is an AP-22
            // violation: the card would still name the provider that died.
            const from =
              typeof payload.provider === "string" ? payload.provider : "";
            pushToast(
              "warning",
              t("sidebar.realtime_provider_fallback")
                .replace("{0}", from || t("sidebar.realtime_provider_unknown"))
                .replace("{1}", backendDetail),
            );
            setNotice(
              t("sidebar.realtime_provider_fallback_short").replace(
                "{0}",
                from || t("sidebar.realtime_provider_unknown"),
              ),
            );
          } else if (status === "provider_warning") {
            // Recoverable: the session continues. A note, never an error.
            setNotice(backendDetail || t("sidebar.realtime_provider_warning"));
          } else if (status === "webrtc_transport_unavailable") {
            setNotice(t("sidebar.realtime_webrtc_degraded"));
          } else if (status === "error_spoken") {
            // The trusted reply the provider did not speak. Audio starts via
            // onAudio; keep the text so an engine without speech synthesis
            // still shows the answer rather than losing the turn.
            const text = typeof payload.text === "string" ? payload.text : "";
            if (text.trim()) setSpokenText(text.trim());
            setError("");
            const kind =
              typeof payload.spoken_kind === "string" ? payload.spoken_kind : "";
            resumeThinkingAfterSpeechRef.current =
              kind === "progress" || kind === "preamble";
          } else if (status === "hangup") {
            // The session ended through a voice hang-up command or end_call.
            // Release the microphone and return to idle.
            void stop();
          } else if (status === "reconnecting") {
            setState("connecting");
            setVoice("connecting");
          } else if (status === "thinking") {
            setVoice("thinking");
          } else if (status === "speaking" || status === "listening") {
            setVoice(status);
          } else if (status === "tts_start") {
            // GPT-Live emits speaking state explicitly because its WebRTC
            // audio never reaches the binary playback path (no onAudio).
            setVoice("speaking");
          } else if (status === "turn_complete" || status === "tts_end") {
            if (
              status === "tts_end" &&
              resumeThinkingAfterSpeechRef.current
            ) {
              resumeThinkingAfterSpeechRef.current = false;
              setVoice("thinking");
            } else {
              resumeThinkingAfterSpeechRef.current = false;
              setVoice("listening");
            }
          } else if (status === "tts_cancel" || status === "audio_clear") {
            // audio_clear is the live session's barge-in flush: the user
            // interrupted, so the assistant is no longer speaking.
            setVoice("listening");
          } else if (
            status === "tts_browser_unavailable" ||
            status === "tts_browser_error"
          ) {
            setError(t("sidebar.realtime_browser_tts_unavailable"));
            if (local) pushToast("warning", t("sidebar.realtime_browser_tts_unavailable"));
            setVoice("listening");
          } else if (status === "audio_closed") {
            void stop();
          } else if (status === "provider_error" || status === "disconnected") {
            if (local) {
              endLocalCall(backendDetail || startFailed);
              return;
            }
            clientRef.current = null;
            void client.disconnect();
            setState("error");
            setError(backendDetail || t("sidebar.realtime_error"));
            levelRef.current = 0;
            clearVoiceInputLevel("browser");
            setVoice("error");
          }
        },
      },
      {
        requiresWebRtcOffer: realtimeTransport && requiresWebRtcOffer,
        webRtcStartEventRequired,
        startBudgetMs,
        browserAudio: callBrowserAudio,
      },
    );
    clientRef.current = client;
    try {
      await client.connect();
      if (!isCurrent()) return;
      setState("connected");
    } catch (cause) {
      if (!isCurrent()) return;
      clientRef.current = null;
      void client.disconnect();
      clearVoiceInputLevel("browser");
      const micDenied = cause instanceof DOMException && cause.name === "NotAllowedError";
      // In the embedded Mac window a refused microphone is macOS's decision, not a
      // browser site setting: there is no site settings page to point at. The
      // person's own press opens a user-origin episode, so the permission toast
      // ("Open System Settings") takes over; a wake-started call never does.
      const hostDenied = micDenied && isEmbeddedMacWindow();
      if (hostDenied && options?.fromGesture) void reportHostMicrophoneDenied();
      const message =
        cause instanceof RealtimeAudioSupportError
          ? supportMessage(cause.issue)
          : hostDenied
            ? t("sidebar.realtime_microphone_denied_desktop")
            : micDenied
              ? t("sidebar.realtime_microphone_denied")
              : startFailed;
      if (local) {
        endLocalCall(message);
        return;
      }
      setState("error");
      setError(message);
      setVoice("error");
    }
  }, [
    mode,
    pushToast,
    realtimeAvailable,
    requiresWebRtcOffer,
    webRtcStartEventRequired,
    browserAudio,
    setTranscription,
    setVoice,
    startBudgetMs,
    state,
    stop,
    supportMessage,
    t,
  ]);

  // This document's browser-held call: on a host with no speech pipeline,
  // every Start/Stop surface reaches it through useVoiceCall. Only the
  // app-root instance (`controlOnly`) owns it, and the latest start/stop are
  // read through refs so the registration does not churn on every render.
  const startRef = useRef(start);
  startRef.current = start;
  const stopRef = useRef(stop);
  stopRef.current = stop;
  useEffect(() => {
    if (!controlOnly) return undefined;
    return registerBrowserVoiceCallOwner({
      start: () => void startRef.current({ fromGesture: true, local: true }),
      stop: () => void stopRef.current(),
    });
  }, [controlOnly]);

  useEffect(() => {
    if (!browserAudio || !wakeOwner) return;
    const onVisible = () => {
      const pending = pendingStart.current;
      if ((!canStartInBackground && document.visibilityState !== "visible") || !pending || !realtimeAvailable) return;
      if (Date.now() - pending.ts > 45_000) {
        pendingStart.current = null;
        return;
      }
      pendingStart.current = null;
      handledRequest.current = pending.id;
      void start();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [browserAudio, wakeOwner, canStartInBackground, realtimeAvailable, start]);

  useEffect(() => {
    if (!browserAudio || !wakeOwner) return;
    // EventStore prepends events. Reversing picked the oldest request and
    // swallowed every subsequent start/stop until it aged out of the store.
    const event = events.find(e => e.name === "BrowserVoiceRequested");
    if (!event || event.id === handledRequest.current || Date.now() - event.ts > 45_000) return;
    const action = (event.payload as { action?: string })?.action;
    if (action === "start") {
      if (!realtimeAvailable) return;
      if (canStartInBackground || document.visibilityState === "visible") {
        handledRequest.current = event.id;
        pendingStart.current = null;
        void start();
      } else {
        // Parked, not handled: firing when the tab returns keeps a
        // background wake from dying silently on the desktop side.
        pendingStart.current = { id: event.id, ts: event.ts };
      }
      return;
    }
    if (action === "stop") {
      handledRequest.current = event.id;
      pendingStart.current = null;
      void stop();
    }
  }, [events, browserAudio, wakeOwner, canStartInBackground, realtimeAvailable, start, stop]);

  useEffect(() => {
    // This surface owns BOTH directions while it is live: it holds the
    // microphone and it plays the reply, so the backend's own levels for
    // either one would be a second, unsynchronised opinion.
    const owns = callSurface && (state === "connecting" || state === "connected");
    setBrowserVoiceInputOwnership(owns);
    setBrowserVoiceOutputOwnership(owns);
  }, [state, callSurface]);

  useEffect(() => {
    if (!callSurface) void stop();
  }, [stop, callSurface]);

  // This document owns the call: an automatic reload (a rebuilt bundle) would
  // unmount this control and hang up mid-sentence. Hold reloads while it lives.
  const reloadOwner = useId();
  useEffect(() => {
    setReloadHold(reloadOwner, state === "connecting" || state === "connected");
  }, [reloadOwner, state]);
  useEffect(() => () => setReloadHold(reloadOwner, false), [reloadOwner]);

  useEffect(
    () => () => {
      connectionGenerationRef.current += 1;
      const client = clientRef.current;
      clientRef.current = null;
      setBrowserVoiceInputOwnership(false);
      setBrowserVoiceOutputOwnership(false);
      if (client) void client.disconnect();
    },
    [],
  );

  if (visible && controlOnly && state === "error") {
    return (
      <aside role="alert" className="fixed bottom-4 right-4 z-50 max-w-sm rounded-lg border border-border bg-popover p-4 text-popover-foreground shadow-lg">
        <p className="text-sm">{error || t("sidebar.realtime_error")}</p>
        <a className="mt-3 block text-sm underline" href={window.location.origin} target="_blank" rel="noopener noreferrer">
          {t("live.open_browser")}
        </a>
        <Button className="mt-3" variant="outline" onClick={() => void stop()}>{t("common.close")}</Button>
      </aside>
    );
  }
  if (!visible || controlOnly) return null;

  const connected = state === "connected";
  const connecting = state === "connecting";
  const unavailable = !realtimeAvailable || supportIssue !== null;
  const label = supportIssue
    ? t("sidebar.realtime_browser_unavailable")
    : unavailable
      ? t("sidebar.realtime_unavailable")
      : connected
        ? t("sidebar.realtime_stop")
        : state === "error"
          ? t("sidebar.realtime_retry")
          : t("sidebar.realtime_start");
  const Icon = connecting ? Loader2 : connected ? MicOff : state === "error" ? RotateCcw : Mic;
  const phase = waveformPhase(state, voiceState);
  // Name what the pill is doing. The waveform says "something is happening";
  // this says WHICH something, which is the part a screen reader gets too —
  // the visualizer itself is aria-hidden because a scrolling row of bars has
  // nothing to announce.
  const progressKey =
    phase === "working"
      ? "sidebar.realtime_working"
      : phase === "speaking"
        ? "sidebar.realtime_speaking"
        : transcription && !transcriptionFinal
          ? "sidebar.realtime_transcribing"
          : "sidebar.realtime_listening";

  // A failure and a note are different things and used to be painted the
  // same: both ran as near-white 10px lines under the button, so a dead
  // session looked exactly like a provider warning the call survived.
  const faultLine = error || (supportIssue ? supportMessage(supportIssue) : "");

  return (
    // An object on the rail, not a translucent wash of the room behind it:
    // this control is sized to its content, so it is allowed to lift.
    <Card className="mt-2 p-2">
      <Button
        variant={connected ? "secondary" : "default"}
        disabled={unavailable || connecting}
        aria-label={label}
        aria-pressed={connected}
        onClick={() => void (connected ? stop() : start({ fromGesture: true }))}
        className="w-full touch-manipulation gap-2"
      >
        <Icon
          className={cn("h-3.5 w-3.5", connecting && "animate-spin motion-reduce:animate-none")}
          aria-hidden="true"
        />
        <span>{connecting ? t("sidebar.realtime_connecting") : label}</span>
      </Button>
      {(connected || connecting) && (
        <div className="mt-2">
          <VoiceWaveform levelRef={levelRef} phase={phase} />
        </div>
      )}
      <div
        className={cn(
          "mt-2 min-h-4 text-micro",
          faultLine ? "text-destructive" : "text-muted-foreground",
        )}
        aria-live="polite"
      >
        {faultLine ||
          (connected
            ? [t(progressKey), effectiveProvider].filter(Boolean).join(" · ")
            : t("sidebar.realtime_browser_hint"))}
      </div>
      {/* The call survived — a different provider answered, or one warned
          about itself. Degraded, so it wears the degraded hue rather than the
          brightest ink on the rail. */}
      {notice && !error && (
        <div
          data-testid="realtime-provider-notice"
          className="mt-1 text-micro text-warning"
          aria-live="polite"
        >
          {notice}
        </div>
      )}
      {/* The surface-spoken reply, shown only when speech synthesis could not
          deliver it — otherwise the whole turn is silent AND invisible. */}
      {spokenText && error && (
        <div
          data-testid="realtime-spoken-text"
          className="mt-1 text-micro text-foreground"
        >
          {spokenText}
        </div>
      )}
    </Card>
  );
}
