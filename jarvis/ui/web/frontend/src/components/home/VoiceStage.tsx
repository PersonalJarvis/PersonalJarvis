import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowDown } from "lucide-react";

import { useEventStore, type VoiceState } from "@/store/events";
import { useHomeStore } from "@/store/home";
import type { WaveformPhase } from "@/components/overlay/VoiceWaveform";
import { useVoiceCall } from "@/components/agentic/useVoiceCall";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { useWakeWord } from "@/hooks/useWakeWord";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Greeting } from "@/components/home/Greeting";
import { JarvisBar } from "@/components/home/JarvisBar";
import { TurnSteps, traceWorthShowing } from "@/components/home/TurnSteps";
import { traceModel } from "@/lib/thinkingSteps";

/**
 * The voice stage — what the front page opens on.
 *
 * Empty, it is one centred column: the greeting, the Jarvis bar, and one
 * quiet line that says what to do next. No microphone button (maintainer,
 * 2026-08-23): you tap the bar, or you say the wake word. The bar IS the
 * control — the same start/stop path the IDE's voice bubble uses, so there
 * is exactly one way voice begins.
 *
 * Once anything has been said the page becomes a document, the way the chat
 * stage next door already is: the whole conversation scrolls in its own
 * viewport and the bar docks to the bottom. WHOLE, not a window onto the
 * last few turns — until 2026-08-24 the lane rendered only the last 8 lines,
 * so anything that scrolled off was gone from the DOM and could not be
 * scrolled back to. The lane reads the home store's transcript
 * (lib/homeTranscript: heard words, the turn's reasoning steps, spoken
 * answers and typed turns merged into one list) plus the live, not-yet-final
 * transcription, so what you are saying appears while you say it. A turn's
 * steps render between your words and the answer — live while the turn runs,
 * folded afterwards (components/home/TurnSteps).
 *
 * Scrolling follows the Claude app. New output pulls the view along ONLY
 * while the view is already at the end; scrolled up to read something, you
 * keep your place while the conversation goes on below, and a button over
 * the bar takes you back to the end. Nothing yanks the page out from under
 * someone mid-sentence.
 */
