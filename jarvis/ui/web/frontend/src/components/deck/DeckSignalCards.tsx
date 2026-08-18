import { useEffect, useMemo, useState } from "react";
import { Camera, Coins, MousePointer2 } from "lucide-react";
import { useEventStore } from "@/store/events";
import { useDeckStore } from "@/store/deck";
import { countWords } from "@/lib/deckState";
import { DeckCard } from "@/components/deck/DeckCard";
import { HudGauge, HudLamp } from "@/components/deck/HudFrame";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * The deck's signal cards — the pictures and the numbers.
 *
 * Both picture cards read the images through the deck routes added for them
 * (`/api/deck/frame`, `/api/deck/cu-frame/{sha}`) and nothing else. The
 * numbers are the ones the bus actually publishes: `BrainTurnCompleted`
 * carries tokens and cost, `TranscriptFinal` the words. No estimate anywhere.
 */

// ----------------------------------------------------------------------
// Computer Use — what the assistant is doing on the screen
// ----------------------------------------------------------------------

const CU_PHASES = ["observe", "uia", "plan", "think", "act", "verify"] as const;

export function ComputerUseCard({ className }: { className?: string }) {
  const t = useT();
  const cu = useDeckStore((s) => s.cu);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const src = cu.lastFrameSha ? `/api/deck/cu-frame/${cu.lastFrameSha}` : null;
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [src]);

  return (
    <DeckCard
      icon={MousePointer2}
      title={t("deck.card_cu")}
      meta={cu.active ? `#${cu.stepIdx}` : cu.frames > 0 ? t("deck.cu_done") : undefined}
      live={cu.active}
      variant="bracket"
      onOpen={() => setActiveSection("run_inspector")}
      openLabel={t("deck.open_section")}
      className={className}
      bodyClassName="p-0"
    >
      <div className="relative flex h-full min-h-[6rem] flex-col">
        {/* The frame the harness last looked at. Object-contain: the whole
            screen matters, not a crop of it. */}
        <div className="relative min-h-0 flex-1 overflow-hidden bg-black/20">
          {src && !broken ? (
            <img
              src={src}
              alt=""
              onError={() => setBroken(true)}
              className={cn("h-full w-full object-contain", !cu.active && "opacity-70")}
            />
          ) : (
            <div className="flex h-full items-center justify-center px-3 text-center text-[11px] text-muted-foreground">
              {cu.active ? t("deck.cu_waiting_frame") : t("deck.cu_idle")}
            </div>
          )}
          {cu.active && (
            <span className="absolute left-2 top-2 flex items-center gap-1 rounded-sm bg-background/80 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-primary">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" aria-hidden />
              live
            </span>
          )}
        </div>

        {/* Phase strip: observe → plan → act → verify. */}
        <div className="flex items-stretch gap-px border-t border-border/60 px-2 py-1">
          {CU_PHASES.map((ph, i) => {
            const on = cu.active && cu.phase === ph;
            const passed = cu.active && (CU_PHASES as readonly string[]).indexOf(cu.phase) > i;
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
                    on ? "bg-primary shadow-[0_0_6px_hsl(var(--primary)/0.7)]" : passed ? "bg-primary/40" : "bg-border",
                  )}
                  style={{ clipPath: "polygon(0 0, calc(100% - 3px) 0, 100% 100%, 3px 100%)" }}
                  aria-hidden
                />
                <span className="truncate">{ph}</span>
              </span>
            );
          })}
        </div>
        {(cu.lastActionKind || cu.windowTitle) && (
          <div className="flex items-center gap-2 border-t border-border/60 px-2 py-1 font-mono text-[10px]">
            {cu.lastActionKind && (
              <span
                className={cn(
                  "shrink-0 rounded-sm px-1 py-px uppercase",
                  cu.lastActionOk === false
                    ? "bg-destructive text-white"
                    : "bg-primary text-primary-foreground",
                )}
              >
                {cu.lastActionKind}
              </span>
            )}
            <span className="min-w-0 flex-1 truncate text-muted-foreground">
              {cu.lastTargetHint || cu.windowTitle}
            </span>
          </div>
        )}
      </div>
    </DeckCard>
  );
}

