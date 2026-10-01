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
import { GigiMark } from "@/components/GigiMark";
import { VoiceComposer } from "@/components/home/VoiceComposer";
import { VoiceGlow } from "@/components/home/VoiceGlow";
import { TurnSteps, traceWorthShowing } from "@/components/home/TurnSteps";
import { traceModel } from "@/lib/thinkingSteps";

/**
 * The voice stage — the front page's chat, spoken (2026-10-01, after the
 * Claude app's voice mode).
 *
 * It looks like the chat it lives in, not like a separate page: the same
 * column, your words in soft bubbles on the right (italic, because they were
 * heard rather than typed), the answers as plain text on the left, and at
 * the bottom a composer-shaped card (components/home/VoiceComposer) that
 * says what is happening and carries Start / Stop. Behind it a soft light
 * rises from the bottom edge and breathes with the voices
 * (components/home/VoiceGlow); while the assistant thinks or speaks, its
 * mark pulses under the last line. `onExit`, where the host page has a typed
 * half, puts the way back to the keyboard into the composer.
 *
 * Empty, it is one centred column: the greeting and the voice composer.
 * Once anything has been said the page becomes a document: the whole
 * conversation scrolls in its own viewport and the composer docks to the
 * bottom. WHOLE, not a window onto the
 * last few turns — until 2026-08-24 the lane rendered only the last 8 lines,
 * so anything that scrolled off was gone from the DOM and could not be
 * scrolled back to. The lane reads the home store's transcript
 * (lib/homeTranscript: heard words, the turn's reasoning steps, spoken
 * answers and typed turns merged into one list) plus the live, not-yet-final
 * transcription, so what you are saying appears while you say it. A turn's
 * steps render between your words and the answer — live while the turn runs,
 * folded afterwards (components/home/TurnSteps).
 *
 * Scrolling follows the Claude app, through the rule every conversation
 * surface here shares (hooks/useStickToBottom): new output pulls the view
 * along ONLY while the view is already at the end; scrolled up to read
 * something, you keep your place while the conversation goes on below, and a
 * button over the bar takes you back. Nothing yanks the page out from under
 * someone mid-sentence.
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
        <VoiceGlow active={active} />
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
      <VoiceGlow active={active} />
      <ScrollArea ref={rootRef} className="relative min-h-0 w-full flex-1" data-testid="voice-transcript">
        <div
          ref={contentRef}
          className="mx-auto flex w-full max-w-[720px] flex-col gap-6 px-6 pb-6 pt-8"
          aria-live="polite"
        >
          {lines.map((m) =>
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
                user={m.who === "user"}
              />
            ),
          )}
          {liveLine && <TranscriptLine who={t("home.transcript_you")} text={liveLine} user live />}
          {liveAnswer && <TranscriptLine who={assistantName} text={liveAnswer} user={false} live />}
          {active && (voiceState === "thinking" || voiceState === "speaking") && (
            <div className="pl-0.5" data-testid="voice-turn-indicator" aria-hidden>
              <GigiMark size={22} className="rounded-md animate-pulse motion-reduce:animate-none" />
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
 * finished line it becomes is the same words, settled. The speaker's name
 * stays for screen readers only — the side says who spoke.
 */
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
      className={cn("flex", user ? "justify-end" : "justify-start")}
      data-testid={live ? (user ? "transcript-live" : "transcript-live-answer") : "transcript-line"}
      data-who={user ? "user" : "assistant"}
    >
      <span className="sr-only">{who}: </span>
      <span
        className={cn(
          "max-w-[80%] whitespace-pre-wrap text-reading",
          user ? "jarvis-user-bubble rounded-[20px] px-4 py-2.5 italic" : "text-foreground",
          live && (user ? "opacity-70" : "text-muted-foreground"),
        )}
      >
        {text}
        {live && (
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
