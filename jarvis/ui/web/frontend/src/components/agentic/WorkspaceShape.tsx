/**
 * How many terminals the workspace opens with, and the shape they land in.
 *
 * ## One control, three grips
 *
 * The count used to be four separate controls sitting one under the other: a
 * number field with its own minus and plus, a slider, and a row of seven preset
 * buttons. All four set the same integer, so all four had to show the same
 * "selected" state, and a screen that says one thing four times in four visual
 * languages is what makes an interface feel machine-assembled.
 *
 * It is now ONE control that happens to be reachable three ways — a stepper you
 * read the number off, a track you drag, and tick labels under that track. The
 * ticks are not presets and are not styled as buttons: they are the scale's
 * legend, and clicking a legend entry is a convenience, not a second mechanism.
 * Every value between them is still reachable, which is the whole reason a
 * "custom" row never needed to exist.
 *
 * ## The stage shows the workspace, not a picture of agents working
 *
 * Each miniature pane used to draw three animated bars standing in for the
 * agent's output. It looked alive and said nothing — no agent has started, and
 * the bars' widths came from an array indexed by pane number. Invented content
 * is the cheapest way to make a preview feel untrustworthy, because the moment
 * the reader works out the bars are fake they stop believing the arrangement
 * too. So the panes now show exactly what is knowable before the workspace
 * opens: how many there are, where each one sits, what each one is called, and
 * which one the prompt bar will type into.
 *
 * The arrangement itself is not decorative. `paneGrid` is the same function the
 * running workspace lays itself out with, fed the same panes the backend will
 * create, at the measured width the grid will occupy — so the preview cannot
 * describe a workspace that will not appear.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Minus, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { IconButton } from "./controls";
import {
  columnsWithoutScrolling,
  paneGrid,
  widthForAllVisible,
  wizardPanes,
} from "./layout";

/**
 * Counts that get a labelled tick on the track.
 *
 * The ones people reach for, so they are one click rather than a careful drag.
 * Anything above the workspace maximum is dropped at render time.
 */
const TICKS = [1, 2, 4, 6, 8, 12] as const;

/**
 * Shape of the area the panes will actually fill, width ÷ height.
 *
 * The workspace grid sits under a toolbar and above the prompt bar, so it is
 * markedly wider relative to its height than the window is: a maximised
 * 1920 × 1080 window leaves the grid roughly 1636 × 726.
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
 * Safety rails for extreme column widths only — at the launcher's own width the
 * derived height lands comfortably inside them, so the stage keeps its true
 * proportions and holds still while the count changes.
 */
const STAGE_MIN_HEIGHT_PX = 120;
const STAGE_MAX_HEIGHT_PX = 260;

/*
 * How much of itself a miniature pane has room to draw.
 *
 * BOTH axes, which an earlier version got wrong: it asked only about width, so
 * sixty terminals — laid out three across and twenty down — kept their title
 * bars at 16 px of height and rendered every call-sign as a squashed smear. A
 * pane that is wide and flat has no more room for a name than a narrow one.
 *
 * Dropping detail as the panes shrink is also the stage being honest: sixty
 * terminals really are sixty slivers, and showing them as plain rectangles says
 * so more clearly than sixty illegible labels.
 */
const PANE_LABEL_MIN_WIDTH_PX = 48;
const PANE_LABEL_MIN_HEIGHT_PX = 34;
const PANE_CHROME_MIN_WIDTH_PX = 18;
const PANE_CHROME_MIN_HEIGHT_PX = 16;

interface WorkspaceShapeProps {
  /** Terminals currently chosen. */
  count: number;
  /** Call-signs the panes will open with, so the stage shows the real names. */
  names: string[];
  /**
   * Width of the slot the workspace will occupy, in px — measured by the view
   * from the element the grid will later fill. It keeps unreadably narrow
   * panes out of the preview and the running workspace.
   */
  workspaceWidthPx: number;
}