// ----------------------------------------------------------------------
// App-Shot — the picture Screen Context just took
// ----------------------------------------------------------------------

/**
 * Shows the last one-shot capture for as long as the backend keeps it — the
 * mirror has a TTL, and when the image 404s the card goes quiet again. The
 * URL carries the sequence so a NEW capture is a new request; the browser
 * would otherwise happily show the previous picture from memory.
 */
export function AppShotCard({ className }: { className?: string }) {
  const t = useT();
  const capture = useDeckStore((s) => s.capture);
  const [gone, setGone] = useState(false);
  const src = capture ? `/api/deck/frame?s=${capture.seq}` : null;

  useEffect(() => {
    setGone(false);
    if (!capture) return;
    // Re-check well within the default 120 s budget so an expired preview
    // does not linger as a stale <img>.
    const id = window.setInterval(async () => {
      try {
        const res = await fetch("/api/deck/frame/meta", { cache: "no-store" });
        const meta = (await res.json()) as { available?: boolean };
        if (!meta.available) setGone(true);
      } catch {
        /* the deck must not care */
      }
    }, 15_000);
    return () => window.clearInterval(id);
  }, [capture]);

  const fresh = capture && !gone;

  return (
    <DeckCard
      icon={Camera}
      title={t("deck.card_shot")}
      meta={fresh && capture ? `${capture.width}×${capture.height}` : undefined}
      live={Boolean(fresh)}
      variant="bracket"
      className={className}
      bodyClassName="p-0"
    >
      <div className="relative h-full min-h-[5rem] overflow-hidden bg-black/20">
        {fresh && src ? (
          <>
            <img
              key={src}
              src={src}
              alt=""
              onError={() => setGone(true)}
              className="h-full w-full object-contain"
            />
            <div className="absolute bottom-0 left-0 right-0 flex items-center gap-2 bg-background/75 px-2 py-0.5 font-mono text-[9px] text-muted-foreground">
              <span className="min-w-0 flex-1 truncate">{capture?.targetLabel || capture?.targetKind}</span>
              {capture && capture.redactions > 0 && (
                <span className="shrink-0 text-primary">
                  {t("deck.shot_redacted").replace("{0}", String(capture.redactions))}
                </span>
              )}
            </div>
          </>
        ) : (
          <div className="flex h-full items-center justify-center px-3 text-center text-[11px] text-muted-foreground">
            {t("deck.shot_empty")}
          </div>
        )}
      </div>
    </DeckCard>
  );
}

// ----------------------------------------------------------------------
// API stats — what this session cost
// ----------------------------------------------------------------------

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 10_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function fmtUsd(n: number): string {
  if (n === 0) return "$0";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
}

