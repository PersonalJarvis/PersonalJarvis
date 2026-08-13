import { describe, expect, it } from "vitest";
import {
  COMFORTABLE_PANE_WIDTH_PX,
  GRID_HORIZONTAL_PADDING_PX,
  PANE_TERMINAL_INSET_PX,
  WORKABLE_COLS,
  columnDepthFor,
  paneColumnsAt,
  paneGrid,
  paneWidthAt,
  panesAreComfortable,
  wizardPanes,
  workableColumnCount,
} from "./layout";

/*
 * The workspace the bug was reported on (2026-08-13), in numbers.
 *
 * A 1 920 px window with the section rail beside it, at the maintainer's own
 * text size — where twelve terminals opened in silence and every pane landed at
 * a width no coding CLI can draw in.
 */
const REPORTED_WINDOW_PX = 1740;
const CELL_AT_SIZE_20 = 12;

describe("how many columns a pane really gets", () => {
  it("answers in the unit that decides, not in pixels", () => {
    // Twelve across on that window: ~145 px each, which sounds survivable and
    // is not — it is thirteen columns, and a coding CLI needs about sixty.
    const cols = paneColumnsAt(12, REPORTED_WINDOW_PX, CELL_AT_SIZE_20);
    expect(cols).toBeLessThan(WORKABLE_COLS);
    expect(cols).toBe(
      Math.floor(
        (paneWidthAt(12, REPORTED_WINDOW_PX) - PANE_TERMINAL_INSET_PX) /
          CELL_AT_SIZE_20,
      ),
    );
  });

  it("gives the same panes a different answer at a smaller text size", () => {
    // The half a fixed count of twenty can never see: the SAME four panes on
    // the SAME window are two different decisions, and only the text size
    // separates them.
    expect(paneColumnsAt(4, REPORTED_WINDOW_PX, CELL_AT_SIZE_20)).toBeLessThan(
      WORKABLE_COLS,
    );
    expect(paneColumnsAt(4, REPORTED_WINDOW_PX, 6)).toBeGreaterThanOrEqual(
      WORKABLE_COLS,
    );
  });

  it("has nothing to say where nothing can be measured", () => {
    // jsdom, and any environment with no canvas. "We could not measure" must
    // never render as a warning.
    expect(paneColumnsAt(12, REPORTED_WINDOW_PX, 0)).toBe(0);
    expect(paneColumnsAt(12, 0, CELL_AT_SIZE_20)).toBe(0);
    expect(paneColumnsAt(12, REPORTED_WINDOW_PX, Number.NaN)).toBe(0);
  });
});

describe("how many panes a window fits", () => {
  it("counts the panes that would still be drawable", () => {
    const across = workableColumnCount(REPORTED_WINDOW_PX, CELL_AT_SIZE_20);
    expect(across).toBeGreaterThan(0);
    expect(paneColumnsAt(across, REPORTED_WINDOW_PX, CELL_AT_SIZE_20)).toBeGreaterThanOrEqual(
      WORKABLE_COLS,
    );
    // And one more would not be.
    expect(paneColumnsAt(across + 1, REPORTED_WINDOW_PX, CELL_AT_SIZE_20)).toBeLessThan(
      WORKABLE_COLS,
    );
  });

  it("never answers zero on a measured window", () => {
    // A window too narrow for one workable pane cannot be fixed by opening
    // fewer, and "0 across" is not an arrangement anyone can choose.
    expect(workableColumnCount(200, CELL_AT_SIZE_20)).toBe(1);
  });

  it("has nothing to say where nothing can be measured", () => {
    expect(workableColumnCount(REPORTED_WINDOW_PX, 0)).toBe(0);
    expect(workableColumnCount(0, CELL_AT_SIZE_20)).toBe(0);
  });
});

/*
 * The widths are expressed as "content width + the grid's own padding" rather
 * than as the literal pixel numbers they come to.
 *
 * Those literals were what made this file break when the grid was tightened
 * from 12 px of padding a side to 4 — a purely visual change that has no
 * business moving a threshold, and did not: only the OUTER width at which it is
 * crossed moved, by exactly the padding.
 */