export function WorkspaceShape({
  count,
  names,
  workspaceWidthPx,
}: WorkspaceShapeProps) {
  const grid = useMemo(() => paneGrid(wizardPanes(count)), [count]);
  /*
   * How many of those columns the window shows at once.
   *
   * The stage draws all of them — the workspace really is `count` columns wide
   * whatever the window does — and the readout says how many are on screen
   * before it scrolls. Two different questions since 2026-08-03, when the
   * workspace stopped wrapping: the ARRANGEMENT no longer depends on the window
   * at all, only the view onto it does.
   */
  const visible = useMemo(
    () => columnsWithoutScrolling(workspaceWidthPx),
    [workspaceWidthPx],
  );

  return (
    <div className="flex flex-col gap-3 p-3">
      <WorkspaceStage
        columns={grid.columns}
        visible={visible}
        rows={grid.rows}
        count={count}
        names={names}
      />
      <Readout
        columns={grid.columns}
        visible={visible}
        count={count}
        workspaceWidthPx={workspaceWidthPx}
      />
    </div>
  );
}

/**
 * The stepper the count is read from or typed into — the header half of this
 * control.
 *
 * Lives in the panel's title row rather than above the stage, so the number sits
 * on the same line as the word "Terminals" and the stage below it is the only
 * thing that changes when the number does.
 */
export function CountStepper({
  count,
  max,
  onChange,
}: {
  count: number;
  max: number;
  onChange: (next: number) => void;
}) {
  const [draft, setDraft] = useState(String(count));

  useEffect(() => setDraft(String(count)), [count]);

  const set = (next: number) => {
    const bounded = Math.max(1, Math.min(max, Math.trunc(next)));
    setDraft(String(bounded));
    onChange(bounded);
  };

  return (
    <div className="flex flex-col items-end gap-1.5">
      <label
        htmlFor="workspace-terminal-count"
        className="font-mono text-[9px] font-medium uppercase tracking-[0.14em] text-muted-foreground"
      >
        Exact count · type a number
      </label>
      <div className="flex h-11 items-stretch overflow-hidden rounded-control border border-border bg-background transition-colors focus-within:border-primary/70 focus-within:ring-2 focus-within:ring-primary/15">
        <IconButton
          label="Use one fewer terminal"
          disabled={count <= 1}
          onClick={() => set(count - 1)}
          className="h-full w-11 rounded-none border-r border-border/70"
        >
          <Minus className="h-4 w-4" />
        </IconButton>
        <div className="flex min-w-[6.5rem] items-center justify-center gap-1.5 px-2">
          <input
            id="workspace-terminal-count"
            type="number"
            inputMode="numeric"
            min={1}
            max={max}
            value={draft}
            aria-label="Number of terminals"
            data-testid="terminal-count-value"
            onFocus={(event) => event.currentTarget.select()}
            onChange={(event) => {
              const raw = event.currentTarget.value;
              setDraft(raw);
              if (
                raw !== "" &&
                Number.isFinite(event.currentTarget.valueAsNumber)
              ) {
                set(event.currentTarget.valueAsNumber);
              }
            }}
            onBlur={() => {
              if (draft === "") setDraft(String(count));
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
            }}
            /*
             * The spin buttons the browser adds are hidden: this field already
             * has accessible minus and plus controls, while the native pair is
             * tiny and appears only on hover.
             */
            className={
              "h-full min-w-0 w-12 border-0 bg-transparent p-0 text-right font-mono " +
              "text-base font-semibold tabular-nums text-foreground outline-none " +
              "[appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none " +
              "[&::-webkit-outer-spin-button]:appearance-none"
            }
          />
          <span
            aria-hidden="true"
            className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground/70"
          >
            / {max}
          </span>
        </div>
        <IconButton
          label="Use one more terminal"
          disabled={count >= max}
          onClick={() => set(count + 1)}
          className="h-full w-11 rounded-none border-l border-border/70"
        >
          <Plus className="h-4 w-4" />
        </IconButton>
      </div>
    </div>
  );
}

