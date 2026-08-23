import { useEffect, useMemo, useRef } from "react";

import { useEventStore, type ChatMessage, type VoiceState } from "@/store/events";
import { VoiceWaveform, type WaveformPhase } from "@/components/overlay/VoiceWaveform";
import { voiceInputLevelRef } from "@/lib/voiceInputLevel";
import { useVoiceCall } from "@/components/agentic/useVoiceCall";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { useVoiceEngineDisplay } from "@/hooks/useVoiceEngineDisplay";
import { useWakeWord } from "@/hooks/useWakeWord";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { Greeting } from "@/components/home/Greeting";

/** How many finished lines the lane keeps above the bar. */
const TRANSCRIPT_LINES = 6;

/**
 * The voice stage — what the front page opens on.
 *
 * Vertically centred, one column: the greeting, the last few lines of the
 * conversation, the Jarvis bar, and one quiet line that says what to do
 * next. No microphone button (maintainer, 2026-08-23): you tap the bar, or
 * you say the wake word. The bar IS the control — the same start/stop path
 * the IDE's voice bubble uses, so there is exactly one way voice begins.
 *
 * The transcript lane reads the same `messages` the chat stage shows (a
 * spoken turn lands there as MessageSent, like a typed one) plus the live,
 * not-yet-final transcription, so what you are saying appears while you say
 * it. Older lines scroll away; this is the live turn, not an archive — the
 * archive is one click away in the sidebar's recent chats.
 */
export function VoiceStage() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const voiceState = useEventStore((s) => s.voiceState);
  const messages = useEventStore((s) => s.messages);
  const transcription = useEventStore((s) => s.transcription);
  const transcriptionFinal = useEventStore((s) => s.transcriptionFinal);
  const { connected, warming } = useVoiceReadiness();
  const engine = useVoiceEngineDisplay();
  const { active: callActive, busy: callBusy, connecting, toggleCall } = useVoiceCall();
  const { config: wakeConfig } = useWakeWord();
  const wakePhrase = wakeConfig?.phrase.trim() || "";

  const lines = useMemo(() => recentLines(messages, TRANSCRIPT_LINES), [messages]);
  const liveLine = transcription && !transcriptionFinal ? transcription : "";

  const laneEnd = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    laneEnd.current?.scrollIntoView({ block: "end" });
  }, [lines.length, liveLine]);

  const phase = waveformPhase(voiceState, connected);
  const pressDisabled = callBusy || connecting || !connected;
  const hint = hintFor({ connected, warming, connecting, voiceState, wakePhrase, t });
  const barLabel = callActive ? t("home.bar_stop") : t("home.bar_start");

  return (
    <div className="flex min-h-0 flex-1 flex-col items-center overflow-hidden" data-testid="voice-stage">
      <div className="flex min-h-0 w-full max-w-[760px] flex-1 flex-col justify-center gap-7 px-6 pb-14 pt-6">
        <Greeting subtitle={t("home.voice_subtitle")} muted={lines.length > 0 || Boolean(liveLine)} />

        <div
          className="flex max-h-[40vh] min-h-[96px] flex-col justify-end gap-3 overflow-y-auto px-1 scrollbar-jarvis"
          data-testid="voice-transcript"
          aria-live="polite"
        >
          {lines.map((m) => (
            <TranscriptLine
              key={m.id}
              who={m.role === "user" ? t("home.transcript_you") : assistantName}
              text={m.content}
              user={m.role === "user"}
            />
          ))}
          {liveLine && (
            <TranscriptLine who={t("home.transcript_you")} text={liveLine} user live />
          )}
          <div ref={laneEnd} />
        </div>

        <div className="flex flex-col items-center gap-3">
          <button
            type="button"
            onClick={() => void toggleCall()}
            disabled={pressDisabled}
            aria-label={barLabel}
            title={barLabel}
            data-testid="jarvis-bar"
            data-phase={phase}
            className={cn(
              "group relative flex h-16 w-full items-center gap-4 rounded-full border border-border bg-card pl-5 pr-3 text-left shadow-[0_1px_2px_rgb(var(--scrim-rgb)/0.06),0_12px_32px_rgb(var(--scrim-rgb)/0.08)] transition-[box-shadow,border-color]",
              "hover:border-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              "disabled:cursor-default disabled:opacity-80",
              callActive && "border-primary/50",
            )}
          >
            <span
              className={cn(
                "flex shrink-0 items-center gap-2 font-mono text-[10px] uppercase tracking-[0.16em]",
                callActive ? "text-primary" : "text-muted-foreground",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "h-2 w-2 rounded-full",
                  callActive ? "bg-primary animate-jarvis-pulse" : "bg-muted-foreground/40",
                )}
              />
              {t(`voice_state.${connecting ? "connecting" : voiceState}`)}
            </span>
            <VoiceWaveform
              levelRef={voiceInputLevelRef}
              phase={phase}
              count={28}
              frame={false}
              className="h-12 min-w-0 flex-1"
            />
            <span className="hidden shrink-0 items-center gap-1.5 rounded-full border border-border bg-secondary/60 px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground sm:flex">
              {engine.providerLabel}
            </span>
          </button>
          <p
            className="font-mono text-[11px] tracking-[0.04em] text-muted-foreground"
            data-testid="voice-hint"
          >
            {hint}
          </p>
        </div>
      </div>
    </div>
  );
}

