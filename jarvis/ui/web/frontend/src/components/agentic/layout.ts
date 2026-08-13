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
 * ## However many columns there are — and ALWAYS one screenful
 *
 * The workspace never wraps. Opening a column always puts it beside the last
 * one, and a workspace of twenty columns is twenty columns across. Nothing here
 * refuses a count or rearranges one behind the user's back: thirty columns on a
 * laptop is a thing they may build, and on a wall display it is a reasonable
 * thing to want. The only shape this module decides is the one a workspace
 * OPENS in (see `wizardPanes`), and every later split, drag and close is the
 * user's.
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
 * The width below which a coding CLI stops drawing a frame anyone can read.
 *
 * 60 is where both installed CLIs were measured to stop laying out a usable
 * frame (2026-08-09, thirteen panes). Below it a TUI does not shrink tidily —
 * it reserves its gutter out of what little there is, lays the remainder out
 * one and two characters wide, and then repaints over rows that no longer hold
 * what it drew, which is how panes that had been working for an hour came back
 * BLANK (reported 2026-08-13).
 *
 * It lives in this leaf module because THREE places have to agree on it and one
 * of them must not import a terminal: the pane holds its agent's columns here
 * (`MIN_REAL_COLS` and `PaneTooNarrowCard` in ./AgenticTerminal), and the wizard
 * says so before anything opens (./WorkspaceShape). A wizard that quoted a
 * different number from the one the panes act on would be advice about a
 * different app.
 */
export const WORKABLE_COLS = 60;

/**
 * The pane chrome between a tile's edge and the terminal grid inside it, in px.
 *
 * Mirrors the horizontal padding on the terminal region in ./AgenticTerminal
 * (`px-1.5`, so 6 px a side). Like {@link GRID_HORIZONTAL_PADDING_PX} the two
 * must not drift: the wizard predicts a pane's COLUMN count from its tile, and
 * an inset it does not know about makes that prediction wrong in the direction
 * that matters — optimistic.
 */
export const PANE_TERMINAL_INSET_PX = 12;

/**
 * How many terminal COLUMNS each of ``columns`` panes gets, at this text size.
 *
 * The unit the decision is actually made in. A pane's width in pixels means
 * nothing on its own — 145 px is roomy at 8 px text and unusable at 20 — and
 * the number that decides whether a coding CLI can draw at all is its column
 * count (see {@link WORKABLE_COLS}).
 *
 * ``cellWidthPx`` is measured from the real font by the caller
 * (`measureAdvance` in @/lib/terminalFont), never assumed here: this module has
 * no DOM and must not invent an advance width. 0 — an environment that cannot
 * measure — gives 0 back, which every caller reads as "nothing to say yet".
 *
 * The same estimate the readout's pixel figure comes from, deliberately: the
 * two numbers stand beside each other and may not disagree. A pane's own fit is
 * the authority once it exists; this is what can be said before it does.
 */
export function paneColumnsAt(
  columns: number,
  containerWidthPx: number,
  cellWidthPx: number,
): number {
  if (!Number.isFinite(cellWidthPx) || cellWidthPx <= 0) return 0;
  const tile = paneWidthAt(columns, containerWidthPx);
  if (tile <= 0) return 0;
  return Math.max(0, Math.floor((tile - PANE_TERMINAL_INSET_PX) / cellWidthPx));
}

/**
 * How many panes fit ACROSS this window before they stop being drawable.
 *
 * What the wizard's warning is built on, and the honest form of a question that
 * used to be answered by a fixed count of twenty. Twenty is blind to both
 * halves of the thing it is guessing at — the window and the reader's text size
 * — which is why twelve terminals opened silently on a 1 920 px window at text
 * size 20 and left every pane at thirteen columns.
 *
 * At least 1: a window too narrow for a single workable pane cannot be made
 * wider by opening fewer, and 0 across is not an arrangement anyone can choose.
 * 0 only for an unmeasured window, which means "no answer yet", not "none fit".
 */
export function workableColumnCount(
  containerWidthPx: number,
  cellWidthPx: number,
): number {
  if (!Number.isFinite(cellWidthPx) || cellWidthPx <= 0) return 0;
  const content = Math.max(0, containerWidthPx - GRID_HORIZONTAL_PADDING_PX);
  if (!Number.isFinite(content) || content <= 0) return 0;
  const perPane = WORKABLE_COLS * cellWidthPx + PANE_TERMINAL_INSET_PX;
  return Math.max(1, Math.floor(content / perPane));
}

