/**
 * How N agent panes are laid out in the workspace — the ONE answer, shared by
 * the grid that renders them and the wizard that previews them.
 *
 * It used to be two answers. The grid put every pane of a row side by side
 * (the backend starts a session with all panes in row 0), while the wizard's
 * dot preview had its own formula that stacked them 3-per-line — so picking
 * "4 terminals" showed 3 above and 1 below and then opened 4 side by side. A
 * preview that contradicts the thing it previews is worse than no preview, and
 * the only durable fix is that both call the same function.
 *
 * ## The workspace is COLUMNS of stacked panes
 *
 * A workspace is a left-to-right list of columns, and each column is a
 * top-to-bottom stack of one or more panes. "Split right" opens a new column
 * beside the anchor; "split down" adds a pane to the anchor's OWN column and
 * leaves every other column untouched.
 *
 * That is the whole point of the model. The earlier one had a single axis — a
 * pane knew only its row — so "split down" could only mean "open a new row",
 * and a row spans the whole window by definition. Splitting one pane therefore
 * squashed all the others to half height, which is not what splitting a pane
 * means anywhere else. A second axis is the smallest thing that fixes it.
 *
 * ## Why one grid and not nested containers
 *
 * The obvious rendering — a flex row of column elements, each a flex column of
 * panes — is wrong here, and expensively so. Every pane must stay MOUNTED for
 * its whole life: unmounting one tears down its WebSocket and kills the coding
 * agent behind it. React re-parents children when the element tree changes
 * shape, so closing a column would remount the panes of every column after it.
 *
 * `paneGrid` therefore returns COORDINATES, not nested lists. All panes are
 * siblings inside one grid container that never changes, and a layout change is
 * only ever a change of numbers on each pane. Nothing is re-parented, so
 * nothing remounts.
 */

/**
 * Most columns that may share one band (one line of columns) when the available
 * width is unknown — a last-resort ceiling, not the number to lay out by.
 *
 * Use `bandCapacityFor(widthPx)` wherever the width IS known, which is every
 * real render. A fixed count cannot work: ten columns in a 2560 px window give
 * each pane ~256 px, but the same ten in a 1440 px window give ~144 px — about
 * 18 characters. Measured at that width, Claude Code truncates every line and
 * breaks single words across rows ("Clau/de/Max"), which is exactly the
 * unreadable pane reported on 2026-07-25 with eight terminals in one band.
 */
export const MAX_PANES_PER_BAND = 10;

/**
 * Narrowest a pane may get before its agent's output stops being readable.
 *
 * An agent TUI draws boxes, file trees and status rows; below roughly 45
 * characters it truncates them and the pane becomes decoration. At the default
 * 13 px monospace a character is ~7.8 px wide, so 45 characters plus the pane's
 * frame and padding lands near 380 px. Deliberately a floor on WIDTH rather than
 * a count, so the same rule holds on a laptop and on a 4K display.
 */
export const MIN_PANE_WIDTH_PX = 380;

/** Horizontal padding of the rendered grid (`p-3`: 12 px on each side). */
export const GRID_HORIZONTAL_PADDING_PX = 24;

/**
 * How many columns fit in ``widthPx`` without starving them.
 *
 * Returns at least 1 — one unreadably narrow pane still beats no pane — and
 * never more than `MAX_PANES_PER_BAND`.
 */
export function bandCapacityFor(
  widthPx: number,
  minPaneWidth: number = MIN_PANE_WIDTH_PX,
): number {
  if (!Number.isFinite(widthPx) || widthPx <= 0) return MAX_PANES_PER_BAND;
  const fits = Math.floor(widthPx / Math.max(1, minPaneWidth));
  return Math.max(1, Math.min(MAX_PANES_PER_BAND, fits));
}

/**
 * Columns that fit inside the grid's content box.
 *
 * `clientWidth` includes padding while CSS grid tracks occupy the content box.
 * Keeping this subtraction here gives the running grid and wizard preview the
 * exact same answer at width thresholds.
 */
export function workspaceBandCapacityFor(containerWidthPx: number): number {
  return bandCapacityFor(
    Math.max(0, containerWidthPx - GRID_HORIZONTAL_PADDING_PX),
  );
}

/**
 * Ceiling on the grid's row unit (see `paneGrid`).
 *
 * The unit is the least common multiple of the column heights, which stays
 * small for real workspaces (columns of 3, 4 and 5 panes → 60). The cap only
 * exists so a pathological mix can never ask the browser for a grid with tens
 * of thousands of rows.
 */
const MAX_ROW_UNIT = 120;

/** A pane's place in the workspace — the fields this module actually needs. */
export interface Positioned {
  /** Which column, left to right. */
  column: number;
  /** Position within that column, top to bottom. */
  slot: number;
}

/**
 * The panes a wizard-opened workspace of ``count`` terminals starts with.
 *
 * One row of columns — `column = index`, `slot = 0` — which is literally what
 * the backend writes when it opens a session from the wizard
 * (`agentic_ide/session.py`: "A wizard-opened workspace is one row of columns").
 *
 * It exists so the preview cannot describe a workspace the backend would never
 * build. The old preview took a shortcut and fed the raw terminal COUNT into
 * `paneColumns`, which happens to agree here only because every terminal gets
 * its own column at the start. Feed `paneGrid` the same panes the grid will
 * receive and the preview stops being a second opinion.
 */
