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
 * ## One line of columns, however many there are — and ALWAYS one screenful
 *
 * The workspace never wraps. Opening a column always puts it beside the last
 * one, and a workspace of twenty columns is twenty columns across.
 *
 * It also never scrolls. The whole workspace is exactly the area it is given:
 * open a pane and every pane gets a little smaller, on whichever axis the split
 * was. That is the maintainer's standing rule for this screen (2026-08-04), and
 * it settles the two failures that came before it:
 *
 * * **Wrapping** (until 2026-08-03) paid for a new pane with the HEIGHT of every
 *   existing one, so the sixth split silently halved the five panes the user was
 *   already reading and dropped the new one onto a second line. The arrangement
 *   changed SHAPE because of a pane added at the end.
 * * **Scrolling** (its replacement) kept the shape and moved the panes off the
 *   screen instead — a seventh terminal was opened somewhere to the right, and
 *   watching eight agents meant scrolling between them. A wall of terminals you
 *   have to scroll is not a wall of terminals.
 *
 * So neither axis has a floor that grows the canvas any more. Readability is a
 * thing the user manages themselves, with the two controls that already exist:
 * open fewer panes, or maximize the one being read. `COMFORTABLE_PANE_WIDTH_PX`
 * survives ONLY as the number the wizard's readout warns from — advice before
 * anything opens, never a layout decision.
 */

/**
 * Below this a pane is cramped for an agent's output — ADVICE, not a floor.
 *
 * An agent TUI draws boxes, file trees and status rows; below roughly 45
 * characters it truncates them and the pane becomes decoration. At the default
 * 13 px monospace a character is ~7.8 px wide, so 45 characters plus the pane's
 * frame and padding lands near 380 px. Measured against a real agent on
 * 2026-07-25 — at ~18 characters Claude Code truncates every line and breaks
 * single words across rows ("Clau/de/Max").
 *
 * Nothing lays out by it. The workspace is always one screenful (see the header
 * above), so this number's only job is to let the wizard say "twelve panes on
 * this window is about 130 px each, which is tight" BEFORE the user commits to
 * twelve — the honest form of a warning, rather than a workspace that quietly
 * grows a scrollbar.
 */
export const COMFORTABLE_PANE_WIDTH_PX = 380;

/**
 * Horizontal padding of the rendered grid — 4 px on each side.
 *
 * It mirrors `GRID_GAP_PX` in AgenticGrid, and the two must not drift: the
 * wizard estimates a pane's width from the grid's CONTENT width, so a padding
 * the layout module does not know about makes the preview's advice slightly
 * wrong about the workspace it is previewing.
 */
export const GRID_HORIZONTAL_PADDING_PX = 8;

/**
 * How wide each of ``columns`` panes ends up at ``containerWidthPx``.
 *
 * The workspace is always exactly its window (see the header), so this is a
 * plain division — and that is the point: it is the number the wizard's readout
 * quotes, so "twelve terminals" is a decision made with its consequence in view
 * rather than one discovered afterwards.
 *
 * Takes an OUTER width (the wizard measures an unpadded element) and subtracts
 * the padding the grid will have, so it answers for the same physical window
 * the running grid does. Never negative, and 0 for an empty workspace.
 */
export function paneWidthAt(columns: number, containerWidthPx: number): number {
  const content = Math.max(0, containerWidthPx - GRID_HORIZONTAL_PADDING_PX);
  const count = Math.max(0, Math.trunc(columns));
  if (count === 0 || !Number.isFinite(content) || content <= 0) return 0;
  return content / count;
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
 * Is ``count`` panes comfortable on a window this wide, or merely possible?
 *
 * Every count fits — the workspace is always one screenful — so the only thing
 * left to say is how much room each pane gets, and this is where that turns
 * into a yes or no. Deliberately about the PANE rather than the count: the same
 * eight terminals are roomy on a 4K display and cramped on a laptop.
 */
export function panesAreComfortable(
  count: number,
  containerWidthPx: number,
): boolean {
  const width = paneWidthAt(count, containerWidthPx);
  // An unmeasured container says nothing yet — and "we have not measured" must
  // not render as a warning, or the wizard opens shouting at every user once.
  return width === 0 || width >= COMFORTABLE_PANE_WIDTH_PX;
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
