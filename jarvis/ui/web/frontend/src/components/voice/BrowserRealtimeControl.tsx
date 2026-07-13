import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Mic, MicOff, RotateCcw } from "lucide-react";

import { useCapabilities } from "@/hooks/useCapabilities";
import { useVoiceMode } from "@/hooks/useVoiceMode";
import { useT } from "@/i18n";
import { RealtimeAudioClient } from "@/lib/realtimeAudio";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";

type ConnectionState = "idle" | "connecting" | "connected" | "error";

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
  const { mode, realtimeAvailable } = useVoiceMode();
  const setVoice = useEventStore((store) => store.setVoice);
  const setTranscription = useEventStore((store) => store.setTranscription);
  const [state, setState] = useState<ConnectionState>("idle");
  const [effectiveProvider, setEffectiveProvider] = useState("");
  const [error, setError] = useState("");
  const [inputLevel, setInputLevel] = useState(0);
  const clientRef = useRef<RealtimeAudioClient | null>(null);
  const browserSurface = capabilities.data?.native_file_actions === false;
  const visible = browserSurface && mode === "realtime";

  const stop = useCallback(async () => {
    const client = clientRef.current;
    clientRef.current = null;
    await client?.disconnect();
    setState("idle");
    setEffectiveProvider("");
    setError("");
    setInputLevel(0);
    setVoice("idle");
  }, [setVoice]);

  const start = useCallback(async () => {
    if (!realtimeAvailable || state === "connecting") return;
    await clientRef.current?.disconnect();
    setState("connecting");
    setError("");
    setEffectiveProvider("");

    const client = new RealtimeAudioClient({
      onTranscript: (text, isFinal, role) => {
        if (role === "user") setTranscription(text, isFinal);
        if (role === "user" && isFinal) setVoice("thinking");
      },
      onAudio: () => {
        setError("");
        setVoice("speaking");
      },
      onInputLevel: setInputLevel,
      onStatus: (status, payload) => {
        if (status === "audio_ready") {
          const provider = typeof payload.provider === "string" ? payload.provider : "";
          if (provider) setEffectiveProvider(provider);
          setState("connected");
          setVoice("listening");
        } else if (status === "mode_fallback") {
          setEffectiveProvider(t("sidebar.realtime_pipeline_fallback"));
        } else if (status === "hangup") {
          // The session ended the call by voice ("auflegen" / end_call) —
          // release the microphone and return to idle.
          void stop();
        } else if (status === "turn_complete" || status === "tts_end") {
          setVoice("listening");
        } else if (status === "tts_cancel") {
          setVoice("listening");
        } else if (status === "tts_browser_unavailable" || status === "tts_browser_error") {
          setError(t("sidebar.realtime_browser_tts_unavailable"));
          setVoice("listening");
        } else if (status === "provider_error" || status === "disconnected") {
          setState("error");
          setError(t("sidebar.realtime_error"));
          setVoice("error");
        }
      },
    });
    clientRef.current = client;
    try {
      await client.connect();
      setState("connected");
    } catch {
      if (clientRef.current === client) clientRef.current = null;
      setState("error");
      setError(t("sidebar.realtime_error"));
      setVoice("error");
    }
  }, [realtimeAvailable, setTranscription, setVoice, state, stop, t]);

  useEffect(() => {
    if (visible) return;
    const client = clientRef.current;
    clientRef.current = null;
    if (client) void client.disconnect();
  }, [visible]);

  useEffect(
    () => () => {
      const client = clientRef.current;
      clientRef.current = null;
      if (client) void client.disconnect();
    },
    [],
  );

  if (!visible) return null;

  const connected = state === "connected";
  const connecting = state === "connecting";
  const unavailable = !realtimeAvailable;
  const label = unavailable
    ? t("sidebar.realtime_unavailable")
    : connected
      ? t("sidebar.realtime_stop")
      : state === "error"
        ? t("sidebar.realtime_retry")
        : t("sidebar.realtime_start");
  const Icon = connecting ? Loader2 : connected ? MicOff : state === "error" ? RotateCcw : Mic;

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
      {connected && (
        <div className="mt-1.5 flex h-1.5 items-stretch gap-0.5" aria-hidden="true">
          {[0.08, 0.24, 0.42, 0.6, 0.78].map((threshold) => (
            <span
              key={threshold}
              className={cn(
                "flex-1 rounded-sm transition-colors duration-75",
                inputLevel >= threshold ? "bg-primary" : "bg-border/60",
              )}
            />
          ))}
        </div>
      )}
      <div className="mt-1.5 min-h-4 text-[10px] text-muted-foreground" aria-live="polite">
        {error ||
          (connected
            ? `${t("sidebar.realtime_connected")} ${effectiveProvider}`.trim()
            : t("sidebar.realtime_browser_hint"))}
      </div>
    </div>
  );
}
