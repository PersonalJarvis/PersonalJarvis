import { useEffect, useRef, useState } from "react";
import { ScrollText } from "lucide-react";
import { useEventStore } from "@/store/events";
import { useDeckStore } from "@/store/deck";
import type { JournalKind, JournalLine, TurnPhase } from "@/lib/deckState";
import { useVoiceReadiness } from "@/hooks/useVoiceReadiness";
import { DeckCard } from "@/components/deck/DeckCard";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * The deck's terminal — the session log.
 *
 * One line per thing the assistant heard, thought, did or said, with the
 * clock time in front and the measured duration at the end: the reasoning
 * made visible as it happens, and kept afterwards so the last minutes can be
 * read back (maintainer decision 2026-08-18: "a display that always shows
 * something, like a terminal, showing the reasoning and how long it takes").
 *
 * The lines come from `deckState.reduceJournal`, folded from the same bus
 * events every other card reads — nothing here is invented. The last row is
 * the cursor: what the assistant is doing this very second, and how long it
 * has been quiet, so the terminal is never blank even on a fresh start.
 */

/** How many lines the terminal paints — the store keeps more, the eye needs fewer. */
const SHOWN_LINES = 60;

// Every signal colour is a PAIR — a dark tint on black, its deep twin on paper
// (CLOUD.md "Frontend theming"): a 400-tint alone reads on the dark stage
// and disappears on the light one.
const TAG_TONE: Record<JournalKind, string> = {
  boot: "text-muted-foreground",
  wake: "text-primary",
  hear: "text-sky-700 dark:text-sky-400",
  think: "text-primary",
  done: "text-primary",
  tool: "text-muted-foreground",
  say: "text-foreground",
  worker: "text-muted-foreground",
  control: "text-violet-700 dark:text-violet-400",
  look: "text-violet-700 dark:text-violet-400",
  memory: "text-sky-700 dark:text-sky-400",
  error: "text-destructive",
  note: "text-muted-foreground",
};

/** Lines whose text is something a person said or heard — shown as speech. */
const SPEECH: ReadonlySet<JournalKind> = new Set(["hear", "say"]);

export function fmtMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.floor((ms % 60_000) / 1000);
  return `${minutes}:${String(seconds).padStart(2, "0")} min`;
}