/**
 * The count from which opening a workspace has to be confirmed out loud.
 *
 * NOT a limit, and deliberately not derived from the window either. The
 * maintainer's rule for this screen (2026-08-11) is that the number of
 * terminals is the user's call — thirty side by side is a thing somebody may
 * want, and on a wall display it is even readable. What the app does not know
 * is how big that display is, so it may not decide FOR them.
 *
 * What it can do is make sure the decision was made. Twenty is where a
 * workspace stops being roomy on any ordinary screen, so from here the wizard
 * says so and waits for a yes. Below it nothing is asked, because a question
 * asked every time is a question nobody reads.
 *
 * Distinct from `COMFORTABLE_PANE_WIDTH_PX` on purpose: that one is measured
 * against the window and phrased as advice, this one is a fixed count and
 * blocks until it is acknowledged. A user on a laptop meets the advice long
 * before this, and a user on a video wall meets only this.
 */
export const CROWDED_TERMINAL_COUNT = 20;

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
 * How deep a wizard-opened column is filled before the next one is started.
 *
 * Two, because the workspace is always one screenful (see the header) and the
 * column is the only axis that can absorb a pane without taking width from
 * every other one. One row of columns — what this used to be — spends the whole
 * window on a single line, so the sixth terminal left every pane about 410 px
 * wide at the maintainer's own text size: well under the ~650 px a 60-column
 * agent grid needs there, and therefore six panes each showing two thirds of
 * themselves with the rest clipped at the tile edge (reported 2026-08-11, and
 * read as the panes overlapping one another).
 *
 * Two deep halves the column count and so doubles the width every pane gets,
 * which is the axis the clipping is on. Deliberately not "as square as
 * possible": past four terminals a squarer grid starts paying in HEIGHT
 * instead, and a coding CLI anchors its input box to its bottom row — a pane
 * wide enough to read but too short to hold the agent's interface is the worse
 * half of the same trade.
 *
 * Only the OPENING shape. Nothing holds a workspace at two deep afterwards: the
 * user's own splits, drags and closes rearrange it freely, and a wall of thirty
 * columns is theirs to build if they want one.
 */
export const WIZARD_COLUMN_HEIGHT = 2;

/**
 * The panes a wizard-opened workspace of ``count`` terminals starts with.
 *
 * Columns of {@link WIZARD_COLUMN_HEIGHT}, each filled top to bottom before the
 * next is opened — which is literally what the backend writes when it opens a
 * session from the wizard (`agentic_ide/session.py`, the same arithmetic). Two
 * terminals stand one above the other; three are a full column plus one beside
 * it; six are three columns of two.
 *
 * It exists so the preview cannot describe a workspace the backend would never
 * build. An earlier preview took a shortcut and derived its dots from the raw
 * terminal COUNT, which happened to agree only while every terminal got a
 * column of its own. Feed `paneGrid` the same panes the grid will receive and
 * the preview stops being a second opinion.
 */
export function wizardPanes(count: number): Positioned[] {
  return Array.from({ length: Math.max(0, Math.trunc(count)) }, (_, index) => ({
    column: Math.floor(index / WIZARD_COLUMN_HEIGHT),
    slot: index % WIZARD_COLUMN_HEIGHT,
  }));
}

/**
 * Is a workspace ``columns`` across comfortable on a window this wide, or
 * merely possible?
 *
 * Every arrangement fits — the workspace is always one screenful — so the only
 * thing left to say is how much room each pane gets, and this is where that
 * turns into a yes or no. Deliberately about the PANE rather than the terminal
 * count: the same eight terminals are roomy on a 4K display and cramped on a
 * laptop, and eight opened as four columns of two are twice the width of eight
 * in a row (see {@link WIZARD_COLUMN_HEIGHT}). Callers pass the COLUMN count
 * for that reason — the number of panes across, not the number of panes.
 */
