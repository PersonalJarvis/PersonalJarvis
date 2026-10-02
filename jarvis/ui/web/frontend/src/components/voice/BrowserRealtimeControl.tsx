import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Mic, MicOff, RotateCcw } from "lucide-react";

import { VoiceWaveform, type WaveformPhase } from "@/components/overlay/VoiceWaveform";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useCapabilities } from "@/hooks/useCapabilities";
import { useVoiceMode } from "@/hooks/useVoiceMode";
import { useInlinePermission } from "@/hooks/useInlinePermission";
import { fill, useT, useUiLanguage } from "@/i18n";
import { BROWSER_VOICE_FEATURE, askHostMicrophone, type HostMicrophoneAnswer } from "@/lib/hostMicrophone";
import { FALLBACK_APP_NAME, listPermissionNames, promptSentence } from "@/lib/permissionCopy";
import {
  browserRealtimeSupportIssue,
  RealtimeAudioClient,
  RealtimeAudioSupportError,
  type BrowserRealtimeSupportIssue,
} from "@/lib/realtimeAudio";
import { hasEmbeddedDesktopBridge } from "@/lib/embeddedDesktop";
import { isMacClient } from "@/lib/permissionPrompts";
import { useEventStore, type VoiceState } from "@/store/events";
import { usePermissionsStore } from "@/store/permissions";
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

// Defined in lib/embeddedDesktop.ts (a module with no UI imports, so permission
// surfaces can ask it cheaply); re-exported here because this is where the
// rest of the shell has always imported it from.
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

/** Browser-owned microphone control for remote/headless installations.
 *
 * The desktop shell already owns the physical microphone through
 * SpeechPipeline, so this control is rendered only when the capability route
 * says native desktop actions are unavailable. That prevents two concurrent
 * capture streams while still making a headless VPS usable entirely in-app.
 */