export function wizardPanes(count: number): Positioned[] {
  return Array.from({ length: Math.max(0, Math.trunc(count)) }, (_, index) => ({
    column: index,
    slot: 0,
  }));
}

/**
 * Width at which ``count`` wizard-opened terminals would fit on ONE line.
 *
 * The preview promises an arrangement that depends on a number the user is not
 * thinking about — how wide the window happens to be right now. Pick 8 in a
 * 1050 px window and the honest answer is "2 across, 4 down"; maximise the same
 * window and it becomes "4 across, 2 down". Reporting only the first reads as a
 * broken promise the moment the second one happens, which is exactly what was
 * reported on 2026-07-26.
 *
 * So the preview also says what a wider window would buy. Returns null when the
 * count is already on one line, or when it never can be (past
 * `MAX_PANES_PER_BAND` no width is enough — the wrap is the point).
 */
export function widthForOneBand(count: number): number | null {
  if (count <= 1 || count > MAX_PANES_PER_BAND) return null;
  return count * MIN_PANE_WIDTH_PX + GRID_HORIZONTAL_PADDING_PX;
}

/** Where one pane sits in the CSS grid. All values are 1-based, as CSS wants. */
export interface PanePlacement {
  column: number;
  row: number;
  /** Grid rows this pane spans — how a short column fills the same height. */
  rowSpan: number;
}

/** The rendered grid: its size, plus one placement per pane, in input order. */
export interface PaneGrid {
  /** `grid-template-columns` count. */
  columns: number;
  /** `grid-template-rows` count. */
  rows: number;
  placements: PanePlacement[];
}

/**
 * Columns per band for a workspace of ``count`` columns: the grid's width.
 *
 * Also the wizard's preview width, which is the point — the dots the user sees
 * before starting are the arrangement they get.
 */
export function paneColumns(count: number, maxPerBand: number = MAX_PANES_PER_BAND): number {
  if (count <= 0) return 0;
  const cap = Math.max(1, maxPerBand);
  const bands = Math.ceil(count / cap);
  return Math.ceil(count / bands);
}

/**
 * Bands a workspace of ``count`` columns occupies once wrapped.
 *
 * A wrapped workspace is taller, so the grid needs proportionally more rows —
 * otherwise two bands share the height of one and every pane is half as tall.
 */
export function paneLines(count: number, maxPerBand: number = MAX_PANES_PER_BAND): number {
  const width = paneColumns(count, maxPerBand);
  return width === 0 ? 0 : Math.ceil(count / width);
}

function greatestCommonDivisor(a: number, b: number): number {
  let [x, y] = [Math.abs(a), Math.abs(b)];
  while (y) [x, y] = [y, x % y];
  return x || 1;
}

/**
 * Place every pane in one CSS grid.
 *
 * Columns are read off the panes' own `column` values (gaps closed, so a
 * half-applied close never renders a blank stripe) and each column's panes are
 * ordered by `slot`.
 *
 * Height is the subtle part: columns hold different numbers of panes, and all
 * of them have to end flush at the bottom. So the grid's row count is the
 * least common multiple of the column heights — a column of 2 spans 3 rows per
 * pane where a column of 3 spans 2 — and every column fills exactly the same
 * height whatever it holds.
 */
export function paneGrid<T extends Positioned>(
  panes: readonly T[],
  maxPerBand: number = MAX_PANES_PER_BAND,
): PaneGrid {
  if (panes.length === 0) return { columns: 0, rows: 0, placements: [] };

  // Column index by the pane's own column number, gaps closed.
  const ordered = [...new Set(panes.map((p) => p.column))].sort((a, b) => a - b);
  const columnIndex = new Map(ordered.map((column, index) => [column, index]));

  // Each column's panes, top to bottom, as indexes into `panes`.
  const stacks: number[][] = ordered.map(() => []);
  panes.forEach((pane, index) => stacks[columnIndex.get(pane.column) ?? 0].push(index));
  for (const stack of stacks) {
    stack.sort((a, b) => panes[a].slot - panes[b].slot);
  }

  const perBand = Math.max(1, paneColumns(ordered.length, maxPerBand));
  const bands = Math.ceil(ordered.length / perBand);

  let unit = 1;
  for (const stack of stacks) {
    const height = Math.max(1, stack.length);
    const next = (unit / greatestCommonDivisor(unit, height)) * height;
    // Past the cap the exact fit is abandoned rather than the layout: every
    // pane keeps one row, so a column simply ends higher than its neighbours.
    unit = next <= MAX_ROW_UNIT ? next : unit;
  }

  const placements: PanePlacement[] = new Array(panes.length);
  stacks.forEach((stack, index) => {
    const band = Math.floor(index / perBand);
    const column = (index % perBand) + 1;
    const span = Math.max(1, Math.floor(unit / Math.max(1, stack.length)));
    stack.forEach((pane, position) => {
      placements[pane] = {
        column,
        row: band * unit + position * span + 1,
        rowSpan: span,
      };
    });
  });

  return { columns: perBand, rows: bands * unit, placements };
}
