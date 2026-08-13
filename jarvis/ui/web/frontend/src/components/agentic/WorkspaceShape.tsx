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
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Minus, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import { IconButton } from "./controls";
import {
  WORKABLE_COLS,
  paneColumnsAt,
  paneGrid,
  paneWidthAt,
  panesAreComfortable,
  wizardPanes,
  workableColumnCount,
  type PanePlacement,
} from "./layout";
import { measureAdvance } from "@/lib/terminalFont";

/**
 * Steps a labelled tick is allowed to land on, coarsest last.
 *
 * The legend used to be the fixed list `[1, 2, 4, 6, 8, 12]` spread edge to
 * edge with `justify-between`, which was correct exactly once: when the
 * workspace maximum was also 12. The backend's `MAX_TERMINALS` is 100 — a
 * runaway guard rather than a product ceiling — and against that track the
 * fixed list put "12" hard against the right-hand end, so the legend claimed
 * the track stopped at 12 while the thumb still had seven eighths to travel.
 *
 * A legend has to be DERIVED from the scale it labels, so `countTicks` picks
 * the finest of these steps that still fits inside `MAX_TICKS` labels. The
 * steps are the ones a person reads as round: 25 gives 1 · 25 · 50 · 75 · 100
 * for the current maximum, and 2 gives back the familiar 1 · 2 · 4 · 6 · 8 ·
 * 10 · 12 if the maximum is ever lowered to twelve again.
 */
const NICE_TICK_STEPS = [1, 2, 5, 10, 25, 50, 100, 250, 500, 1000] as const;

/**
 * How many labels the track may carry.
 *
 * Seven, because that is what a 1…12 track needs to keep its every-other-pane
 * rhythm; more than that and the labels start touching on a narrow window.
 */
const MAX_TICKS = 7;

/**
 * Half the width of the range input's thumb, in px.
 *
 * The thumb's CENTRE never reaches the ends of the element — it travels from
 * half a thumb in to half a thumb short of the far edge. Labels placed at a
 * naive 0 % and 100 % would therefore sit beside the thumb they name rather
 * than under it, by the most visible amount at exactly the two values people
 * check first. The legend row is inset by this much on both sides so its
 * percentages measure the same distance the thumb actually covers.
 *
 * Approximate on purpose: the exact thumb width is the platform's business and
 * differs by a pixel or two between Chromium, WebKit and the desktop WebView.
 * Being a pixel out is invisible; being eight px out is not.
 */
const RANGE_THUMB_HALF_WIDTH_PX = 7;

/**
 * The counts that get a labelled tick on a track running 1…`max`.
 *
 * Always starts at 1 and always ends at `max`, so the legend can never
 * disagree with the track's own ends. A step tick that would crowd the final
 * label is dropped rather than drawn on top of it.
 */
export function countTicks(max: number): number[] {
  if (!Number.isFinite(max) || max <= 1) return [1];
  const ceiling = Math.trunc(max);
  for (const step of NICE_TICK_STEPS) {
    const ticks = [1];
    for (let value = step; value < ceiling; value += step) {
      if (value > 1) ticks.push(value);
    }
    ticks.push(ceiling);
    // The last step tick before the end: keep it only if it is at least half a
    // step clear, otherwise the two labels overlap (max 16, step 5 → 15 and 16).
    if (ticks.length >= 3 && ceiling - ticks[ticks.length - 2] < step / 2) {
      ticks.splice(ticks.length - 2, 1);
    }
    if (ticks.length <= MAX_TICKS) return ticks;
  }
  return [1, ceiling];
}

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
  /**
   * The text size the panes will open at, in px.
   *
   * Passed in rather than read here, so the readout and the wizard's blocking
   * warning can never quote two different sizes for the same workspace. See
   * ./paneFont for where the reader's choice is kept.
   */
  fontSize: number;
}