export function VoiceStage() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  const voiceState = useEventStore((s) => s.voiceState);
  const transcript = useHomeStore((s) => s.transcript);
  const liveReply = useHomeStore((s) => s.liveReply);
  const transcription = useEventStore((s) => s.transcription);
  const transcriptionFinal = useEventStore((s) => s.transcriptionFinal);
  const { connected, warming } = useVoiceReadiness();
  const { connecting } = useVoiceCall();
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

  const rootRef = useRef<HTMLDivElement | null>(null);
  const columnRef = useRef<HTMLDivElement | null>(null);
  // Whether new output pulls the view along. A ref, not state: the auto-scroll
  // below runs in a layout effect and must read the CURRENT answer, not the
  // one a re-render would deliver a frame later. `atEnd` mirrors it for the
  // button, which only has to be right by the next paint.
  const stickRef = useRef(true);
  const [atEnd, setAtEnd] = useState(true);

  const jumpToEnd = useCallback(() => {
    const viewport = viewportOf(rootRef.current);
    if (!viewport) return;
    stickRef.current = true;
    setAtEnd(true);
    if (!prefersReducedMotion() && typeof viewport.scrollTo === "function") {
      viewport.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" });
    } else {
      viewport.scrollTop = viewport.scrollHeight;
    }
  }, []);

  // One listener answers both questions: does new output pull the view along,
  // and is the "back to the end" button needed.
  useEffect(() => {
    const viewport = viewportOf(rootRef.current);
    if (!viewport) return;
    const read = () => {
      const near = isNearEnd(viewport.scrollTop, viewport.scrollHeight, viewport.clientHeight);
      stickRef.current = near;
      setAtEnd(near);
    };
    read();
    viewport.addEventListener("scroll", read, { passive: true });
    return () => viewport.removeEventListener("scroll", read);
  }, [hasLines]);

  useLayoutEffect(() => {
    const viewport = viewportOf(rootRef.current);
    if (!viewport || !stickRef.current) return;
    viewport.scrollTop = viewport.scrollHeight;
  }, [lines, liveLine, liveAnswer]);

  // An answer and its steps grow WITHOUT a new line arriving, so the effect
  // above never fires for them; the column's own size is the honest signal
  // where the platform has ResizeObserver.
  useEffect(() => {
    const column = columnRef.current;
    const viewport = viewportOf(rootRef.current);
    if (!column || !viewport || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (stickRef.current) viewport.scrollTop = viewport.scrollHeight;
    });
    ro.observe(column);
    return () => ro.disconnect();
  }, [hasLines]);

  const phase = waveformPhase(voiceState, connected);
  const hint = hintFor({ connected, warming, connecting, voiceState, wakePhrase, t });

  if (!hasLines) {
    return (
      <div
        className="flex min-h-0 flex-1 flex-col items-center overflow-hidden"
        data-testid="voice-stage"
        data-empty="true"
      >
        <div className="flex w-full max-w-[760px] flex-1 flex-col justify-center gap-8 px-6 pb-14">
          <Greeting subtitle={t("home.voice_subtitle")} />
          <JarvisBar phase={phase} hint={hint} />
        </div>
      </div>
    );
  }

  return (
    <div
      className="flex min-h-0 flex-1 flex-col items-center overflow-hidden"
      data-testid="voice-stage"
      data-empty="false"
    >
      <ScrollArea ref={rootRef} className="min-h-0 w-full flex-1" data-testid="voice-transcript">
        <div
          ref={columnRef}
          className="mx-auto flex w-full max-w-[760px] flex-col gap-3 px-6 pb-4 pt-6"
          aria-live="polite"
        >
          <Greeting subtitle={t("home.voice_subtitle")} muted />
          <div className="h-2 shrink-0" aria-hidden />
          {lines.map((m) =>
            m.who === "steps" ? (
              <div key={m.id} className="pl-[80px]" data-testid="transcript-steps">
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
        </div>
      </ScrollArea>

      <div className="relative w-full max-w-[760px] px-6 pb-6 pt-2">
        {!atEnd && (
          <button
            type="button"
            onClick={jumpToEnd}
            data-testid="voice-scroll-end"
            aria-label={t("home.transcript_to_end")}
            title={t("home.transcript_to_end")}
            className={cn(
              "absolute -top-3 left-1/2 z-10 flex h-8 w-8 -translate-x-1/2 items-center justify-center",
              "rounded-full border border-border bg-card/95 text-muted-foreground shadow-md backdrop-blur",
              "transition-colors hover:border-primary/40 hover:text-foreground",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            )}
          >
            <ArrowDown aria-hidden className="h-4 w-4" />
          </button>
        )}
        <JarvisBar phase={phase} hint={hint} />
      </div>
    </div>
  );
}

/**
 * Within this many pixels of the bottom counts as "at the end", so sub-pixel
 * rounding — or the half line a growing answer adds between two frames —
 * never reads as "they scrolled away".
 */
export const NEAR_END_PX = 72;

export function isNearEnd(scrollTop: number, scrollHeight: number, clientHeight: number): boolean {
  return scrollHeight - scrollTop - clientHeight <= NEAR_END_PX;
}

/** Radix renders the scrolling element as the viewport inside our ScrollArea root. */
function viewportOf(root: HTMLElement | null): HTMLElement | null {
  if (!root) return null;
  return (root.querySelector("[data-radix-scroll-area-viewport]") as HTMLElement | null) ?? root;
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
    : false;
}

/**
 * One line of the lane. A LIVE line (words still being said, an answer still
 * being produced) is muted and italic with a cursor; the finished line it
 * becomes is in full colour — the same words, settled.
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
      className="grid grid-cols-[64px_1fr] items-baseline gap-x-4"
      data-testid={live ? (user ? "transcript-live" : "transcript-live-answer") : "transcript-line"}
      data-who={user ? "user" : "assistant"}
    >
      <span className="truncate text-right font-mono text-[10px] uppercase tracking-[0.12em] text-muted-foreground">
        {who}
      </span>
      <span
        className={cn(
          "whitespace-pre-wrap",
          user
            ? cn("text-[15px]", live ? "text-muted-foreground/70" : "text-muted-foreground")
            : cn(
                "font-display text-[17px] leading-snug",
                live ? "text-muted-foreground" : "text-foreground",
              ),
          live && "italic",
        )}
      >
        {text}
        {live && (
          <span
            className="ml-0.5 inline-block h-[1em] w-0.5 translate-y-0.5 animate-pulse bg-primary motion-reduce:animate-none"
            aria-hidden
          />
        )}
      </span>
    </div>
  );
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