export function BrowserRealtimeControl({ controlOnly = false }: { controlOnly?: boolean } = {}) {
  const t = useT();
  const language = useUiLanguage();
  const appName = usePermissionsStore((store) => store.snapshot?.app_identity.app_name ?? "");
  const capabilities = useCapabilities();
  const { mode, realtimeAvailable, requiresWebRtcOffer, startBudgetMs, browserAudio } =
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
  // The host's microphone is waiting on the person (macOS asking, or a Settings
  // switch): while this control says so itself, the floating permission card
  // must not say it a second time (see useInlinePermission).
  const [hostMicNoteAt, setHostMicNoteAt] = useState(0);
  const hostMicNote = hostMicNoteAt > 0;
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
  // Only macOS gates the microphone on a system permission; on Windows and Linux
  // the embedded window requests nothing and adds no round trip before the stream.
  const hostMicGate = embedded && typeof navigator !== "undefined" && isMacClient(navigator.userAgent);
  // While this control itself says the host microphone is waiting on the person,
  // it is the inline surface: the floating card stays quiet.
  const { resolved: hostMicResolved } = useInlinePermission(BROWSER_VOICE_FEATURE, hostMicNote);
  const hostMicGrantedAt = hostMicResolved?.granted ? hostMicResolved.ts : 0;
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
  const browserSurface = Boolean(
    capabilities.data &&
      (browserAudio || capabilities.data.native_file_actions === false || !hasEmbeddedDesktopBridge()),
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

  /** The sentence for a host-microphone answer that stops the start (macOS asking, or blocked). */
  const hostMicrophoneSentence = useCallback(
    (answer: Extract<HostMicrophoneAnswer, { kind: "pending" | "blocked" }>) =>
      answer.kind === "pending"
        ? fill(t("permissions.inline.os_dialog"), {
            permissions: listPermissionNames(t, ["microphone"], language),
          })
        : promptSentence({
            t,
            language,
            appName: appName || FALLBACK_APP_NAME,
            episode: {
              feature: BROWSER_VOICE_FEATURE,
              reason: answer.reason,
              permissions: ["microphone"],
            },
          }),
    [appName, language, t],
  );

  // The person answered macOS (or flipped the Settings switch): replace the
  // waiting/blocked text with "allowed - press again". Nothing starts by itself.
  useEffect(() => {
    // A grant from before this note was raised is not the answer to it.
    if (!hostMicNote || hostMicGrantedAt < hostMicNoteAt) return;
    setHostMicNoteAt(0);
    setError("");
    setState((current) => (current === "error" ? "idle" : current));
    setVoice("idle");
    setNotice(t("permissions.inline.browser_voice.allowed"));
  }, [hostMicGrantedAt, hostMicNote, hostMicNoteAt, setVoice, t]);

  const stop = useCallback(async () => {
    connectionGenerationRef.current += 1;
    const client = clientRef.current;
    clientRef.current = null;
    setState("idle");
    setEffectiveProvider("");
    setError("");
    setNotice("");
    setHostMicNoteAt(0);
    setSpokenText("");
    levelRef.current = 0;
    clearVoiceInputLevel("browser");
    clearVoiceOutputLevel("browser");
    setBrowserVoiceOutputOwnership(false);
    setBrowserVoiceInputOwnership(false);
    setVoice("idle");
    await client?.disconnect();
  }, [setVoice]);

  /**
   * `gesture` is true only for the person's own click. That click is the moment
   * the desktop window asks macOS for the microphone (before `getUserMedia`); a
   * start the desktop made by itself (a wake word, a request event) never asks.
   */
  const start = useCallback(async ({ gesture = false }: { gesture?: boolean } = {}) => {
    if (!realtimeAvailable || clientRef.current || state === "connecting") return;
    const generation = connectionGenerationRef.current + 1;
    connectionGenerationRef.current = generation;
    setState("connecting");
    setError("");
    setNotice("");
    setHostMicNoteAt(0);
    setEffectiveProvider("");
    levelRef.current = 0;
    clearVoiceInputLevel("browser");
    clearVoiceOutputLevel("browser");
    // The embedded desktop window reaches the SAME macOS microphone permission
    // the native pipeline uses; ask for it from this gesture, before the stream
    // opens. A remote browser keeps the browser's own site-settings message.
    if (gesture && hostMicGate) {
      const answer = await askHostMicrophone();
      if (connectionGenerationRef.current !== generation) return;
      if (answer.kind === "pending") {
        // macOS is asking; the person answers there and presses Start again.
        setState("idle");
        setNotice(hostMicrophoneSentence(answer));
        setHostMicNoteAt(Date.now());
        return;
      }
      if (answer.kind === "blocked") {
        setState("error");
        setError(hostMicrophoneSentence(answer));
        setHostMicNoteAt(Date.now());
        setVoice("error");
        return;
      }
    }
    let client: RealtimeAudioClient;
    const isCurrent = () =>
      connectionGenerationRef.current === generation && clientRef.current === client;
    client = new RealtimeAudioClient(
      {
        onTranscript: (text, isFinal, role) => {
          if (!isCurrent()) return;
          // Live adapters project all speaker snapshots onto the shared bus.
          // Keeping a second local caption would overwrite the conversation.
          if (browserAudio) return;
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
            setVoice("listening");
          } else if (status === "audio_closed") {
            void stop();
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
      { requiresWebRtcOffer, startBudgetMs, browserAudio },
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
      let message: string;
      if (cause instanceof RealtimeAudioSupportError) {
        message = supportMessage(cause.issue);
      } else if (cause instanceof DOMException && cause.name === "NotAllowedError") {
        // In the desktop window a refused stream is first of all macOS: map it
        // to the same permission episode as every other feature (the request
        // answers with what is really off). Only when macOS says it is fine,
        // or this is a remote browser, is it the browser's own site settings.
        message = t("sidebar.realtime_microphone_denied");
        if (gesture && hostMicGate) {
          const answer = await askHostMicrophone();
          if (connectionGenerationRef.current !== generation) return;
          if (answer.kind === "pending" || answer.kind === "blocked") {
            message = hostMicrophoneSentence(answer);
            setHostMicNoteAt(Date.now());
          }
        }
      } else {
        message = t("sidebar.realtime_error");
      }
      setError(message);
      setVoice("error");
    }
  }, [
    hostMicGate,
    hostMicrophoneSentence,
    pushToast,
    realtimeAvailable,
    requiresWebRtcOffer,
    browserAudio,
    setTranscription,
    setVoice,
    startBudgetMs,
    state,
    stop,
    supportMessage,
    t,
  ]);

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
    const owns = visible && (state === "connecting" || state === "connected");
    setBrowserVoiceInputOwnership(owns);
    setBrowserVoiceOutputOwnership(owns);
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
        onClick={() => void (connected ? stop() : start({ gesture: true }))}
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
