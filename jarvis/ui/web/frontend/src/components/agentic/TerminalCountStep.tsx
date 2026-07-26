/**
 * "How many terminals?" — the wizard step where the shape of the workspace is
 * decided.
 *
 * ## Why this is a picture and not a number
 *
 * The step used to be six square cards of dots plus a separate "Custom
 * Terminals" row. Three things were wrong with it, and only the third is about
 * looks.
 *
 * 1. **It could overflow.** The custom row drew its dots inside a fixed 40×40 px
 *    box with nothing stopping them. Twenty-three terminals in a narrow window
 *    is twelve rows of dots — about 120 px — so the column grew straight out
 *    through the card, over the buttons above and below it.
 * 2. **It made a promise it did not keep.** The dots described an arrangement
 *    that depends on something the user is not thinking about: how wide the
 *    window happens to be right now. Eight terminals are "2 across, 4 down" in a
 *    1050 px window and "4 across, 2 down" maximised. The preview showed the
 *    first and the workspace opened the second, and nothing anywhere said why.
 * 3. **Dots are not the thing.** A dot grid is a diagram OF a workspace. Since
 *    the real arrangement is one function call away (`paneGrid`, the same one
 *    the running grid lays itself out with), the preview can simply BE the
 *    workspace, small — real panes, real call-signs, the real aspect ratio.
 *
 * So: one stage showing the workspace that is about to open, one track to pick
 * the count, and a readout underneath that states the arrangement together with
 * the condition it holds under. A preview that names its own assumption cannot
 * be caught lying by a maximise button.
 *
 * The stage is a fixed-height box whose panes are laid out by a CSS grid with
 * `minmax(0, 1fr)` tracks. Overflow is not clipped away as an afterthought —
 * the container mathematically cannot be exceeded, whatever number is picked.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Minus, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  MAX_PANES_PER_BAND,
  paneGrid,
  paneLines,
  widthForOneBand,
  wizardPanes,
  workspaceBandCapacityFor,
} from "./layout";

/**
 * Counts worth a labelled notch on the track.
 *
 * Not "presets" — the track already covers every number. These are the ones
 * people actually reach for, marked so they are one click rather than a careful
 * drag. Anything above the workspace maximum is filtered out at render time.
 */
const NOTCHES = [1, 2, 3, 4, 6, 8, 12] as const;

/**
 * Shape of the area the panes will actually fill, width ÷ height.
 *
 * The workspace grid sits under a toolbar and above the prompt bar, so it is
 * markedly wider relative to its height than the window is: a maximised 1920 ×
 * 1080 window leaves the grid roughly 1636 × 726.
 *
 * The stage takes its height from this, so a miniature pane has the proportions
 * of the pane it stands for. Without it the stage would be right about the
 * arrangement and wrong about the panes — four across would look like letterbox
 * strips where the real ones are nearly square, which is the sort of small lie
 * that makes a preview feel untrustworthy even when its numbers are correct.
 */
const WORKSPACE_ASPECT = 1636 / 726;

/**
 * Bounds on the stage's height, in px.
 *
 * Safety rails for extreme window widths only — at the wizard's own column
 * width the derived height lands comfortably inside them, so the stage keeps
 * its true proportions and holds still while the count changes.
 */
const STAGE_MIN_HEIGHT_PX = 160;
const STAGE_MAX_HEIGHT_PX = 340;

/** Below this rendered width a pane has no room for its call-sign. */
const PANE_LABEL_MIN_PX = 54;
/** Below this it has no room for anything but a tinted rectangle. */
const PANE_CHROME_MIN_PX = 22;

interface TerminalCountStepProps {
  /** Terminals currently chosen. */
  count: number;
  /** Workspace maximum, from the backend. */
  max: number;
  /** Call-signs the panes will open with, so the stage shows the real names. */
  names: string[];
  /**
   * Width of the slot the workspace will occupy, in px — measured by the view
   * from the element the grid will later fill. The arrangement depends on it,
   * so the stage is told rather than left to guess.
   */
  workspaceWidthPx: number;
  onChange: (count: number) => void;
}