const FOUR_COMFORTABLE_AT = 4 * COMFORTABLE_PANE_WIDTH_PX;

describe("paneWidthAt", () => {
  it("divides the window between the columns, whatever the count", () => {
    // The rule this whole module now serves (maintainer, 2026-08-04): the
    // workspace is always one screenful, so a pane is a SHARE of the window and
    // never a fixed size the window has to grow to accommodate.
    const outer = FOUR_COMFORTABLE_AT + GRID_HORIZONTAL_PADDING_PX;
    expect(paneWidthAt(4, outer)).toBe(COMFORTABLE_PANE_WIDTH_PX);
    expect(paneWidthAt(8, outer)).toBe(COMFORTABLE_PANE_WIDTH_PX / 2);
    expect(paneWidthAt(40, outer)).toBe(COMFORTABLE_PANE_WIDTH_PX / 10);
  });

  it("answers for the grid's content width, not the element around it", () => {
    // The wizard measures an unpadded element; the running grid pads itself.
    // A helper that ignored that would quote a width the workspace never has.
    expect(paneWidthAt(1, 1000)).toBe(1000 - GRID_HORIZONTAL_PADDING_PX);
  });

  it("has nothing to divide for an empty or unmeasured workspace", () => {
    expect(paneWidthAt(0, 1440)).toBe(0);
    expect(paneWidthAt(4, 0)).toBe(0);
    expect(paneWidthAt(4, Number.NaN)).toBe(0);
  });
});

describe("panesAreComfortable", () => {
  it("turns on either side of the readable width, measured against a real agent", () => {
    // 2026-07-25, against Claude Code: below ~380 px it truncates every line and
    // breaks single words across rows ("Clau/de/Max"). That is now ADVICE the
    // wizard gives before anything opens — it no longer moves a single pixel.
    const outer = FOUR_COMFORTABLE_AT + GRID_HORIZONTAL_PADDING_PX;
    expect(panesAreComfortable(4, outer)).toBe(true);
    expect(panesAreComfortable(5, outer)).toBe(false);
  });

  it("is about the pane, not the count — the same eight differ by display", () => {
    const laptop = 1440;
    const wall = 8 * COMFORTABLE_PANE_WIDTH_PX + GRID_HORIZONTAL_PADDING_PX;
    expect(panesAreComfortable(8, laptop)).toBe(false);
    expect(panesAreComfortable(8, wall)).toBe(true);
  });

  it("says nothing rather than warning while the container is unmeasured", () => {
    // A first paint reports 0. Rendering that as "these panes will be cramped"
    // means every user is shouted at once, for a reading that was never taken.
    expect(panesAreComfortable(12, 0)).toBe(true);
  });
});