export function WorkspaceShape({
  count,
  names,
  workspaceWidthPx,
  fontSize,
}: WorkspaceShapeProps) {
  const grid = useMemo(() => paneGrid(wizardPanes(count)), [count]);

  return (
    <div className="flex flex-col gap-3 p-3">
      <WorkspaceStage
        columns={grid.columns}
        rows={grid.rows}
        placements={grid.placements}
        count={count}
        names={names}
      />
      <Readout
        columns={grid.columns}
        workspaceWidthPx={workspaceWidthPx}
        fontSize={fontSize}
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
 *
 * ## Why the middle segment is a `<label>` and the field is `type="text"`
 *
 * It has always been typeable and never LOOKED it, which for the maintainer
 * amounted to the same thing as not being typeable (2026-08-11). Two separate
 * causes, both fixed here:
 *
 * * The caret only appeared if you hit the 48 px the digits occupied. The
 *   surrounding segment was a plain `div`, so a click on the padding — most of
 *   the target — landed on nothing at all. It is a `<label>` now: the whole
 *   segment focuses the field, and it carries a text cursor and a hover tint so
 *   the pointer says "editable" before the click.
 * * `type="number"` looks like a display because a browser draws it like one.
 *   It also behaves differently in Chromium, WebKit and the desktop WebView,
 *   reports an empty string for anything it privately considers invalid, and
 *   adds spin buttons that then have to be hidden again. A text field with
 *   `inputMode="numeric"` keeps the phone keypad, hands us the raw keystrokes,
 *   and behaves identically everywhere — at the price of stepping with the
 *   arrow keys ourselves, which is four lines below.
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
        <label
          htmlFor="workspace-terminal-count"
          className={
            "group flex min-w-[7rem] cursor-text items-center justify-center gap-1.5 px-3 " +
            "transition-colors hover:bg-secondary/50"
          }
        >
          <input
            id="workspace-terminal-count"
            type="text"
            inputMode="numeric"
            autoComplete="off"
            spellCheck={false}
            value={draft}
            aria-label="Number of terminals"
            aria-describedby="workspace-terminal-count-max"
            data-testid="terminal-count-value"
            onFocus={(event) => event.currentTarget.select()}
            onChange={(event) => {
              // Anything that is not a digit is dropped rather than rejected,
              // so a pasted "12 panes" still sets 12 instead of nothing.
              const digits = event.currentTarget.value.replace(/\D+/g, "");
              setDraft(digits);
              if (digits !== "") set(Number(digits));
            }}
            // An abandoned half-edit ("", or a paste of pure letters) is not a
            // count; the field returns to what the workspace actually is.
            onBlur={() => setDraft(String(count))}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === "Escape") {
                setDraft(String(count));
                event.currentTarget.blur();
                return;
              }
              // The stepping `type="number"` would have given us, kept because
              // holding an arrow key is how a count gets nudged without
              // reaching for the mouse.
              if (event.key === "ArrowUp") {
                event.preventDefault();
                set(count + 1);
              } else if (event.key === "ArrowDown") {
                event.preventDefault();
                set(count - 1);
              }
            }}
            /*
             * The dashed rule under the digits is the "this is a field" cue, so
             * it belongs to the DIGITS: at `h-full` it landed on the group's own
             * bottom border and read as a rendering fault rather than an
             * invitation. A fixed height keeps it a clear 8 px clear of it.
             */
            className={
              "h-7 w-14 min-w-0 border-0 border-b border-dashed border-border " +
              "bg-transparent p-0 text-right font-mono text-base font-semibold " +
              "leading-7 tabular-nums text-foreground outline-none transition-colors " +
              "group-hover:border-muted-foreground/70 " +
              "focus:border-solid focus:border-primary " +
              /* Focusing selects the digits, and the browser's default
                 selection is a saturated blue that belongs to no theme this
                 app has. Tinted with the accent already carrying "this is the
                 value you are setting". */
              "selection:bg-primary/30 selection:text-foreground"
            }
          />
          <span
            id="workspace-terminal-count-max"
            className="shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground/70"
          >
            / {max}
          </span>
        </label>
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
 * The track, with round counts labelled beneath it.
 *
 * A native range input, so keyboard, screen reader and touch all work without
 * being reimplemented. The labels under it are buttons, but deliberately styled
 * as a scale legend — see the note at the top of this file.
 *
 * Each label is placed at the fraction of the track its value occupies, with a
 * hairline pointing at the exact spot. The row it lives in is `relative` and the
 * labels are absolutely positioned, because the previous `justify-between` laid
 * them out by COUNT rather than by VALUE: six labels always came out evenly
 * spaced whatever they said, which on a 1…100 track put "2" a fifth of the way
 * along and "12" at the far end. A legend that is off by that much is worse than
 * no legend, because it is read as the truth about the scale.
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
  const ticks = useMemo(() => countTicks(max), [max]);
  const span = Math.max(1, max - 1);

  return (
    <div className="flex flex-col gap-2 px-3 pb-3">
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
      <div
        className="relative h-7"
        /* Inset by the same half-thumb the track's own travel is inset by, so a
           label's percentage measures the distance the thumb really covers. */
        style={{
          marginLeft: RANGE_THUMB_HALF_WIDTH_PX,
          marginRight: RANGE_THUMB_HALF_WIDTH_PX,
        }}
      >
        {ticks.map((n) => (
          <button
            key={n}
            type="button"
            aria-pressed={count === n}
            aria-label={`Use ${n} terminals`}
            onClick={() => onChange(n)}
            style={{ left: `${((n - 1) / span) * 100}%` }}
            className={cn(
              "absolute top-0 flex -translate-x-1/2 flex-col items-center gap-1",
              "px-1 font-mono text-[11px] tabular-nums transition-colors",
              count === n
                ? "text-primary"
                : "text-muted-foreground/60 hover:text-foreground",
            )}
          >
            <span
              aria-hidden="true"
              className={cn(
                "h-1.5 w-px transition-colors",
                count === n ? "bg-primary" : "bg-border",
              )}
            />
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
 * Placed by those coordinates, not by document order. The two agreed only while
 * every terminal got a column of its own: a wizard workspace now opens as
 * columns of two (see `WIZARD_COLUMN_HEIGHT`), and left to CSS's own row-first
 * flow the preview would show T1 and T2 side by side where the workspace puts
 * one under the other.
 *
 * The stage is the whole workspace AND the whole window, because those are the
 * same thing now: every column is drawn inside the frame, however many there
 * are, and more terminals make each one narrower. It used to draw the grid
 * wider than the frame and clip the remainder, which was the honest picture of
 * a workspace you scrolled sideways — and is exactly what the maintainer asked
 * to be rid of on 2026-08-04.
 */
function WorkspaceStage({
  columns,
  rows,
  placements,
  count,
  names,
}: {
  columns: number;
  rows: number;
  placements: PanePlacement[];
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
        className="grid h-full w-full gap-1 p-1"
        style={{
          gridTemplateColumns: `repeat(${safeColumns}, minmax(0, 1fr))`,
          gridTemplateRows: `repeat(${safeRows}, minmax(0, 1fr))`,
        }}
      >
        {Array.from({ length: count }).map((_, index) => {
          const at = placements[index];
          return (
            <StagePane
              key={index}
              name={names[index] ?? `T${index + 1}`}
              detail={detail}
              /* The workspace opens with the first pane selected — the prompt
                 bar types into it. Showing that here means the stage is not
                 just the right shape, it is the right state. */
              focused={index === 0}
              /* A short column's panes are TALLER, exactly as in the running
                 grid: `rowSpan` is how three terminals draw one full column
                 beside a single pane that reaches the same bottom edge. */
              style={
                at
                  ? {
                      gridColumn: at.column,
                      gridRow: `${at.row} / span ${at.rowSpan}`,
                    }
                  : undefined
              }
            />
          );
        })}
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
  style,
}: {
  name: string;
  detail: PaneDetail;
  focused: boolean;
  /** Where this pane sits in the stage grid — see `WorkspaceStage`. */
  style?: CSSProperties;
}) {
  return (
    <div
      style={style}
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
 * What the stage shows, in words, together with what it costs.
 *
 * The second half is the part that matters. An arrangement stated without its
 * consequence is a bug this step actually had: correct on screen, and silent
 * about the thing the user would notice a minute later.
 *
 * ## Why it counts COLUMNS and not only pixels
 *
 * "About 145 px each" is true and says nothing. A pixel width means one thing
 * at 8 px text and the opposite at 20, and the number that decides whether a
 * coding CLI can draw at all is its column count — below {@link WORKABLE_COLS}
 * a pane holds its agent's columns and shows a card instead of a terminal (see
 * `PaneTooNarrowCard` in ./AgenticTerminal).
 *
 * That is exactly what went unsaid on 2026-08-13: twelve terminals opened on a
 * 1 920 px window at text size 20 with no warning at all, because the only
 * question asked was "twenty or more?" — a fixed count, blind to both the
 * window and the text size. The readout now measures the real font and says
 * what the user is about to get, in the unit that decides it.
 */
function Readout({
  columns,
  workspaceWidthPx,
  fontSize,
}: {
  columns: number;
  workspaceWidthPx: number;
  fontSize: number;
}) {
  // By COLUMNS rather than the raw count, so the sentence can never describe a
  // different workspace from the stage above it.
  const paneWidth = paneWidthAt(columns, workspaceWidthPx);
  const comfortable = panesAreComfortable(columns, workspaceWidthPx);
  /*
   * The real font, measured — never an assumed advance width. `null` where
   * there is no canvas to measure with (jsdom, and any environment that cannot
   * answer), and every branch below treats that as "no answer yet" rather than
   * as a warning: a readout that shouted at everyone once because it could not
   * measure would be the next thing reported.
   */
  const cell = useMemo(() => measureAdvance(fontSize), [fontSize]);
  const perPane = paneColumnsAt(columns, workspaceWidthPx, cell ?? 0);
  const affordable = workableColumnCount(workspaceWidthPx, cell ?? 0);

  /*
   * There is no longer an "and the rest are off screen" case to warn about —
   * every pane is on screen, always. What is left to say is the price of that
   * promise, which the user pays in pane WIDTH, so the readout quotes it before
   * they commit to the count rather than after.
   */
  let condition: string;
  if (paneWidth === 0) {
    condition = "All on one screen.";
  } else if (perPane > 0 && perPane < WORKABLE_COLS) {
    // The one case worth spelling out: these panes will not be terminals.
    condition =
      `All on one screen, about ${perPane} columns each — too narrow for an ` +
      `agent to draw in, so they open as status cards. This window fits ` +
      `${affordable} across at text size ${fontSize}.`;
  } else if (perPane > 0) {
    condition = `All on one screen, about ${perPane} columns each.`;
  } else if (comfortable) {
    condition = `All on one screen, about ${formatPx(paneWidth)} px each.`;
  } else {
    condition = `All on one screen, about ${formatPx(
      paneWidth,
    )} px each — narrow for an agent's output. Maximize a pane to read it full size.`;
  }

  return (
    <p
      data-testid="workspace-stage-readout"
      data-pane-cols={perPane || ""}
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
