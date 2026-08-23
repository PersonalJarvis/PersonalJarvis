import { useEffect, useMemo, useRef } from "react";

import { useEventStore, type VoiceState } from "@/store/events";
import { useHomeStore } from "@/store/home";
import type { TranscriptLine as TranscriptEntry } from "@/lib/homeTranscript";
import type { WaveformPhase } from "@/components/overlay/VoiceWaveform";
import { useVoiceCall } from "@/components/agentic/useVoiceCall";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { useWakeWord } from "@/hooks/useWakeWord";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { Greeting } from "@/components/home/Greeting";
import { JarvisBar } from "@/components/home/JarvisBar";
import { TurnSteps } from "@/components/home/TurnSteps";

/** How many finished lines the lane keeps above the bar. */
const TRANSCRIPT_LINES = 8;

/**
 * The voice stage — what the front page opens on.
 *
 * Vertically centred, one column: the greeting, the last few lines of the
 * conversation, the Jarvis bar, and one quiet line that says what to do
 * next. No microphone button (maintainer, 2026-08-23): you tap the bar, or
 * you say the wake word. The bar IS the control — the same start/stop path
 * the IDE's voice bubble uses, so there is exactly one way voice begins.
 *
 * The transcript lane reads the home store's transcript (lib/homeTranscript:
 * heard words, the turn's reasoning steps, spoken answers and typed turns
 * merged into one list) plus the live, not-yet-final transcription, so what
 * you are saying appears while you say it. A turn's steps render between
 * your words and the answer — live while the turn runs, folded afterwards
 * (components/home/TurnSteps). Older lines scroll away; this is the live
 * turn, not an archive — the archive is one click away in the sidebar's
 * recent chats.
 */
export function VoiceStage() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const voiceState = useEventStore((s) => s.voiceState);
  const transcript = useHomeStore((s) => s.transcript);
  const transcription = useEventStore((s) => s.transcription);
  const transcriptionFinal = useEventStore((s) => s.transcriptionFinal);
  const { connected, warming } = useVoiceReadiness();
  const { connecting } = useVoiceCall();
  const { config: wakeConfig } = useWakeWord();
  const wakePhrase = wakeConfig?.phrase.trim() || "";

  const lines = useMemo(() => recentLines(transcript, TRANSCRIPT_LINES), [transcript]);
  const liveLine = transcription && !transcriptionFinal ? transcription : "";
  // A live turn keeps the lane pinned to its end as steps arrive.
  const liveSteps = lines.some((m) => m.who === "steps" && m.live);

  const laneEnd = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    laneEnd.current?.scrollIntoView({ block: "end" });
  }, [lines, liveLine, liveSteps]);

  const phase = waveformPhase(voiceState, connected);
  const hint = hintFor({ connected, warming, connecting, voiceState, wakePhrase, t });

  return (
    <div className="flex min-h-0 flex-1 flex-col items-center overflow-hidden" data-testid="voice-stage">
      <div className="flex min-h-0 w-full max-w-[760px] flex-1 flex-col justify-center gap-7 px-6 pb-14 pt-6">
        <Greeting subtitle={t("home.voice_subtitle")} muted={lines.length > 0 || Boolean(liveLine)} />

        <div
          className="flex max-h-[44vh] min-h-[96px] flex-col justify-end gap-3 overflow-y-auto px-1 scrollbar-jarvis"
          data-testid="voice-transcript"
          aria-live="polite"
        >
          {lines.map((m) =>
            m.who === "steps" ? (
              <div key={m.id} className="pl-[80px]" data-testid="transcript-steps">
                <TurnSteps
                  steps={m.steps}
                  live={m.live}
                  durationMs={m.durationMs}
                  compact
                  defaultOpen={m.live}
                />
              </div>
            ) : (
              <TranscriptLine
                key={m.id}
                who={m.who === "user" ? t("home.transcript_you") : assistantName}
                text={m.text}
                user={m.who === "user"}
              />
            ),
          )}
          {liveLine && (
            <TranscriptLine who={t("home.transcript_you")} text={liveLine} user live />
          )}
          <div ref={laneEnd} />
        </div>

        <div className="flex flex-col items-center gap-3">
          <JarvisBar phase={phase} />
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

/** The last N lines of the transcript, oldest first. */
export function recentLines(lines: TranscriptEntry[], limit: number): TranscriptEntry[] {
  return lines.length > limit ? lines.slice(lines.length - limit) : lines;
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