export function ApiStatsCard({ className }: { className?: string }) {
  const t = useT();
  const usage = useDeckStore((s) => s.usage);
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const models = useMemo(
    () => Object.entries(usage.byModel).sort((a, b) => b[1].costUsd - a[1].costUsd || b[1].turns - a[1].turns).slice(0, 3),
    [usage.byModel],
  );
  const total = usage.tokensIn + usage.tokensOut;
  // Gauge scales are honest and self-referential: the token gauge shows the
  // OUTPUT share of everything sent and received (a real ratio), the cost
  // gauge shows this session against one US dollar — a fixed, stated scale,
  // not a budget the app pretends to know.
  const outShare = total > 0 ? usage.tokensOut / total : 0;
  const costOfDollar = Math.min(1, usage.costUsd / 1);

  return (
    <DeckCard
      icon={Coins}
      title={t("deck.card_api")}
      meta={usage.turns > 0 ? `${usage.turns} ${t("deck.turns")}` : undefined}
      live={usage.lastTurnTs !== null && Date.now() - usage.lastTurnTs < 15_000}
      variant="chamfer"
      onOpen={() => setActiveSection("apikeys")}
      openLabel={t("deck.open_section")}
      className={className}
    >
      {usage.turns === 0 ? (
        <p className="text-[11px] text-muted-foreground">{t("deck.api_empty")}</p>
      ) : (
        <div className="flex h-full min-h-0 items-center gap-3">
          <HudGauge value={outShare} size={62} label={t("deck.api_out")} readout={fmtTokens(total)} />
          <HudGauge value={costOfDollar} size={62} label={t("deck.api_cost")} readout={fmtUsd(usage.costUsd)} />
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <div className="grid grid-cols-2 gap-x-3">
              <Stat label={t("deck.api_in")} value={fmtTokens(usage.tokensIn)} />
              <Stat label={t("deck.api_out")} value={fmtTokens(usage.tokensOut)} />
            </div>
            {models.length > 0 && (
              <ul className="space-y-0.5 border-t border-border/60 pt-1">
                {models.map(([name, m]) => (
                  <li key={name} className="flex items-center gap-2 font-mono text-[9.5px]">
                    <HudLamp on={name === usage.lastModel} />
                    <span className="min-w-0 flex-1 truncate text-foreground">{name}</span>
                    <span className="shrink-0 tabular-nums text-muted-foreground">{m.turns}×</span>
                    <span className="shrink-0 tabular-nums text-primary">{fmtUsd(m.costUsd)}</span>
                  </li>
                ))}
              </ul>
            )}
            {usage.lastCacheHit && (
              <span className="font-mono text-[9px] uppercase tracking-wider text-emerald-400">
                {t("deck.api_cache_hit")}
              </span>
            )}
          </div>
        </div>
      )}
    </DeckCard>
  );
}

function Stat({ label, value, hot }: { label: string; value: string; hot?: boolean }) {
  return (
    <div className="flex flex-col">
      <span className="font-mono text-[9px] uppercase tracking-[0.14em] text-muted-foreground">{label}</span>
      <span className={cn("font-mono text-sm tabular-nums", hot ? "text-primary" : "text-foreground")}>{value}</span>
    </div>
  );
}

// ----------------------------------------------------------------------
// Live counter — words, as they are spoken
// ----------------------------------------------------------------------

/**
 * The big number is the live transcript's word count and moves with every
 * partial the recogniser sends — that is the "in the millisecond" the
 * maintainer asked for. Underneath: the session total (final transcripts)
 * and today's dictation words from the dictation statistics.
 */
export function LiveCounter({ className }: { className?: string }) {
  const t = useT();
  const transcription = useEventStore((s) => s.transcription);
  const transcriptionFinal = useEventStore((s) => s.transcriptionFinal);
  const voiceState = useEventStore((s) => s.voiceState);
  const wordsSession = useDeckStore((s) => s.wordsSession);
  const utterances = useDeckStore((s) => s.utterances);
  const [today, setToday] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch("/api/dictation/stats");
        if (!res.ok) return;
        const data = (await res.json()) as { today?: { words?: number } };
        if (alive && typeof data.today?.words === "number") setToday(data.today.words);
      } catch {
        /* the strip just stays away */
      }
    };
    void load();
    const id = window.setInterval(load, 60_000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [utterances]);

  const live = !transcriptionFinal && voiceState === "listening" ? countWords(transcription) : 0;
  const listening = voiceState === "listening";

  return (
    <div
      className={cn("relative flex items-baseline gap-2.5 border border-border/70 px-3 py-1", className)}
      style={{ clipPath: "polygon(8px 0, 100% 0, 100% calc(100% - 8px), calc(100% - 8px) 100%, 0 100%, 0 8px)" }}
    >
      <HudLamp on={listening} className="self-center" />
      <span
        className={cn(
          "font-mono text-2xl font-semibold tabular-nums leading-none transition-colors",
          listening ? "text-primary" : "text-foreground",
        )}
        aria-live="polite"
      >
        {listening ? live : wordsSession}
      </span>
      <span className="font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground">
        {listening ? t("deck.words_live") : t("deck.words_session")}
      </span>
      {today !== null && (
        <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
          · {t("deck.words_today").replace("{0}", String(today))}
        </span>
      )}
    </div>
  );
}
