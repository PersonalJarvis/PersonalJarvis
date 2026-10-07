import { useLayoutEffect, useMemo } from "react";

import { useEventStore, type VoiceState } from "@/store/events";
import { useHomeStore } from "@/store/home";
import { useVoiceCall } from "@/components/agentic/useVoiceCall";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { useStickToBottom } from "@/hooks/useStickToBottom";
import { useWakeWord } from "@/hooks/useWakeWord";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ScrollToEndButton } from "@/components/ui/scroll-to-end-button";
import { Greeting } from "@/components/home/Greeting";
import { PetMark } from "@/components/pets/PetMark";
import { VoiceComposer } from "@/components/home/VoiceComposer";
import { VoiceGlow } from "@/components/home/VoiceGlow";
import { useSpokenCursor } from "@/components/home/useSpokenCursor";
import { TurnSteps, traceWorthShowing } from "@/components/home/TurnSteps";
import { traceModel } from "@/lib/thinkingSteps";

/**
 * The live voice conversation shares the chat column and scroll behavior.
 * Speaker captions and tool steps remain visible for the whole conversation.
 * Players that report word boundaries can highlight their current position;
 * unaligned streams render ordinary text without pretending to know it.
 */
export function VoiceStage({ onExit }: { onExit?: () => void } = {}) {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const voiceState = useEventStore((s) => s.voiceState);
  const transcript = useHomeStore((s) => s.transcript);
  const liveReply = useHomeStore((s) => s.liveReply);
  const transcription = useEventStore((s) => s.transcription);
  const transcriptionFinal = useEventStore((s) => s.transcriptionFinal);
  const { connected, warming } = useVoiceReadiness();
  const { active, connecting } = useVoiceCall();
  const { config: wakeConfig } = useWakeWord();
  const wakePhrase = wakeConfig?.phrase.trim() || "";

  // Steps blocks with nothing to show (a sub-second brain call, no tools)
  // are dropped so an empty block never leaves a blank gap in the lane.
  const lines = useMemo(
    () =>
      transcript.filter(
        (m) => m.who !== "steps" || traceWorthShowing(m.steps, m.durationMs, m.live),
      ),
    [transcript],
  );
  const liveLine = transcription && !transcriptionFinal ? transcription : "";
  // The answer as it is produced, until its spoken line replaces it. Hidden
  // once the lane's last line already IS the answer (the final arrived and
  // a late snapshot would only repeat it).
  const lastLine = lines[lines.length - 1];
  const liveAnswer =
    liveReply && !(lastLine?.who === "assistant" && lastLine.text.startsWith(liveReply))
      ? liveReply
      : "";
  const hasLines = lines.length > 0 || Boolean(liveLine) || Boolean(liveAnswer);
  // The answer the voice is saying right now: the growing snapshot, or —
  // once its final line has landed while the audio still plays — that line.
  const lastIsAnswer = !liveAnswer && lastLine?.who === "assistant";
  // A live work trace already shows the pet on its live line — also while
  // the answer streams in under it — so a second pet below would say the
  // same thing twice.
  const traceShowsPet = lines.some((m) => m.who === "steps" && m.live);

  const { rootRef, contentRef, atEnd, jumpToEnd, follow } = useStickToBottom();
  useLayoutEffect(follow, [follow, lines, liveLine, liveAnswer]);

  const hint = hintFor({ connected, warming, connecting, voiceState, wakePhrase, t });

  if (!hasLines) {
    return (
      <div
        className="relative flex min-h-0 flex-1 flex-col items-center overflow-hidden"
        data-testid="voice-stage"
        data-empty="true"
      >
        <VoiceGlow active={active} thinking={voiceState === "thinking"} />
        <div className="relative flex w-full max-w-[680px] flex-1 flex-col justify-center gap-7 px-6 pb-[14vh]">
          <Greeting subtitle={t("home.voice_subtitle")} />
          <VoiceComposer hint={hint} onExit={onExit} />
        </div>
      </div>
    );
  }

  return (
    <div
      className="relative flex min-h-0 flex-1 flex-col items-center overflow-hidden"
      data-testid="voice-stage"
      data-empty="false"
    >
      <VoiceGlow active={active} thinking={voiceState === "thinking"} />
      <ScrollArea ref={rootRef} className="relative min-h-0 w-full flex-1" data-testid="voice-transcript">
        <div
          ref={contentRef}
          className="mx-auto flex w-full max-w-[720px] flex-col gap-6 px-6 pb-6 pt-8"
          aria-live="polite"
        >
          {lines.map((m, i) =>
            m.who === "steps" ? (
              <div key={m.id} className="max-w-[85%] pl-1" data-testid="transcript-steps">
                <TurnSteps
                  steps={m.steps}
                  live={m.live}
                  durationMs={m.durationMs}
                  model={traceModel(m.steps)}
                  compact
                  defaultOpen={m.live}
                />
              </div>
            ) : (
              <TranscriptLine
                key={m.id}
                who={m.who === "user" ? t("home.transcript_you") : assistantName}
                text={m.text}
                lineId={m.id}
                user={m.who === "user"}
                playbackEligible={active && lastIsAnswer && i === lines.length - 1}
              />
            ),
          )}
          {liveLine && <TranscriptLine who={t("home.transcript_you")} text={liveLine} user live />}
          {liveAnswer && (
            <TranscriptLine who={assistantName} text={liveAnswer} user={false} live playbackEligible={active} />
          )}
          {active && (voiceState === "thinking" || voiceState === "speaking") && !traceShowsPet && (
            <div className="pl-0.5" data-testid="voice-turn-indicator" aria-hidden>
              <PetMark size={48} state={voiceState === "speaking" ? "talking" : "working"} />
            </div>
          )}
        </div>
      </ScrollArea>

      <div className="relative w-full max-w-[720px] px-6 pb-4 pt-2">
        {!atEnd && <ScrollToEndButton onClick={jumpToEnd} testId="voice-scroll-end" />}
        <VoiceComposer hint={hint} onExit={onExit} />
      </div>
    </div>
  );
}

