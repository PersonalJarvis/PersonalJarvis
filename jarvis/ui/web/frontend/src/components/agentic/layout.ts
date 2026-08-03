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
 *
 * ## One line of columns, however many there are
 *
 * The workspace never wraps. Opening a column always puts it beside the last
 * one, and a workspace of twenty columns is twenty columns across.
 *
 * It used to wrap at the point where the panes would fall below
 * `MIN_PANE_WIDTH_PX` — six on a normal desktop — and that was the wrong
 * trade. Wrapping does not create room; it takes the height of every existing
 * pane to pay for the new one, so the sixth split silently halved the five
 * panes the user was already reading and dropped the new one onto a second
 * line. The arrangement the user built changed SHAPE because of a pane they
 * added at the end, which is exactly what a workspace must not do (reported
 * 2026-08-03).
 *
 * Readability is still a floor — it is just paid for the same way the vertical
 * one is. Below `MIN_PANE_WIDTH_PX` per column the workspace grows WIDER than
 * the window and scrolls sideways, the way it already grows taller and scrolls
 * down when the stacks get short (`MIN_PANE_HEIGHT_PX`). A workspace larger
 * than the window is an ordinary thing that every editor does; a workspace
 * that rearranges itself is not.
 */

/**
 * Narrowest a pane may get before its agent's output stops being readable.
 *
 * An agent TUI draws boxes, file trees and status rows; below roughly 45
 * characters it truncates them and the pane becomes decoration. At the default
 * 13 px monospace a character is ~7.8 px wide, so 45 characters plus the pane's
 * frame and padding lands near 380 px. Deliberately a floor on WIDTH rather than
 * a count, so the same rule holds on a laptop and on a 4K display.
 *
 * It is the width the workspace stops SHRINKING at, not a count it stops
 * growing at: past it the columns keep their width and the workspace scrolls
 * sideways. Measured against a real agent on 2026-07-25 — at ~18 characters
 * Claude Code truncates every line and breaks single words across rows
 * ("Clau/de/Max"), which is what this number exists to prevent.
 */
export const MIN_PANE_WIDTH_PX = 380;

/**
 * Shortest a pane may get before its agent's output stops being readable.
 *
 * The mirror of `MIN_PANE_WIDTH_PX`, and it was missing. Panes shared the window
 * height in equal parts with nothing stopping them from shrinking, so raising
 * the per-workspace cap from 12 to 100 quietly traded one failure for another:
 * measured on a 2560 px screen, 12 panes give each ~26 text rows, 40 give 7, and
 * 100 give 3. Nothing crashes — the workspace simply becomes unusable, which is
 * harder to notice than a crash and worse to live with.
 *
 * An agent TUI needs its input box plus enough history to see what it just did:
 * roughly a dozen rows at the default 13 px (~17 px per row) plus the pane's
 * header and frame. Past that point the grid scrolls instead of shrinking, the
 * same way a long page does.
 */
export const MIN_PANE_HEIGHT_PX = 240;

/**
 * Horizontal padding of the rendered grid — 4 px on each side.
 *
 * It mirrors `GRID_GAP_PX` in AgenticGrid, and the two must not drift: the
 * column count is computed from the grid's CONTENT width, so a padding the
 * layout module does not know about makes the wizard's preview and the running
 * grid disagree about how many panes fit at a given window width.
 */
export const GRID_HORIZONTAL_PADDING_PX = 8;

/**
 * How wide the workspace has to be drawn to hold ``columns`` readable columns.
 *
 * The horizontal twin of the canvas height rule: normally the workspace is
 * exactly the width it was given and nothing scrolls, and once the columns
 * would be squeezed below `MIN_PANE_WIDTH_PX` it grows past the window instead
 * and the grid scrolls sideways.
 *
 * ``availableWidthPx`` is the grid's CONTENT width, so the return value is one
 * too — the caller's padding is already off both.
 */
export function workspaceWidthFor(
  columns: number,
  availableWidthPx: number,
): number {
  const wanted = Math.max(0, columns) * MIN_PANE_WIDTH_PX;
  return Math.max(availableWidthPx, wanted);
}

/**
 * How many columns are visible at ``containerWidthPx`` before it scrolls.
 *
 * Nothing lays out by this — it is what the wizard's readout says out loud, so
 * someone picking twelve terminals on a laptop learns they are getting a
 * workspace they scroll rather than one that silently rearranged itself.
 *
 * Takes an OUTER width (the wizard measures an unpadded element) and subtracts
 * the padding the grid will have, so it answers for the same physical window
 * the running grid does. Never 0: one cramped column still beats none.
 */
export function columnsWithoutScrolling(containerWidthPx: number): number {
  const content = Math.max(0, containerWidthPx - GRID_HORIZONTAL_PADDING_PX);
  if (!Number.isFinite(content) || content <= 0) return 1;
  return Math.max(1, Math.floor(content / MIN_PANE_WIDTH_PX));
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
 * build. An earlier preview took a shortcut and derived its dots from the raw
 * terminal COUNT, which happens to agree here only because every terminal gets
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
 * Window width at which ``count`` terminals are all visible at once.
 *
 * The arrangement no longer depends on the window — `count` terminals are
 * `count` columns at every size — but how much of it you can SEE does, and the
 * preview says so rather than letting a workspace that scrolls come as a
 * surprise. Returns null when one column is the whole workspace and there is
 * nothing to scroll past.
 */
export function widthForAllVisible(count: number): number | null {
  if (count <= 1) return null;
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
export function paneGrid<T extends Positioned>(panes: readonly T[]): PaneGrid {
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
    const span = Math.max(1, Math.floor(unit / Math.max(1, stack.length)));
    stack.forEach((pane, position) => {
      placements[pane] = {
        column: index + 1,
        row: position * span + 1,
        rowSpan: span,
      };
    });
  });

  return { columns: ordered.length, rows: unit, placements };
}