export function panesAreComfortable(
  columns: number,
  containerWidthPx: number,
): boolean {
  const width = paneWidthAt(columns, containerWidthPx);
  // An unmeasured container says nothing yet — and "we have not measured" must
  // not render as a warning, or the wizard opens shouting at every user once.
  return width === 0 || width >= COMFORTABLE_PANE_WIDTH_PX;
}

/** What `columnDepthFor` needs to know about the workspace on screen. */
export interface RefoldMeasurements {
  /** How many panes the workspace holds. */
  paneCount: number;
  /** The canvas all of them share, in px. */
  canvasWidthPx: number;
  canvasHeightPx: number;
  /**
   * The width ONE pane needs to show the whole terminal it is drawing.
   *
   * Measured by the panes themselves and reported upwards, never estimated
   * here. Only a pane knows the reader's
   * chosen text size, and the figure that matters is `MIN_REAL_COLS` cells at
   * exactly that size plus its own frame — arithmetic this module would have to
   * duplicate, and would then get subtly wrong on the day a padding changes.
   *
   * Zero means nothing is clipped, which is the answer "one row is fine".
   */
  neededPaneWidthPx: number;
  /**
   * Below this a pane is too SHORT to be worth the width it just bought.
   *
   * A coding CLI anchors its input box to its bottom row, so a pane that has
   * been folded until it can no longer hold the agent's interface has traded
   * one unreadable pane for another (the same trade `MIN_REAL_ROWS` refuses in
   * `./AgenticTerminal`). This is what stops the fold before that point.
   */
  minPaneHeightPx: number;
}

/**
 * How deep the workspace's columns have to be for every pane to show itself.
 *
 * The workspace is always exactly one screenful — it never scrolls and never
 * grows (see the header) — so the only room a pane can be given is room taken
 * from somewhere else. Width is the axis that is failing: at the maintainer's
 * text size six panes in a row are ~410 px each where a 60-column agent grid
 * needs ~660, so a third of every terminal is drawn past its own tile edge and
 * clipped (reported 2026-08-11, and read as the panes overlapping each other).
 *
 * Height is the axis that can pay for it. Folding the row in two halves the
 * column count, and half as many columns are twice as wide — the panes get the
 * width they were short of, and the text size the user chose is untouched. That
 * ordering is the whole decision: shrinking the font instead was tried and
 * withdrawn the same day it shipped (2026-08-11), because it silently overrode
 * the toolbar's own size control.
 *
 * So: the smallest depth at which every pane clears `neededPaneWidthPx`, and
 * never deeper than the height can carry. Depth 1 — one row, the shape the
 * workspace has always opened in — whenever that already fits, because a fold
 * nobody needs is a rearrangement behind the user's back.
 *
 * Returns a DEPTH (panes stacked per column), not a column count: that is what
 * both `wizardPanes` and the backend's re-fold are expressed in.
 */
export function columnDepthFor(measurements: RefoldMeasurements): number {
  const panes = Math.max(0, Math.trunc(measurements.paneCount));
  const needed = measurements.neededPaneWidthPx;
  if (panes <= 1) return 1;
  // Nothing measured, or nothing clipped — either way there is no evidence the
  // workspace is too narrow, and this must never fold on a guess.
  if (!Number.isFinite(needed) || needed <= 0) return 1;
  if (!Number.isFinite(measurements.canvasWidthPx) || measurements.canvasWidthPx <= 0) {
    return 1;
  }

  const content = Math.max(0, measurements.canvasWidthPx - GRID_HORIZONTAL_PADDING_PX);
  // How many columns still leave each one wide enough. At least one: a window
  // too narrow for a single pane cannot be fixed by folding, and zero columns
  // would divide by zero on the way to an infinite depth.
  const affordable = Math.max(1, Math.floor(content / needed));
  if (affordable >= panes) return 1;

  const wanted = Math.ceil(panes / affordable);

  // The ceiling the other axis imposes. An unmeasured height must not cap
  // anything — during the first frames the canvas reports 0, and a cap of 1
  // there would decide "one row" for a workspace nobody has measured yet.
  const height = measurements.canvasHeightPx;
  const floorPx = measurements.minPaneHeightPx;
  if (!Number.isFinite(height) || height <= 0 || !Number.isFinite(floorPx) || floorPx <= 0) {
    return wanted;
  }
  const bearable = Math.max(1, Math.floor(height / floorPx));
  return Math.min(wanted, bearable);
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
