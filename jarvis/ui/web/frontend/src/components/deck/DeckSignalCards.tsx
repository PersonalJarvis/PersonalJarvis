import { useEffect, useMemo, useState } from "react";
import { Camera, Coins } from "lucide-react";
import { useEventStore } from "@/store/events";
import { useDeckStore } from "@/store/deck";
import { countWords, type CaptureState } from "@/lib/deckState";
import { DeckCard } from "@/components/deck/DeckCard";
import { HudGauge, HudLamp } from "@/components/deck/HudFrame";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * The deck's signal cards — the picture and the numbers.
 *
 * The capture card reads its image through the deck route added for it
 * (`/api/deck/frame`) and nothing else. The numbers are the ones the bus
 * actually publishes: `BrainTurnCompleted` carries tokens and cost,
 * `TranscriptFinal` the words. No estimate anywhere.
 */

// ----------------------------------------------------------------------
// Capture — the picture Screen Context just took, briefly, then the ledger
// ----------------------------------------------------------------------

/**
 * How long a new capture stays on the front page before it fades. Long
 * enough to see what was looked at, short enough that a picture of the
 * screen never sits there as furniture (maintainer complaint 2026-08-18: the
 * shot used to stay until the next one replaced it). The backend keeps the
 * bytes for its own budget (`deck_preview_s`); this is only how long the
 * deck SHOWS them.
 */
export const CAPTURE_AFTERGLOW_MS = 20_000;

/**
 * The receipt event and the mirrored bytes are two different messages, and a
 * fetch that lands between them gets a 404. The service now mirrors first,
 * but a picture must not depend on that ordering forever: retry briefly.
 */
const FRAME_RETRIES = 3;
const FRAME_RETRY_MS = 400;

function fmtClock(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function CaptureCard({ className }: { className?: string }) {
  const t = useT();
  const capture = useDeckStore((s) => s.capture);
  const captures = useDeckStore((s) => s.captures);

  // The afterglow: a fresh capture is shown, a rail drains, then it is gone.
  // `remaining` is only ever read while a picture is up, so the interval runs
  // only then.
  const [remaining, setRemaining] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [gone, setGone] = useState(false);
  useEffect(() => {
    if (!capture) return;
    setGone(false);
    setAttempt(0);
    setRemaining(CAPTURE_AFTERGLOW_MS);
    const started = Date.now();
    const id = window.setInterval(() => {
      const left = CAPTURE_AFTERGLOW_MS - (Date.now() - started);
      setRemaining(Math.max(0, left));
      if (left <= 0) window.clearInterval(id);
    }, 250);
    return () => window.clearInterval(id);
  }, [capture]);

  const showing = Boolean(capture) && !gone && remaining > 0;
  const src = capture ? `/api/deck/frame?s=${capture.seq}&r=${attempt}` : null;

  const onError = () => {
    if (attempt < FRAME_RETRIES) {
      window.setTimeout(() => setAttempt((n) => n + 1), FRAME_RETRY_MS);
    } else {
      setGone(true);
    }
  };

  return (
    <DeckCard
      icon={Camera}
      title={t("deck.card_shot")}
      meta={showing && capture ? `${capture.width}×${capture.height}` : captures.length > 0 ? captures.length : undefined}
      live={showing}
      variant="bracket"
      className={className}
      bodyClassName="p-0"
    >
      <div className="relative h-full min-h-[5rem] overflow-hidden bg-black/20">
        {showing && src ? (
          <>
            <img
              key={src}
              src={src}
              alt=""
              onError={onError}
              className="h-full w-full object-contain"
            />
            <div className="absolute bottom-0 left-0 right-0 flex flex-col bg-background/75">
              <div className="flex items-center gap-2 px-2 py-0.5 font-mono text-[9px] text-muted-foreground">
                <span className="min-w-0 flex-1 truncate">{capture?.targetLabel || capture?.targetKind}</span>
                {capture && capture.redactions > 0 && (
                  <span className="shrink-0 text-primary">
                    {t("deck.shot_redacted").replace("{0}", String(capture.redactions))}
                  </span>
                )}
                <span className="shrink-0 tabular-nums">
                  {t("deck.shot_fades").replace("{0}", `${Math.ceil(remaining / 1000)} s`)}
                </span>
              </div>
              {/* The draining rail: the picture's remaining time, visible. */}
              <div className="h-px w-full bg-border/60">
                <div
                  className="h-px bg-primary transition-[width] duration-200 ease-linear"
                  style={{ width: `${(remaining / CAPTURE_AFTERGLOW_MS) * 100}%` }}
                  aria-hidden
                />
              </div>
            </div>
          </>
        ) : captures.length > 0 ? (
          <CaptureLedger captures={captures} t={t} />
        ) : (
          <div className="flex h-full items-center justify-center px-3 text-center text-[11px] text-muted-foreground">
            {t("deck.shot_empty")}
          </div>
        )}
      </div>
    </DeckCard>
  );
}

/**
 * What was looked at this session — time, target, size, what was redacted.
 * Words only, never pixels: the mirror holds one frame and no history, and
 * the ledger keeps that promise (the labels are the service's own scrubbed
 * ones, the same the receipt event carries).
 */
function CaptureLedger({ captures, t }: { captures: CaptureState[]; t: (key: string) => string }) {
  return (
    <div className="flex h-full flex-col px-2.5 py-1.5">
      <span className="font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground">
        {t("deck.shot_earlier")}
      </span>
      <ul className="mt-1 min-h-0 flex-1 space-y-0.5 overflow-y-auto">
        {captures.map((c) => (
          <li key={c.seq} className="flex items-center gap-2 font-mono text-[10px]">
            <span className="shrink-0 tabular-nums text-muted-foreground">{fmtClock(c.ts)}</span>
            <span className="min-w-0 flex-1 truncate text-foreground">{c.targetLabel || c.targetKind || "—"}</span>
            {c.width > 0 && c.height > 0 && (
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {c.width}×{c.height}
              </span>
            )}
            {c.redactions > 0 && (
              <span className="shrink-0 text-primary">
                {t("deck.shot_redacted").replace("{0}", String(c.redactions))}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
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
