import { useEffect, useState } from "react";
import { Timer } from "lucide-react";
import { useEventStore } from "@/store/events";
import { useDeckStore } from "@/store/deck";
import type { TurnPhase } from "@/lib/deckState";
import { DeckCard } from "@/components/deck/DeckCard";
import { HudLamp } from "@/components/deck/HudFrame";
import { fmtMs } from "@/components/deck/DeckLogCard";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * The response instrument — how the answer is coming about, and how long
 * each stage takes.
 *
 * Replaces the live screen mirror on the front page (maintainer decision
 * 2026-08-18: not a live feed, a display that shows the process and its
 * timing). Four phases — hear, think, act, speak — light up as the turn moves
 * through them; the big figure is the clock since the request was complete,
 * running while the turn is open and frozen when it ends. Next to it the
 * marks that matter for a voice assistant, all measured from the same
 * anchor: when the transcript was final, when the brain's first token came,
 * when the first audio was audible. Between turns the card keeps the LAST
 * turn's figures, so after the first answer it always has something real.
 *
 * Every number is the backend's own (`deckState.reduceTurn`): latency marks
 * from the latency tracker, tokens from `BrainTurnCompleted`. Nothing here is
 * estimated for show.
 */

const PHASES: readonly TurnPhase[] = ["hear", "think", "act", "speak"];

/** A turn nobody has touched for this long is over in every way but the event. */
const QUIET_AFTER_MS = 90_000;

export function TurnCard({ className }: { className?: string }) {
  const t = useT();
  const turn = useDeckStore((s) => s.turn);
  const cuActive = useDeckStore((s) => s.cu.active);
  const setActiveSection = useEventStore((s) => s.setActiveSection);

  const live = turn.phase !== "idle";
  // The stopwatch ticks only while a turn is open — ten times a second, which
  // is what a person reads as "running" without the digits blurring.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 100);
    return () => window.clearInterval(id);
  }, [live]);

  const quiet = live && turn.lastEventTs !== null && now - turn.lastEventTs > QUIET_AFTER_MS;
  const clockEnd = live ? (quiet ? (turn.lastEventTs ?? now) : now) : (turn.endedTs ?? turn.lastEventTs ?? 0);
  const elapsed = turn.anchorTs !== null ? Math.max(0, clockEnd - turn.anchorTs) : null;
  const phaseIdx = PHASES.indexOf(turn.phase);

  return (
    <DeckCard
      icon={Timer}
      title={t("deck.card_turn")}
      meta={
        turn.index > 0
          ? `#${turn.index} · ${live ? (quiet ? t("deck.turn_quiet") : t("deck.turn_live")) : t("deck.turn_last")}`
          : undefined
      }
      live={live && !quiet}
      variant="chamfer"
      onOpen={() => setActiveSection("run_inspector")}
      openLabel={t("deck.open_section")}
      className={className}
      bodyClassName="p-0"
    >
      {turn.index === 0 ? (
        <p className="px-2.5 py-1 text-[11px] text-muted-foreground">{t("deck.turn_empty")}</p>
      ) : (
        <div className={cn("flex h-full min-h-0 flex-col", quiet && "opacity-60")}>
          {/* Phase strip: hear → think → act → speak. */}
          <div className="flex items-stretch gap-px px-2.5 pt-0.5">
            {PHASES.map((ph, i) => {
              const on = live && turn.phase === ph;
              const passed = live ? phaseIdx > i : true;
              return (
                <span
                  key={ph}
                  className={cn(
                    "flex flex-1 flex-col items-center gap-0.5 font-mono text-[8px] uppercase tracking-wider",
                    on ? "text-primary" : "text-muted-foreground/70",
                  )}
                >
                  <span
                    className={cn(
                      "h-1 w-full",
                      on
                        ? "bg-primary shadow-[0_0_6px_hsl(var(--primary)/0.7)]"
                        : passed
                          ? "bg-primary/40"
                          : "bg-border",
                    )}
                    style={{ clipPath: "polygon(0 0, calc(100% - 3px) 0, 100% 100%, 3px 100%)" }}
                    aria-hidden
                  />
                  <span className="truncate">{t(`deck.turn_phase_${ph}`)}</span>
                </span>
              );
            })}
          </div>

          {/* The clock and the marks. */}
          <div className="flex min-h-0 flex-1 items-center gap-3 px-2.5 py-1">
            <div className="flex shrink-0 flex-col">
              <span
                className={cn(
                  "font-mono text-2xl font-semibold tabular-nums leading-none",
                  live && !quiet ? "text-primary" : "text-foreground",
                )}
              >
                {elapsed === null ? "—" : fmtMs(elapsed)}
              </span>
              <span className="mt-1 max-w-[12ch] truncate font-mono text-[8px] uppercase leading-tight tracking-[0.14em] text-muted-foreground">
                {turn.anchorTs === null ? t("deck.turn_phase_hear") : t("deck.turn_since_anchor")}
              </span>
            </div>
            <ul className="flex min-w-0 flex-1 flex-col gap-px font-mono text-[9.5px]">
              <Mark label={t("deck.turn_stt")} ms={turn.sttMs} />
              {turn.ackMs !== null && <Mark label={t("deck.turn_ack")} ms={turn.ackMs} hot />}
              <Mark label={t("deck.turn_first_token")} ms={turn.ttftMs} />
              <Mark label={t("deck.turn_first_audio")} ms={turn.firstAudioMs} hot />
            </ul>
          </div>

          {/* The bottom line: who answered, and with what. */}
          <div className="flex items-center gap-2 overflow-hidden border-t border-border/60 px-2.5 py-1 font-mono text-[9.5px] text-muted-foreground">
            <HudLamp on={live && !quiet} />
            <span className="min-w-0 flex-1 truncate">
              {[
                turn.model || turn.provider,
                turn.attempts > 1 ? `${turn.attempts} ${t("deck.turn_attempts")}` : "",
                turn.tools > 0
                  ? `${turn.tools}${turn.toolsFailed > 0 ? `(${turn.toolsFailed}✕)` : ""} ${t("deck.turn_tools")}`
                  : "",
                turn.cu || cuActive ? t("deck.turn_screen") : "",
                turn.words > 0 ? `${turn.words} ${t("deck.turn_words")}` : "",
                turn.brainMs !== null ? `${t("deck.turn_brain")} ${fmtMs(turn.brainMs)}` : "",
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
            {turn.cacheHit && (
              <span className="shrink-0 uppercase tracking-wider text-emerald-400">{t("deck.api_cache_hit")}</span>
            )}
          </div>
        </div>
      )}
    </DeckCard>
  );
}

function Mark({ label, ms, hot }: { label: string; ms: number | null; hot?: boolean }) {
  return (
    <li className="flex items-baseline gap-2">
      <span className="min-w-0 flex-1 truncate uppercase tracking-wider text-muted-foreground/80">{label}</span>
      <span
        className={cn(
          "shrink-0 tabular-nums",
          ms === null ? "text-muted-foreground/50" : hot ? "text-primary" : "text-foreground",
        )}
      >
        {ms === null ? "—" : fmtMs(ms)}
      </span>
    </li>
  );
}