describe("columnDepthFor", () => {
  /*
   * The workspace from the report, at the numbers it was actually measured at:
   * a 2560 px window, six panes, and a terminal that needs ~660 px to show its
   * 60-column grid at the maintainer's text size. Six in a row gave each ~410,
   * so every pane drew a third of itself past its own edge.
   */
  const REPORTED = {
    paneCount: 6,
    canvasWidthPx: 2560,
    canvasHeightPx: 900,
    neededPaneWidthPx: 660,
    minPaneHeightPx: 380,
  };

  it("folds the reported workspace into two rows, and no deeper", () => {
    // 2560 px carries three 660 px columns, so six panes need two per column —
    // 3x2, each pane ~850 px, every terminal fully visible at the SAME text size.
    expect(columnDepthFor(REPORTED)).toBe(2);
  });

  it("leaves a workspace alone when one row already fits", () => {
    // The same six panes on a display wide enough for six full columns. Folding
    // here would be a rearrangement behind the user's back for no gain at all.
    expect(columnDepthFor({ ...REPORTED, canvasWidthPx: 6 * 660 + 100 })).toBe(1);
  });

  it("does not fold on a guess when no pane reported being clipped", () => {
    // `neededPaneWidthPx` is 0 exactly when every terminal already shows itself.
    // That is evidence of health, not a missing measurement.
    expect(columnDepthFor({ ...REPORTED, neededPaneWidthPx: 0 })).toBe(1);
  });

  it("stays put while the canvas is still unmeasured", () => {
    // A first paint reports 0. Folding on it would reshape every workspace once
    // on open, before anything had been measured at all.
    expect(columnDepthFor({ ...REPORTED, canvasWidthPx: 0 })).toBe(1);
    expect(columnDepthFor({ ...REPORTED, canvasWidthPx: Number.NaN })).toBe(1);
  });

  it("stops folding once the panes would be too short to hold an agent", () => {
    // Twelve panes on a laptop want six deep on width alone. A 900 px canvas
    // carries two panes of 380 px, so the fold stops at two: a pane wide enough
    // to read but too short to show the agent's input box is the worse half of
    // the same trade (see MIN_REAL_ROWS in ./AgenticTerminal).
    expect(
      columnDepthFor({ ...REPORTED, paneCount: 12, canvasWidthPx: 1440 }),
    ).toBe(2);
  });

  it("lets height cap nothing while height is unknown", () => {
    // Width has been measured and height has not — capping at 1 there would
    // silently refuse the fold the width evidence already justifies.
    expect(
      columnDepthFor({ ...REPORTED, paneCount: 12, canvasWidthPx: 1440, canvasHeightPx: 0 }),
    ).toBe(Math.ceil(12 / Math.max(1, Math.floor((1440 - GRID_HORIZONTAL_PADDING_PX) / 660))));
  });

  it("has nothing to fold below two panes", () => {
    expect(columnDepthFor({ ...REPORTED, paneCount: 1 })).toBe(1);
    expect(columnDepthFor({ ...REPORTED, paneCount: 0 })).toBe(1);
  });

  it("keeps a single pane per column when even one will not fit", () => {
    // A window narrower than one usable terminal cannot be repaired by folding.
    // It must still answer with a real depth rather than dividing by zero.
    const depth = columnDepthFor({ ...REPORTED, canvasWidthPx: 300 });
    expect(depth).toBe(2);
    expect(Number.isFinite(depth)).toBe(true);
  });
});

describe("wizardPanes", () => {
  it("is the columns of two the backend opens a workspace with", () => {
    // Mirrors agentic_ide/session.py, which fills a column to
    // WIZARD_COLUMN_HEIGHT before opening the next one. The preview feeds these
    // to the same `paneGrid` the running workspace uses, so it cannot describe
    // a layout the backend would never build.
    expect(wizardPanes(3)).toEqual([
      { column: 0, slot: 0 },
      { column: 0, slot: 1 },
      { column: 1, slot: 0 },
    ]);
  });

  it("has no panes for an empty or nonsensical count", () => {
    expect(wizardPanes(0)).toEqual([]);
    expect(wizardPanes(-4)).toEqual([]);
  });

  it("halves the columns a count is spread over, which is what pane width is", () => {
    // The 2026-08-11 report: six terminals in one row left every pane about
    // 410 px on the maintainer's display, well under the width its agent needs,
    // so each pane was clipped at its tile edge and the six read as overlapping.
    // Opened two deep the same six are three columns — double the width each.
    const grid = paneGrid(wizardPanes(6));
    expect(grid.columns).toBe(3);
    expect(grid.rows).toBe(2);
  });

  it("stands the odd one out at full height beside a filled column", () => {
    // Three terminals: a column of two, and a third that reaches the same
    // bottom edge rather than leaving a hole under itself.
    const grid = paneGrid(wizardPanes(3));
    expect(grid.placements).toEqual([
      { column: 1, row: 1, rowSpan: 1 },
      { column: 1, row: 2, rowSpan: 1 },
      { column: 2, row: 1, rowSpan: 2 },
    ]);
  });

  it("arranges the same way at any window size", () => {
    // 8 terminals are 4 columns of 2 — on a 4K display and on a laptop alike.
    // The window decides how much room each pane gets, never the arrangement.
    const grid = paneGrid(wizardPanes(8));
    expect(grid.columns).toBe(4);
    expect(grid.rows).toBe(2);
  });
});