export function TerminalCountStep({
  count,
  max,
  names,
  workspaceWidthPx,
  onChange,
}: TerminalCountStepProps) {
  const perBand = useMemo(
    () => workspaceBandCapacityFor(workspaceWidthPx),
    [workspaceWidthPx],
  );
  const grid = useMemo(
    () => paneGrid(wizardPanes(count), perBand),
    [count, perBand],
  );
  const bands = useMemo(() => paneLines(count, perBand), [count, perBand]);

  const set = (next: number) =>
    onChange(Math.max(1, Math.min(max, Math.trunc(next))));

  return (
    <div className="flex flex-col gap-5">
      <WorkspaceStage
        columns={grid.columns}
        rows={bands}
        count={count}
        names={names}
      />

      <Readout
        columns={grid.columns}
        bands={bands}
        count={count}
        workspaceWidthPx={workspaceWidthPx}
      />

      <CountTrack count={count} max={max} onChange={set} />
    </div>
  );
}

/**
 * The workspace that is about to open, at a scale that fits on this screen.
 *
 * Every pane is placed by `paneGrid` — the function the running workspace uses
 * — over the panes the backend will actually create (`wizardPanes`). The two
 * cannot drift apart without the running grid changing too.
 */
function WorkspaceStage({
  columns,
  rows,
  count,
  names,
}: {
  columns: number;
  rows: number;
  count: number;
  names: string[];
}) {
  /*
   * The stage measures ITSELF rather than being told its size.
   *
   * Its height comes from CSS (`aspect-ratio`), so only the browser knows what
   * it ended up being once the min/max rails are applied — and each pane needs
   * that to decide how much of itself it has room to draw. Four panes get
   * call-signs and output lines; forty get a tinted tile, which is the stage
   * being honest about what forty terminals look like.
   */
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const node = stageRef.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    setSize({ width: node.clientWidth, height: node.clientHeight });
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      setSize({
        width: box?.width ?? node.clientWidth,
        height: box?.height ?? node.clientHeight,
      });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const safeColumns = Math.max(1, columns);
  const safeRows = Math.max(1, rows);
  const paneWidth = size.width > 0 ? size.width / safeColumns : 0;
  const paneHeight = size.height > 0 ? size.height / safeRows : 0;
  // Panes are laid out as a grid of equal cells, so one pane's detail level is
  // every pane's — decided once rather than per tile.
  const detail: PaneDetail =
    paneWidth === 0 || paneWidth >= PANE_LABEL_MIN_PX
      ? "full"
      : paneWidth >= PANE_CHROME_MIN_PX
        ? "chrome"
        : "tile";

  return (
    <div
      data-testid="workspace-stage"
      className="relative overflow-hidden rounded-2xl border border-border bg-background/80"
      style={{
        aspectRatio: `${WORKSPACE_ASPECT}`,
        minHeight: STAGE_MIN_HEIGHT_PX,
        maxHeight: STAGE_MAX_HEIGHT_PX,
      }}
    >
      {/*
        A faint bench grid under the panes. It is not decoration for its own
        sake: it gives the floating panes a surface to sit on, so the stage
        reads as a place rather than as shapes on a background.
      */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-[0.35]"
        style={{
          backgroundImage:
            "linear-gradient(to right, hsl(var(--border)) 1px, transparent 1px)," +
            "linear-gradient(to bottom, hsl(var(--border)) 1px, transparent 1px)",
          backgroundSize: "28px 28px",
          maskImage:
            "radial-gradient(ellipse at center, black 40%, transparent 85%)",
          WebkitMaskImage:
            "radial-gradient(ellipse at center, black 40%, transparent 85%)",
        }}
      />

      <div
        ref={stageRef}
        data-testid="workspace-stage-grid"
        className="relative grid h-full w-full gap-1.5 p-2"
        style={{
          gridTemplateColumns: `repeat(${safeColumns}, minmax(0, 1fr))`,
          gridTemplateRows: `repeat(${safeRows}, minmax(0, 1fr))`,
        }}
      >
        {Array.from({ length: count }).map((_, index) => (
          <StagePane
            key={index}
            name={names[index] ?? `T${index + 1}`}
            index={index}
            detail={detail}
            /* The workspace opens with the first pane selected — the prompt bar
               types into it. Showing that here means the stage is not just the
               right shape, it is the right state. */
            focused={index === 0}
            height={paneHeight}
          />
        ))}
      </div>
    </div>
  );
}

type PaneDetail = "full" | "chrome" | "tile";

/** One terminal, as it will look — title bar, call-sign, and agent output. */
function StagePane({
  name,
  index,
  detail,
  focused,
  height,
}: {
  name: string;
  index: number;
  detail: PaneDetail;
  focused: boolean;
  height: number;
}) {
  // Output lines are only worth drawing where there is height for them to read
  // as lines rather than as a smear.
  const lines = height >= 64 ? 3 : height >= 42 ? 2 : 0;

  return (
    <div
      className={cn(
        "flex min-h-0 min-w-0 flex-col overflow-hidden rounded-md border transition-colors",
        focused
          ? "border-primary/50 bg-primary/[0.09]"
          : "border-primary/20 bg-primary/[0.04]",
      )}
    >
      {detail !== "tile" && (
        <div
          className={cn(
            "flex shrink-0 items-center gap-1.5 border-b px-1.5 py-1",
            focused
              ? "border-primary/30 bg-primary/10"
              : "border-primary/15 bg-primary/[0.05]",
          )}
        >
          <span
            className={cn(
              "h-1.5 w-1.5 shrink-0 rounded-full",
              focused ? "bg-primary" : "bg-primary/40",
            )}
          />
          {detail === "full" && (
            <span
              className={cn(
                "truncate font-mono text-[10px] leading-none",
                focused ? "text-primary" : "text-primary/60",
              )}
            >
              {name}
            </span>
          )}
        </div>
      )}

      {detail === "full" && lines > 0 && (
        <div className="flex min-h-0 flex-1 flex-col justify-start gap-1 p-1.5">
          {Array.from({ length: lines }).map((_, line) => (
            <span
              key={line}
              /*
                Agent output, suggested rather than faked: bars of varying width
                breathing slightly out of step, the way several terminals working
                at once actually look. Deterministic widths and delays, so the
                stage does not reshuffle itself on every keystroke.
              */
              className="h-1 shrink-0 animate-pulse rounded-full bg-primary/25 motion-reduce:animate-none"
              style={{
                width: `${[82, 58, 71, 45, 64][(index + line) % 5]}%`,
                animationDelay: `${((index * 3 + line) % 7) * 260}ms`,
                animationDuration: `${2400 + ((index + line) % 3) * 700}ms`,
              }}
            />
          ))}
          {focused && (
            <span className="mt-auto h-1.5 w-1.5 shrink-0 animate-pulse rounded-[1px] bg-primary motion-reduce:animate-none" />
          )}
        </div>
      )}
    </div>
  );
}

/**
 * What the stage shows, in words, together with what it depends on.
 *
 * The second half is the part that matters. An arrangement stated without its
 * condition is the bug this step had: correct on screen, wrong five seconds
 * later, with nothing to explain the difference.
 */
function Readout({
  columns,
  bands,
  count,
  workspaceWidthPx,
}: {
  columns: number;
  bands: number;
  count: number;
  workspaceWidthPx: number;
}) {
  const oneBandAt = widthForOneBand(count);
  const wrapped = bands > 1;
  const canUnwrap =
    wrapped && oneBandAt !== null && oneBandAt > Math.round(workspaceWidthPx);

  let condition: string;
  if (!wrapped) {
    condition = "All side by side — this window is wide enough.";
  } else if (canUnwrap) {
    condition = `Wrapped to keep every pane readable. From ${formatPx(
      oneBandAt,
    )} px wide they would all fit on one line — this window is ${formatPx(
      Math.round(workspaceWidthPx),
    )} px.`;
  } else if (count > MAX_PANES_PER_BAND) {
    condition = `More than ${MAX_PANES_PER_BAND} side by side is unreadable at any window size, so they wrap.`;
  } else {
    condition = "Wrapped to keep every pane readable.";
  }

  return (
    <div
      data-testid="workspace-stage-readout"
      className="flex flex-wrap items-baseline gap-x-3 gap-y-1"
    >
      <span className="font-mono text-sm font-semibold tracking-tight text-primary">
        {columns} across
        <span className="mx-1.5 text-muted-foreground">×</span>
        {bands} down
      </span>
      <span className="min-w-0 flex-1 text-xs text-muted-foreground">
        {condition}
      </span>
    </div>
  );
}

/** 1 636 rather than 1636 — a width is read, not computed with. */
function formatPx(value: number): string {
  return value.toLocaleString("en-US").replace(/,/g, " ");
}

/**
 * The one control: a track from 1 to the workspace maximum.
 *
 * One control rather than the old six cards PLUS a custom row. Two ways to set
 * one number meant two competing "selected" states, and the cards claimed the
 * common counts were somehow a different kind of choice than 7. They are not —
 * they are just the ones people reach for, so they get a notch on the track.
 *
 * A native range input, so keyboard, screen reader, and touch all work without
 * being re-implemented. The notches are buttons over it, not a second control:
 * they set the same value.
 */
function CountTrack({
  count,
  max,
  onChange,
}: {
  count: number;
  max: number;
  onChange: (next: number) => void;
}) {
  const notches = NOTCHES.filter((n) => n <= max);

  return (
    <div className="rounded-2xl border border-border bg-card/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-2">
          <span
            data-testid="terminal-count-value"
            className="font-mono text-3xl font-semibold leading-none text-primary"
          >
            {count}
          </span>
          <span className="text-sm text-muted-foreground">
            {count === 1 ? "terminal" : "terminals"}
          </span>
        </div>

        <div className="flex shrink-0 items-center gap-1 rounded-lg border border-border bg-background/60 p-1">
          <button
            type="button"
            aria-label="Use one fewer terminal"
            disabled={count <= 1}
            onClick={() => onChange(count - 1)}
            className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Minus className="h-4 w-4" />
          </button>
          <input
            type="number"
            min={1}
            max={max}
            value={count}
            aria-label="Number of terminals"
            onChange={(event) =>
              onChange(
                Number.isFinite(event.currentTarget.valueAsNumber)
                  ? event.currentTarget.valueAsNumber
                  : 1,
              )
            }
            className="h-8 w-14 rounded-md border border-border bg-background text-center font-mono text-sm font-semibold outline-none focus:border-primary/60"
          />
          <button
            type="button"
            aria-label="Use one more terminal"
            disabled={count >= max}
            onClick={() => onChange(count + 1)}
            className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Plus className="h-4 w-4" />
          </button>
        </div>
      </div>

      <input
        type="range"
        min={1}
        max={max}
        step={1}
        value={count}
        aria-label="Number of terminals"
        data-testid="terminal-count-range"
        onChange={(event) => onChange(Number(event.currentTarget.value))}
        className="mt-4 w-full accent-primary"
      />

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {notches.map((n) => (
          <button
            key={n}
            type="button"
            aria-pressed={count === n}
            onClick={() => onChange(n)}
            className={cn(
              "rounded-md px-2 py-1 font-mono text-xs transition-colors",
              count === n
                ? "bg-primary/15 text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {n}
          </button>
        ))}
        <span className="ml-auto font-mono text-[11px] text-muted-foreground">
          max {max}
        </span>
      </div>
    </div>
  );
}