export function fmtClock(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** "quiet for 3 min" — coarse on purpose; a second-by-second count would fidget. */
export function fmtQuiet(ms: number): string {
  if (ms < 60_000) return `${Math.max(1, Math.floor(ms / 1000))} s`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)} min`;
  return `${Math.floor(ms / 3_600_000)} h`;
}

function fill(template: string, args?: Record<string, string>): string {
  if (!args) return template;
  return template.replace(/\{(\w+)\}/g, (m, k: string) => (k in args ? args[k] : m));
}

const NOW_KEY: Record<TurnPhase, string> = {
  idle: "deck.log_now_idle",
  hear: "deck.log_now_hear",
  think: "deck.log_now_think",
  act: "deck.log_now_act",
  speak: "deck.log_now_speak",
};

export function LogCard({ className }: { className?: string }) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const journal = useDeckStore((s) => s.journal);
  const turn = useDeckStore((s) => s.turn);
  const { connected, warming } = useVoiceReadiness();
  const voiceState = useEventStore((s) => s.voiceState);

  // The cursor line ticks once a second: "quiet for 3 min" has to move
  // without an event arriving, that is its whole point.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  // Stick to the bottom like a terminal — unless the reader scrolled up to
  // read something, in which case new lines must not yank them back down.
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const stickRef = useRef(true);
  const onScroll = () => {
    const el = bodyRef.current;
    if (!el) return;
    stickRef.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 24;
  };
  useEffect(() => {
    const el = bodyRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [journal.length, turn.phase]);

  const shown = journal.length > SHOWN_LINES ? journal.slice(journal.length - SHOWN_LINES) : journal;
  const live = turn.phase !== "idle";
  const lastTs = journal.length > 0 ? journal[journal.length - 1].ts : null;

  // What the cursor says: the link first, then the voice, then the turn — and
  // the supervisor's own state when it is ahead of the turn (listening
  // before a transcript exists).
  const nowKey = !connected
    ? "deck.log_now_offline"
    : warming
      ? "deck.log_now_warming"
      : live
        ? NOW_KEY[turn.phase]
        : voiceState === "listening"
          ? "deck.log_now_hear"
          : voiceState === "speaking"
            ? "deck.log_now_speak"
            : "deck.log_now_idle";
  const quiet = !live && lastTs !== null ? now - lastTs : null;

  return (
    <DeckCard
      icon={ScrollText}
      title={t("deck.card_log")}
      meta={journal.length > 0 ? journal.length : undefined}
      live={live}
      variant="rail"
      onOpen={() => setActiveSection("sessions")}
      openLabel={t("deck.log_open_section")}
      className={className}
      bodyClassName="p-0"
    >
      <div
        ref={bodyRef}
        onScroll={onScroll}
        className="h-full overflow-y-auto px-2.5 pb-2 font-mono text-[10.5px] leading-[1.5]"
      >
        {shown.map((line) => (
          <LogRow key={line.id} line={line} t={t} />
        ))}
        {/* The cursor: never empty. Wraps rather than truncates — this is the
            one line that must always be readable whole. */}
        <div className="mt-1 flex items-baseline gap-2 text-[10.5px]">
          <span className="shrink-0 tabular-nums text-muted-foreground/70">{fmtClock(now)}</span>
          <span
            className={cn(
              "inline-block h-[1.1em] w-[0.55em] shrink-0 self-center",
              live ? "bg-foreground/70" : "bg-foreground/60 motion-safe:animate-pulse",
            )}
            aria-hidden
          />
          <span className="min-w-0 flex-1 whitespace-normal break-words">
            <span className={live ? "text-primary" : "text-foreground/80"}>{t(nowKey)}</span>
            {quiet !== null && quiet > 5_000 && (
              <span className="text-muted-foreground/70">
                {" · "}
                {t("deck.log_quiet_for").replace("{0}", fmtQuiet(quiet))}
              </span>
            )}
          </span>
        </div>
      </div>
    </DeckCard>
  );
}

function LogRow({ line, t }: { line: JournalLine; t: (key: string) => string }) {
  const label = line.labelKey ? fill(t(line.labelKey), line.args) : "";
  const failed = line.ok === false;
  const speech = SPEECH.has(line.kind) && Boolean(line.text);
  return (
    <div
      className={cn(
        "flex items-baseline gap-2",
        failed && "text-destructive",
        // A sub-note (first token, first audio) hangs under its parent line.
        line.kind === "note" && !line.text && line.ms !== undefined && "opacity-80",
      )}
    >
      <span className="shrink-0 tabular-nums text-muted-foreground/70">{fmtClock(line.ts)}</span>
      <span
        className={cn(
          "w-[6ch] shrink-0 uppercase tracking-wider",
          failed ? "text-destructive" : TAG_TONE[line.kind],
          line.open && "motion-safe:animate-pulse",
        )}
      >
        {t(`deck.log_kind_${line.kind}`)}
      </span>
      {/* Wraps like a terminal does — a transcript cut to "wie spät…" would
          hide the one thing the line is for. Long lines cost height, and the
          card has that. */}
      <span className="min-w-0 flex-1 whitespace-normal break-words [overflow-wrap:anywhere]">
        {label && <span className={cn(line.text ? "text-muted-foreground" : "text-foreground/90")}>{label}</span>}
        {label && line.text && " "}
        {line.text && (
          <span className={cn(speech ? "text-foreground" : "text-foreground/90")}>
            {speech ? `“${line.text}”` : line.text}
          </span>
        )}
        {line.open ? (
          <span className="text-primary" aria-hidden>
            {" …"}
          </span>
        ) : (
          line.ms !== undefined && (
            <span className={cn("tabular-nums", failed ? "text-destructive" : "text-muted-foreground")}>
              {" · "}
              {fmtMs(line.ms)}
            </span>
          )
        )}
      </span>
    </div>
  );
}