describe("paneGrid", () => {
  const pane = (column: number, slot: number) => ({ column, slot });

  it("puts a fresh workspace side by side on one row", () => {
    const grid = paneGrid([pane(0, 0), pane(1, 0), pane(2, 0)]);
    expect(grid.columns).toBe(3);
    expect(grid.rows).toBe(1);
    expect(grid.placements).toEqual([
      { column: 1, row: 1, rowSpan: 1 },
      { column: 2, row: 1, rowSpan: 1 },
      { column: 3, row: 1, rowSpan: 1 },
    ]);
  });

  it("keeps every column on one line however many there are", () => {
    // The 2026-08-03 report, at the layout level: the twelfth column is the
    // twelfth column, not the second one of a new row. Only the user's split
    // buttons decide which panes share space.
    const grid = paneGrid(Array.from({ length: 12 }, (_, i) => pane(i, 0)));
    expect(grid.columns).toBe(12);
    expect(grid.rows).toBe(1);
    expect(grid.placements.map((p) => p.column)).toEqual([
      1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12,
    ]);
    expect(grid.placements.every((p) => p.row === 1)).toBe(true);
  });

  it("splits DOWN inside one column and leaves the others full height", () => {
    // The whole point of the two-axis model: splitting the middle pane must not
    // halve the panes beside it, which is what a full-width row used to do.
    const grid = paneGrid([pane(0, 0), pane(1, 0), pane(1, 1), pane(2, 0)]);
    expect(grid.columns).toBe(3);
    expect(grid.rows).toBe(2);
    expect(grid.placements).toEqual([
      { column: 1, row: 1, rowSpan: 2 }, // untouched neighbour, still full height
      { column: 2, row: 1, rowSpan: 1 }, // the anchor, now the top half
      { column: 2, row: 2, rowSpan: 1 }, // the pane the split opened
      { column: 3, row: 1, rowSpan: 2 },
    ]);
  });

  it("makes columns of different depths end flush at the bottom", () => {
    // A column of 2 next to a column of 3: six rows, spanned 3 and 2, so both
    // columns fill exactly the same height.
    const grid = paneGrid([
      pane(0, 0),
      pane(0, 1),
      pane(1, 0),
      pane(1, 1),
      pane(1, 2),
    ]);
    expect(grid.rows).toBe(6);
    expect(grid.placements).toEqual([
      { column: 1, row: 1, rowSpan: 3 },
      { column: 1, row: 4, rowSpan: 3 },
      { column: 2, row: 1, rowSpan: 2 },
      { column: 2, row: 3, rowSpan: 2 },
      { column: 2, row: 5, rowSpan: 2 },
    ]);
  });

  it("never moves an existing column when one more is opened", () => {
    // The user-facing contract, pinned directly: for any count, splitting RIGHT
    // changes no existing placement. It used to hold only below the wrap.
    // Deliberately built from split panes rather than `wizardPanes` — the
    // wizard opens columns of two now, so it is no longer a stand-in for
    // "one more column", which is what this contract is about.
    const columns = (n: number) =>
      Array.from({ length: n }, (_, i) => pane(i, 0));
    for (let count = 1; count < 40; count += 1) {
      const before = paneGrid(columns(count)).placements;
      const after = paneGrid(columns(count + 1)).placements;
      expect(after.slice(0, count)).toEqual(before);
    }
  });

  it("closes gaps the backend left in the column numbers", () => {
    // close_terminal() re-packs columns, but a session read mid-change can
    // still carry a gap — an empty column would render as a blank stripe.
    const grid = paneGrid([pane(0, 0), pane(4, 0)]);
    expect(grid.columns).toBe(2);
    expect(grid.placements.map((p) => p.column)).toEqual([1, 2]);
  });

  it("orders a column by slot, not by arrival", () => {
    const grid = paneGrid([pane(0, 2), pane(0, 0), pane(0, 1)]);
    expect(grid.placements.map((p) => p.row)).toEqual([3, 1, 2]);
  });

  it("handles an empty workspace", () => {
    expect(paneGrid([])).toEqual({ columns: 0, rows: 0, placements: [] });
  });
});
