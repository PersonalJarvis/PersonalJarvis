import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Mic, MicOff, RotateCcw } from "lucide-react";

import { VoiceWaveform, type WaveformPhase } from "@/components/overlay/VoiceWaveform";
import { useCapabilities } from "@/hooks/useCapabilities";
import { useVoiceMode } from "@/hooks/useVoiceMode";
import { useT } from "@/i18n";
import {
  browserRealtimeSupportIssue,
  RealtimeAudioClient,
  RealtimeAudioSupportError,
  type BrowserRealtimeSupportIssue,
} from "@/lib/realtimeAudio";
import { useEventStore, type VoiceState } from "@/store/events";
import { cn } from "@/lib/utils";
import {
  clearVoiceInputLevel,
  setBrowserVoiceInputOwnership,
  setVoiceInputLevel,
} from "@/lib/voiceInputLevel";

type ConnectionState = "idle" | "connecting" | "connected" | "error";

/** True only inside a pywebview host, never from the backend's machine flag.
 *
 * A normal Chrome window can connect to the same local desktop backend, where
 * `native_file_actions` is also true. Checking the client bridge keeps browser
 * microphone control visible there without enabling a second microphone in the
 * embedded desktop window.
 */
export function hasEmbeddedDesktopBridge(): boolean {
  const host = window as unknown as {
    __JARVIS_EMBEDDED_DESKTOP?: boolean;
    pywebview?: { api?: unknown };
    chrome?: { webview?: { postMessage?: unknown } };
    webkit?: { messageHandlers?: Record<string, unknown> };
  };
  return Boolean(
    host.__JARVIS_EMBEDDED_DESKTOP ||
      host.pywebview?.api ||
      typeof host.chrome?.webview?.postMessage === "function" ||
      host.webkit?.messageHandlers?.jarvisFileDrag,
  );
}

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

/** Browser-owned microphone control for remote/headless installations.
 *
 * The desktop shell already owns the physical microphone through
 * SpeechPipeline, so this control is rendered only when the capability route
 * says native desktop actions are unavailable. That prevents two concurrent
 * capture streams while still making a headless VPS usable entirely in-app.
 */
export function BrowserRealtimeControl() {
  const t = useT();
  const capabilities = useCapabilities();
  const { mode, realtimeAvailable, requiresWebRtcOffer, startBudgetMs } =
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
  const connectionGenerationRef = useRef(0);
  const browserSurface = Boolean(
    capabilities.data &&
      (capabilities.data.native_file_actions === false || !hasEmbeddedDesktopBridge()),
  );
  const visible = browserSurface && mode === "realtime";
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
    setState("idle");
    setEffectiveProvider("");
    setError("");
    setNotice("");
    setSpokenText("");
    levelRef.current = 0;
    clearVoiceInputLevel("browser");
    setBrowserVoiceInputOwnership(false);
    setVoice("idle");
    await client?.disconnect();
  }, [setVoice]);

  const start = useCallback(async () => {
    if (!realtimeAvailable || state === "connecting") return;
    const generation = connectionGenerationRef.current + 1;
    connectionGenerationRef.current = generation;
    const previousClient = clientRef.current;
    clientRef.current = null;
    setState("connecting");
    setError("");
    setEffectiveProvider("");
    levelRef.current = 0;
    clearVoiceInputLevel("browser");
    await previousClient?.disconnect();
    if (connectionGenerationRef.current !== generation) return;

    let client: RealtimeAudioClient;
    const isCurrent = () =>
      connectionGenerationRef.current === generation && clientRef.current === client;
    client = new RealtimeAudioClient(
      {
        onTranscript: (text, isFinal, role) => {
          if (!isCurrent()) return;
          if (role === "user") setTranscription(text, isFinal);
          if (role === "user" && isFinal) setVoice("thinking");
        },
        onAudio: () => {
          if (!isCurrent()) return;
          setError("");
          setVoice("speaking");
        },
        onInputLevel: (value) => {
          if (!isCurrent()) return;
          levelRef.current = value;
          setVoiceInputLevel(value, "browser");
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
          } else if (status === "hangup") {
            // The session ended through a voice hang-up command or end_call.
            // Release the microphone and return to idle.
            void stop();
          } else if (status === "thinking") {
            setVoice("thinking");
          } else if (status === "turn_complete" || status === "tts_end") {
            setVoice("listening");
          } else if (status === "tts_cancel") {
            setVoice("listening");
          } else if (
            status === "tts_browser_unavailable" ||
            status === "tts_browser_error"
          ) {
            setError(t("sidebar.realtime_browser_tts_unavailable"));
            setVoice("listening");
          } else if (status === "provider_error" || status === "disconnected") {
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
      { requiresWebRtcOffer, startBudgetMs },
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
      setState("error");
      setError(
        cause instanceof RealtimeAudioSupportError
          ? supportMessage(cause.issue)
          : cause instanceof DOMException && cause.name === "NotAllowedError"
            ? t("sidebar.realtime_microphone_denied")
            : t("sidebar.realtime_error"),
      );
      setVoice("error");
    }
  }, [
    pushToast,
    realtimeAvailable,
    requiresWebRtcOffer,
    setTranscription,
    setVoice,
    startBudgetMs,
    state,
    stop,
    supportMessage,
    t,
  ]);

  useEffect(() => {
    setBrowserVoiceInputOwnership(
      visible && (state === "connecting" || state === "connected"),
    );
  }, [state, visible]);

  useEffect(() => {
    if (!visible) void stop();
  }, [stop, visible]);

  useEffect(
    () => () => {
      connectionGenerationRef.current += 1;
      const client = clientRef.current;
      clientRef.current = null;
      setBrowserVoiceInputOwnership(false);
      if (client) void client.disconnect();
    },
    [],
  );

  if (!visible) return null;

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

  return (
    <div className="mt-2 rounded-md border border-border/70 bg-background/50 p-2">
      <button
        type="button"
        disabled={unavailable || connecting}
        aria-label={label}
        aria-pressed={connected}
        onClick={() => void (connected ? stop() : start())}
        className={cn(
          "flex min-h-9 w-full touch-manipulation items-center justify-center gap-2 rounded-md px-2",
          "text-xs font-medium transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
          connected
            ? "bg-primary text-primary-foreground hover:bg-primary/90"
            : "border border-border bg-card text-foreground hover:border-primary/50",
          (unavailable || connecting) && "cursor-not-allowed opacity-60",
        )}
      >
        <Icon
          className={cn("h-3.5 w-3.5", connecting && "animate-spin motion-reduce:animate-none")}
          aria-hidden="true"
        />
        <span>{connecting ? t("sidebar.realtime_connecting") : label}</span>
      </button>
      {(connected || connecting) && (
        <div className="mt-1.5">
          <VoiceWaveform levelRef={levelRef} phase={phase} />
        </div>
      )}
      <div className="mt-1.5 min-h-4 text-[10px] text-muted-foreground" aria-live="polite">
        {error ||
          (supportIssue ? supportMessage(supportIssue) : "") ||
          (connected
            ? [t(progressKey), effectiveProvider].filter(Boolean).join(" · ")
            : t("sidebar.realtime_browser_hint"))}
      </div>
      {notice && !error && (
        <div
          data-testid="realtime-provider-notice"
          className="mt-1 text-[10px] leading-snug text-amber-600 dark:text-amber-400"
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
          className="mt-1 text-[10px] leading-snug text-foreground"
        >
          {spokenText}
        </div>
      )}
    </div>
  );
}