function TranscriptLine({
  who,
  text,
  user,
  live = false,
}: {
  who: string;
  text: string;
  user: boolean;
  live?: boolean;
}) {
  return (
    <div
      className="grid grid-cols-[64px_1fr] items-baseline gap-x-4"
      data-testid={live ? "transcript-live" : "transcript-line"}
    >
      <span className="truncate text-right font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
        {who}
      </span>
      <span
        className={cn(
          "whitespace-pre-wrap",
          user ? "text-[15px] text-muted-foreground" : "font-display text-[17px] leading-snug text-foreground",
          live && "italic",
        )}
      >
        {text}
        {live && (
          <span className="ml-0.5 inline-block h-[1em] w-0.5 translate-y-0.5 animate-pulse bg-primary motion-reduce:animate-none" aria-hidden />
        )}
      </span>
    </div>
  );
}

/** The last N spoken/typed lines worth showing: user and assistant only. */
export function recentLines(messages: ChatMessage[], limit: number): ChatMessage[] {
  const out: ChatMessage[] = [];
  for (let i = messages.length - 1; i >= 0 && out.length < limit; i--) {
    const m = messages[i];
    if (m.role === "user" || m.role === "assistant") out.push(m);
  }
  return out.reverse();
}

export function waveformPhase(state: VoiceState, connected: boolean): WaveformPhase {
  if (!connected) return "idle";
  switch (state) {
    case "connecting":
      return "connecting";
    case "listening":
      return "listening";
    case "thinking":
      return "working";
    case "speaking":
      return "speaking";
    case "error":
      return "error";
    default:
      return "idle";
  }
}

/** One line under the bar: what to do, or what is happening. */
export function hintFor({
  connected,
  warming,
  connecting,
  voiceState,
  wakePhrase,
  t,
}: {
  connected: boolean;
  warming: boolean;
  connecting: boolean;
  voiceState: VoiceState;
  wakePhrase: string;
  t: (key: string) => string;
}): string {
  if (!connected) return warming ? t("home.hint_warming") : t("home.hint_offline");
  if (warming) return t("home.hint_warming");
  if (connecting) return t("home.hint_connecting");
  switch (voiceState) {
    case "listening":
      return t("home.hint_listening");
    case "thinking":
      return t("home.hint_thinking");
    case "speaking":
      return t("home.hint_speaking");
    case "paused":
      return t("home.hint_paused");
    case "error":
      return t("home.hint_error");
    default:
      return wakePhrase
        ? fill(t("home.hint_idle"), { wake: wakePhrase })
        : t("home.hint_idle_nowake");
  }
}
