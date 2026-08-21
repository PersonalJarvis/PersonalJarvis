import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Camera, ChevronUp, FolderOpen, Gauge, MessagesSquare, Terminal, type LucideIcon } from "lucide-react";
import { useEventStore, type SectionId } from "@/store/events";
import { useDeckStore } from "@/store/deck";
import { useCommandActivityStore } from "@/store/commandActivity";
import { useRuns } from "@/hooks/useRuns";
import { useOutputsList } from "@/hooks/useOutputs";
import type { IdeState } from "@/lib/agenticIdeApi";
import { boardAtRest } from "@/lib/deckRest";
import { CAPTURE_AFTERGLOW_MS } from "@/components/deck/DeckSignalCards";
import { HudFrameOverlay, useElementSize } from "@/components/deck/HudFrame";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

const fetchIdeState = async (): Promise<IdeState> =>
  (await import("@/lib/agenticIdeApi")).fetchIdeState();

/**
 * The board's bottom row when nothing is happening — five instruments at
 * rest, on one strip.
 *
 * The rule that picks this form over the row of cards lives in
 * `lib/deckRest.ts`; this is what it looks like. Each segment prints the same
 * three things: what the instrument is, the FIGURE that says how big it is
 * (runs ever recorded, outputs on disk, panes in the workspace) and one dim
 * line of context — the last time it did something, the project it is
 * pointed at. A gauge reading zero still shows its dial; "No outputs yet."
 * showed neither dial nor needle and took a card-sized hole to do it.
 *
 * Every segment is the same jump into its section that the card's title was,
 * so nothing becomes unreachable by collapsing — and the chevron at the end
 * brings the full row straight back.
 *
 * Colours are theme tokens only: this reads the same on the light stage.
 */

interface RestSegment {
  key: string;
  icon: LucideIcon;
  label: string;
  /** How big this instrument is — a count, or an em dash when there is none. */
  value: string;
  /** One dim line of context under the figure. */
  note: string;
  section?: SectionId;
}