/**
 * The track, with the common counts labelled beneath it.
 *
 * A native range input, so keyboard, screen reader and touch all work without
 * being reimplemented. The labels under it are buttons, but deliberately styled
 * as a scale legend — see the note at the top of this file.
 */
export function CountTrack({
  count,
  max,
  onChange,
}: {
  count: number;
  max: number;
  onChange: (next: number) => void;
}) {
  const ticks = TICKS.filter((n) => n <= max);
  return (
    <div className="flex flex-col gap-1.5 px-3 pb-3">
      <input
        type="range"
        min={1}
        max={max}
        step={1}
        value={count}
        aria-label="Number of terminals"
        data-testid="terminal-count-range"
        onChange={(event) => onChange(Number(event.currentTarget.value))}
        className="h-1 w-full cursor-pointer accent-primary"
      />
      <div className="flex items-center justify-between">
        {ticks.map((n) => (
          <button
            key={n}
            type="button"
            aria-pressed={count === n}
            onClick={() => onChange(n)}
            className={cn(
              "rounded px-1 font-mono text-[11px] tabular-nums transition-colors",
              count === n
                ? "text-primary"
                : "text-muted-foreground/60 hover:text-foreground",
            )}
          >
            {n}
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * The workspace that is about to open, at a scale that fits on this screen.
 *
 * Every pane is placed by `paneGrid` — the function the running workspace uses
 * — over the panes the backend will actually create (`wizardPanes`). The two
 * cannot drift apart without the running grid changing too.
 *
 * The stage is the WINDOW, not the workspace. Past `visible` columns the grid
 * inside it is drawn wider than the frame and the remainder is clipped at the
 * right edge — which is exactly what the running workspace does, and says it
 * more plainly than any sentence could: this many fit, the rest are a scroll
 * away.
 */
function WorkspaceStage({
  columns,
  visible,
  rows,
  count,
  names,
}: {
  columns: number;
  visible: number;
  rows: number;
  count: number;
  names: string[];
}) {
  /*
   * The stage measures ITSELF rather than being told its size.
   *
   * Its height comes from CSS (`aspect-ratio`), so only the browser knows what
   * it ended up being once the min/max rails are applied — and each pane needs
   * that to decide how much of itself it has room to draw.
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
  // How much wider than the frame the workspace is drawn. 1 while everything
  // fits, so the common case is an ordinary full-width grid.
  const overshoot = Math.max(1, safeColumns / Math.max(1, visible));
  const paneWidth = size.width > 0 ? size.width / safeColumns : 0;
  const paneHeight = size.height > 0 ? size.height / safeRows : 0;
  // Panes are laid out as a grid of equal cells, so one pane's detail level is
  // every pane's — decided once rather than per tile. Before the first measure
  // both are 0; assume the roomy case so the stage never flashes as bare tiles.
  const unmeasured = paneWidth === 0 || paneHeight === 0;
  const detail: PaneDetail =
    unmeasured ||
    (paneWidth >= PANE_LABEL_MIN_WIDTH_PX &&
      paneHeight >= PANE_LABEL_MIN_HEIGHT_PX)
      ? "full"
      : paneWidth >= PANE_CHROME_MIN_WIDTH_PX &&
          paneHeight >= PANE_CHROME_MIN_HEIGHT_PX
        ? "chrome"
        : "tile";

  return (
    <div
      data-testid="workspace-stage"
      className="overflow-hidden rounded-control bg-background/70 ring-1 ring-inset ring-border/60"
      style={{
        aspectRatio: `${WORKSPACE_ASPECT}`,
        minHeight: STAGE_MIN_HEIGHT_PX,
        maxHeight: STAGE_MAX_HEIGHT_PX,
      }}
    >
      <div
        ref={stageRef}
        data-testid="workspace-stage-grid"
        className="grid h-full gap-1 p-1"
        style={{
          width: `${overshoot * 100}%`,
          gridTemplateColumns: `repeat(${safeColumns}, minmax(0, 1fr))`,
          gridTemplateRows: `repeat(${safeRows}, minmax(0, 1fr))`,
        }}
      >
        {Array.from({ length: count }).map((_, index) => (
          <StagePane
            key={index}
            name={names[index] ?? `T${index + 1}`}
            detail={detail}
            /* The workspace opens with the first pane selected — the prompt bar
               types into it. Showing that here means the stage is not just the
               right shape, it is the right state. */
            focused={index === 0}
          />
        ))}
      </div>
    </div>
  );
}

type PaneDetail = "full" | "chrome" | "tile";

/** One terminal, showing only what is knowable before anything has started. */
function StagePane({
  name,
  detail,
  focused,
}: {
  name: string;
  detail: PaneDetail;
  focused: boolean;
}) {
  return (
    <div
      /*
       * Dark, like the terminals it stands for — not a tile of brand colour.
       * Colour marks ONE thing here: which pane the prompt bar will type into.
       */
      className={cn(
        "flex min-h-0 min-w-0 items-start justify-start overflow-hidden rounded-[3px]",
        focused ? "bg-primary/[0.09] ring-1 ring-inset ring-primary/40" : "bg-muted/40",
      )}
    >
      {detail === "full" && (
        <span
          className={cn(
            "truncate px-1.5 py-1 font-mono text-[10px] leading-none",
            focused ? "text-primary" : "text-muted-foreground",
          )}
        >
          {name}
        </span>
      )}
      {detail === "chrome" && (
        <span
          className={cn(
            "m-1 h-1 w-1 shrink-0 rounded-full",
            focused ? "bg-primary" : "bg-muted-foreground/50",
          )}
        />
      )}
    </div>
  );
}

/**
 * What the stage shows, in words, together with what it depends on.
 *
 * The second half is the part that matters. An arrangement stated without its
 * condition is a bug this step actually had: correct on screen, wrong five
 * seconds later after a maximise, with nothing to explain the difference.
 */
function Readout({
  columns,
  visible,
  count,
  workspaceWidthPx,
}: {
  columns: number;
  visible: number;
  count: number;
  workspaceWidthPx: number;
}) {
  const allVisibleAt = widthForAllVisible(count);
  const scrolls = columns > visible;

  let condition: string;
  if (!scrolls) {
    condition = "All side by side — this window is wide enough.";
  } else if (allVisibleAt !== null) {
    condition = `All side by side, ${visible} of them on screen — scroll sideways for the rest. From ${formatPx(
      allVisibleAt,
    )} px wide they all fit at once; this window is ${formatPx(
      Math.round(workspaceWidthPx),
    )} px.`;
  } else {
    condition = "All side by side — scroll sideways for the rest.";
  }

  return (
    <p
      data-testid="workspace-stage-readout"
      className="text-xs leading-relaxed text-muted-foreground"
    >
      <span className="font-mono tabular-nums text-foreground">
        {columns} across
      </span>{" "}
      · {condition}
    </p>
  );
}

/**
 * Digit grouping for a pixel width the user READS: 1 636, not 1636.
 *
 * The separator is an explicit NARROW NO-BREAK SPACE escape rather than a
 * literal one, so it stays visible in the source and cannot be mistaken for an
 * ordinary space by the next person who greps for it — which is exactly how it
 * slipped past a test that searched for a plain space. It also keeps the number
 * from breaking across two lines mid-thousands.
 *
 * Grouped by hand rather than through `toLocaleString`, whose output depends on
 * the runtime's locale data: a number the user reads should not change shape
 * with the machine that rendered it.
 */
const THOUSANDS_SEPARATOR = "\u202F";

function formatPx(value: number): string {
  return String(Math.round(value)).replace(
    /\B(?=(\d{3})+(?!\d))/g,
    THOUSANDS_SEPARATOR,
  );
}
