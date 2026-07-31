/**
 * The corpus itself, drawn at true scale — the one thing this screen is meant
 * to be remembered by.
 *
 * What it replaces: a row of five raw bucket counts ("Captured 0 ·
 * Keyword-searchable 0 · Embedded 3237 · Distilled 1475 · Failed 0"). Those
 * were the internal state machine printed verbatim, and because an item sits
 * in exactly one bucket, the numbers appeared to move BACKWARDS as work
 * progressed. Nobody could see from them that the store was two-thirds
 * unfinished.
 *
 * Here the whole bar is the corpus and each band is a real share of it, so
 * "most of it is still being summarised" is something you see before you read
 * anything. The bands are ordered by how finished the material is, which is
 * also the order the pipeline moves it in — left is done, right is not.
 *
 * The hatch carries a claim, so it obeys one: it only MOVES while the backend
 * genuinely reports `processing`. A queue that is standing still shows a
 * static hatch, because an animation that plays over a stalled pipeline is
 * the same lie in motion that this whole redesign exists to remove.
 */
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { Num } from "@/components/ultrawiki/overview/primitives";
import type { UltraWikiProgress } from "@/lib/ultrawikiApi";

interface Band {
  key: "summarised" | "usable" | "arriving" | "failed";
  count: number;
  /** Unfinished material carries the hatch; finished material does not. */
  working: boolean;
  className: string;
  dotClassName: string;
}

/**
 * Split the canonical numbers into disjoint bands.
 *
 * Subtraction only, never re-addition of buckets: every input comes from the
 * one progress model, so the bands are guaranteed to sum to the corpus.
 */
export function bandsOf(progress: UltraWikiProgress): Band[] {
  const summarised = Math.max(0, progress.summarised);
  const failed = Math.max(0, progress.failed);
  const usable = Math.max(0, progress.searchable - summarised);
  const arriving = Math.max(
    0,
    progress.total - summarised - failed - usable,
  );
  return [
    {
      key: "summarised",
      count: summarised,
      working: false,
      className: "bg-[#5bd4a4]",
      dotClassName: "bg-[#5bd4a4]",
    },
    {
      key: "usable",
      count: usable,
      working: true,
      className: "bg-[#5bd4a4]/35",
      dotClassName: "bg-[#5bd4a4]/35",
    },
    {
      key: "arriving",
      count: arriving,
      working: true,
      className: "bg-muted-foreground/25",
      dotClassName: "bg-muted-foreground/40",
    },
    {
      key: "failed",
      count: failed,
      working: false,
      className: "bg-destructive/70",
      dotClassName: "bg-destructive/70",
    },
  ];
}

export function IntakeBar({
  progress,
  running,
}: {
  progress: UltraWikiProgress;
  /** The backend's own verdict that work is moving right now. */
  running: boolean;
}): JSX.Element {
  const t = useT();
  const bands = bandsOf(progress).filter((band) => band.count > 0);
  const total = Math.max(1, progress.total);

  return (
    <div data-testid="ultrawiki-intake-bar">
      <div
        className="flex h-2.5 w-full overflow-hidden rounded-full bg-muted/40"
        aria-hidden
      >
        {bands.map((band) => (
          <div
            key={band.key}
            data-band={band.key}
            data-working={band.working && running ? "true" : "false"}
            className={cn(
              "h-full",
              band.className,
              // A band only animates when there is genuinely something to
              // animate about; `uw-hatch` degrades to a static texture under
              // prefers-reduced-motion.
              band.working && "uw-hatch",
              band.working && running && "uw-hatch-live",
            )}
            style={{
              // A single failed item out of 4 712 is still worth a pixel.
              width: `${(band.count / total) * 100}%`,
              minWidth: "3px",
            }}
          />
        ))}
      </div>

      <dl className="mt-2.5 flex flex-wrap items-center gap-x-5 gap-y-1.5">
        {bands.map((band) => (
          <div
            key={band.key}
            className="flex items-baseline gap-1.5"
            data-testid={`ultrawiki-intake-legend-${band.key}`}
          >
            <span
              className={cn("h-1.5 w-1.5 shrink-0 rounded-full", band.dotClassName)}
              aria-hidden
            />
            <dt className="text-xs text-muted-foreground">
              {t(`ultrawiki.overview.band_${band.key}`)}
            </dt>
            <dd className="text-xs font-medium text-foreground">
              <Num value={band.count} />
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