function fmtClock(ms: number | null | undefined): string {
  if (!ms) return "";
  return new Date(ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/** Output timestamps come from the backend in seconds. */
function outputClock(seconds: number | null | undefined): string {
  return seconds ? fmtClock(seconds * 1000) : "";
}

const DASH = "—";

/**
 * Reads the five instruments once, for both questions the board asks: is it
 * at rest, and what do the segments say. The queries are the very ones the
 * cards use, by the same keys, so this costs no extra request.
 */
export function useDeckRest(enabled = true): { atRest: boolean; segments: RestSegment[] } {
  const t = useT();
  const outputs = useOutputsList();
  const runs = useRuns();
  const captures = useDeckStore((s) => s.captures);
  const capture = useDeckStore((s) => s.capture);
  const termLines = useDeckStore((s) => s.termLines);
  const shell = useCommandActivityStore((s) => s.entries);
  // Off until the board is actually up: nothing heavy — and nothing polling
  // every five seconds — belongs on the boot's critical path, and this is the
  // one request the cards were not already going to make.
  const ide = useQuery<IdeState>({
    queryKey: ["deck", "ide-state"],
    queryFn: fetchIdeState,
    refetchInterval: 5_000,
    retry: false,
    enabled,
  });

  const outputRows = outputs.data ?? [];
  const runRows = runs.data ?? [];
  const panes = ide.data?.session?.terminals ?? [];
  const shellRunning = shell.filter((e) => e.status === "running").length;

  // The capture card shows a picture only inside its afterglow; past that it
  // is a ledger, and a ledger is history like any other. Nothing re-renders
  // when an afterglow simply runs out, so the deadline gets its own wake-up:
  // one timeout per capture, and the row settles the moment the picture goes.
  const captureTs = capture?.ts ?? 0;
  const [afterglowOver, setAfterglowOver] = useState(false);
  useEffect(() => {
    if (!captureTs) return;
    const left = captureTs + CAPTURE_AFTERGLOW_MS - Date.now();
    if (left <= 0) {
      setAfterglowOver(true);
      return;
    }
    setAfterglowOver(false);
    const id = window.setTimeout(() => setAfterglowOver(true), left);
    return () => window.clearTimeout(id);
  }, [captureTs]);
  const captureShowing = captureTs > 0 && !afterglowOver;

  const atRest = boardAtRest({
    runningOutputs: outputRows.filter((o) => o.status === "running").length,
    liveRuns: runRows.filter((r) => r.ended_ms === null).length,
    shellRunning,
    termLines: termLines.length,
    idePanes: panes.length,
    captureShowing,
  });

  const outputData = outputs.data;
  const runData = runs.data;
  const wsData = ide.data?.workspaces;
  const shellCount = shell.length;
  const segments = useMemo<RestSegment[]>(() => {
    const outs = outputData ?? [];
    const rns = runData ?? [];
    const wss = wsData ?? [];
    const lastOutput = outs[0];
    const lastRun = rns[0];
    const activeWs = wss.find((w) => w.active) ?? wss[0];
    const lastAt = (clock: string) => t("deck.rest_last").replace("{0}", clock);
    return [
      {
        key: "outputs",
        icon: FolderOpen,
        label: t("deck.card_outputs"),
        value: outputData ? String(outs.length) : DASH,
        note: lastOutput
          ? [
              outputClock(lastOutput.completed_at ?? lastOutput.started_at),
              lastOutput.utterance || lastOutput.summary || lastOutput.slug,
            ]
              .filter(Boolean)
              .join(" · ")
          : t("deck.rest_none_yet"),
        section: "outputs",
      },
      {
        key: "runs",
        icon: Gauge,
        label: t("deck.card_runs"),
        value: runData ? String(rns.length) : DASH,
        note: lastRun ? lastAt(fmtClock(lastRun.started_ms)) : t("deck.rest_none_yet"),
        section: "run_inspector",
      },
      {
        key: "capture",
        icon: Camera,
        label: t("deck.card_shot"),
        value: captures.length > 0 ? String(captures.length) : DASH,
        note: captures[0] ? lastAt(fmtClock(captures[0].ts)) : t("deck.rest_none_yet"),
      },
      {
        key: "terminals",
        icon: Terminal,
        label: t("deck.card_terminals"),
        value: String(shellCount),
        note: t("deck.rest_idle"),
        section: "clis",
      },
      {
        key: "workspace",
        icon: MessagesSquare,
        label: t("deck.card_ide"),
        value: wss.length > 0 ? String(wss.length) : DASH,
        note: activeWs
          ? [activeWs.name, activeWs.branch ? `⎇ ${activeWs.branch}` : ""].filter(Boolean).join(" ")
          : t("deck.rest_no_workspace"),
        section: "agentic-ide",
      },
    ];
  }, [t, outputData, runData, wsData, captures, shellCount]);

  return { atRest, segments };
}

export function DeckRestStrip({
  segments,
  onExpand,
  className,
}: {
  segments: RestSegment[];
  /** Bring the full row of cards back. */
  onExpand: () => void;
  className?: string;
}) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const [ref, size] = useElementSize<HTMLElement>();

  return (
    <section ref={ref} className={cn("group/card relative flex items-stretch", className)}>
      {/* Chamfer: the deck's frame for a READOUT (HudFrame.tsx). The strip is
          five readings on one plate, not a stream and not a picture. */}
      <HudFrameOverlay variant="chamfer" w={size.w} h={size.h} />

      {/* Five across on the deck's own width; two or three up when the stage
          is narrow, so a reading is never squeezed to its first digit. */}
      <div className="relative grid min-w-0 flex-1 grid-cols-2 items-stretch sm:grid-cols-3 lg:grid-cols-5">
        {segments.map((seg) => {
          const Icon = seg.icon;
          const section = seg.section;
          // No status lamp here on purpose: five unlit dots in a row are
          // clutter, and "at rest" is the whole premise of the strip. The
          // icon carries which instrument this is.
          const body = (
            <>
              <Icon className="h-3 w-3 shrink-0 text-muted-foreground transition-colors group-hover/seg:text-primary" />
              <span className="flex min-w-0 flex-col gap-0.5 leading-none">
                <span className="truncate font-mono text-[9px] uppercase tracking-[0.2em] text-foreground/70 transition-colors group-hover/seg:text-primary">
                  {seg.label}
                </span>
                <span className="flex min-w-0 items-baseline gap-1.5">
                  <span className="shrink-0 font-mono text-[15px] tabular-nums leading-none text-foreground">
                    {seg.value}
                  </span>
                  <span className="min-w-0 truncate font-mono text-[9.5px] text-muted-foreground">{seg.note}</span>
                </span>
              </span>
            </>
          );
          const shared =
            "group/seg flex min-w-0 items-center gap-1.5 border-l border-border/50 px-3 py-1.5 text-left first:border-l-0";
          return section ? (
            <button
              key={seg.key}
              type="button"
              onClick={() => setActiveSection(section)}
              title={t("deck.open_section")}
              className={cn(
                shared,
                "transition-colors hover:bg-primary/5",
                "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/60",
              )}
            >
              {body}
            </button>
          ) : (
            <div key={seg.key} className={shared}>
              {body}
            </div>
          );
        })}
      </div>

      <button
        type="button"
        onClick={onExpand}
        title={t("deck.rest_expand")}
        aria-label={t("deck.rest_expand")}
        className={cn(
          "relative flex w-8 shrink-0 items-center justify-center border-l border-border/50 text-muted-foreground",
          "transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary/60",
        )}
      >
        <ChevronUp className="h-3.5 w-3.5" />
      </button>
    </section>
  );
}