/**
 * One line of the lane, drawn the way the chat draws a turn so voice mode
 * reads like the chat it lives in: your words in a soft bubble on the
 * right — italic, because they were heard — the assistant's as plain text
 * on the left. A LIVE line (words still being said, an
 * answer still being produced) is dimmed and italic with a cursor; the
 * finished line it becomes is the same words, settled. `spoken`, while the
 * voice is saying this line, splits it: the words already said in full ink,
 * the rest grey. The speaker's name stays for screen readers only — the side
 * says who spoke.
 */
export function TranscriptLine({
  who,
  text,
  user,
  live = false,
  playbackEligible = false,
  lineId = "",
}: {
  who: string;
  text: string;
  user: boolean;
  live?: boolean;
  playbackEligible?: boolean;
  lineId?: string;
}) {
  const spoken = useSpokenCursor(text, !user && playbackEligible, lineId);
  const reading = !user && spoken !== null;
  return (
    <div
      className={cn("flex", user ? "justify-end" : "justify-start")}
      data-testid={live ? (user ? "transcript-live" : "transcript-live-answer") : "transcript-line"}
      data-who={user ? "user" : "assistant"}
    >
      <span className="sr-only">{who}: </span>
      <span
        className={cn(
          "max-w-[80%] whitespace-pre-wrap text-reading",
          user ? "jarvis-user-bubble rounded-[20px] px-4 py-2.5 italic" : "text-foreground",
          live && !reading && (user ? "opacity-70" : "text-muted-foreground"),
        )}
      >
        {reading ? (
          <>
            <span data-testid="spoken-part">{text.slice(0, spoken)}</span>
            <span className="text-muted-foreground" data-testid="unspoken-part">
              {text.slice(spoken)}
            </span>
          </>
        ) : (
          text
        )}
        {live && !reading && (
          <span
            className="ml-0.5 inline-block h-[1em] w-0.5 translate-y-0.5 animate-pulse bg-current motion-reduce:animate-none"
            aria-hidden
          />
        )}
      </span>
    </div>
  );
}

/** The voice composer's line: what to do, or what is happening. */
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
