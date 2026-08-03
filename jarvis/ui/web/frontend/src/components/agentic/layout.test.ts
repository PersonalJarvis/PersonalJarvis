import { describe, expect, it } from "vitest";
import {
  GRID_HORIZONTAL_PADDING_PX,
  MIN_PANE_WIDTH_PX,
  columnsWithoutScrolling,
  paneGrid,
  widthForAllVisible,
  wizardPanes,
  workspaceWidthFor,
} from "./layout";

/*
 * The thresholds are expressed as "content width + the grid's own padding"
 * rather than as the literal pixel numbers they came to.
 *
 * Those literals were what made this file break when the grid was tightened
 * from 12 px of padding a side to 4 — a purely visual change that has no
 * business moving a threshold, and did not: only the OUTER width at which it is
 * crossed moved, by exactly the padding.
 */
const FOUR_COLUMNS_AT = 4 * MIN_PANE_WIDTH_PX;
const SIX_COLUMNS_AT = 6 * MIN_PANE_WIDTH_PX;

describe("workspaceWidthFor", () => {
  it("is exactly the window while the columns are readable in it", () => {
    // Nothing scrolls in the ordinary case: four columns in a window wide
    // enough for six are four columns of a quarter of the window each.
    expect(workspaceWidthFor(4, SIX_COLUMNS_AT)).toBe(SIX_COLUMNS_AT);
  });

  it("grows past the window rather than squeezing the columns", () => {
    // The 2026-08-03 report: a sixth terminal on a five-column window used to
    // wrap onto a second line, which halved the five panes the user was already
    // reading. Now the workspace is six columns wide and the grid scrolls.
    const window = 5 * MIN_PANE_WIDTH_PX;
    expect(workspaceWidthFor(6, window)).toBe(6 * MIN_PANE_WIDTH_PX);
  });

  it("never lets a column fall below the readable floor, at any count", () => {
    // Measured against a real agent on 2026-07-25: below ~380 px Claude Code
    // truncates every line and breaks single words across rows.
    for (const columns of [1, 2, 6, 7, 12, 40, 100]) {
      const width = workspaceWidthFor(columns, 1440);
      expect(width / columns).toBeGreaterThanOrEqual(MIN_PANE_WIDTH_PX);
    }
  });

  it("is the window itself for an empty workspace", () => {
    expect(workspaceWidthFor(0, 1440)).toBe(1440);
  });
});

describe("columnsWithoutScrolling", () => {
  it("uses the grid content width on both sides of the four-column threshold", () => {
    const outer = FOUR_COLUMNS_AT + GRID_HORIZONTAL_PADDING_PX;
    expect(columnsWithoutScrolling(outer - 1)).toBe(3);
    expect(columnsWithoutScrolling(outer)).toBe(4);
  });

  it("uses the grid content width on both sides of the six-column threshold", () => {
    const outer = SIX_COLUMNS_AT + GRID_HORIZONTAL_PADDING_PX;
    expect(columnsWithoutScrolling(outer - 1)).toBe(5);
    expect(columnsWithoutScrolling(outer)).toBe(6);
  });

  it("gives a laptop fewer visible columns than a large display", () => {
    expect(columnsWithoutScrolling(1440)).toBeLessThan(
      columnsWithoutScrolling(2560),
    );
  });

  it("never returns zero — one cramped column still beats none", () => {
    expect(columnsWithoutScrolling(120)).toBe(1);
    expect(columnsWithoutScrolling(0)).toBe(1);
    expect(columnsWithoutScrolling(Number.NaN)).toBe(1);
  });

  it("has no ceiling — a wide enough window shows however many there are", () => {
    // The old capacity helper stopped at ten whatever the display. A 4K wall
    // really does fit more than ten readable columns, and nothing about the
    // count is capped any more.
    expect(columnsWithoutScrolling(100_000)).toBeGreaterThan(10);
  });
});

describe("wizardPanes", () => {
  it("is the one row of columns the backend opens a workspace with", () => {
    // Mirrors agentic_ide/session.py, which sets column=index and slot=0 for a
    // wizard-opened workspace. The preview feeds these to the same `paneGrid`
    // the running workspace uses, so it cannot describe a layout the backend
    // would never build.
    expect(wizardPanes(3)).toEqual([
      { column: 0, slot: 0 },
      { column: 1, slot: 0 },
      { column: 2, slot: 0 },
    ]);
  });

  it("has no panes for an empty or nonsensical count", () => {
    expect(wizardPanes(0)).toEqual([]);
    expect(wizardPanes(-4)).toEqual([]);
  });

  it("lays out exactly like the workspace it stands for, at any window size", () => {
    // 8 terminals are 8 columns — on a 4K display and on a laptop alike. The
    // window decides how many are ON SCREEN, never how they are arranged.
    const grid = paneGrid(wizardPanes(8));
    expect(grid.columns).toBe(8);
    expect(grid.rows).toBe(1);
  });
});

describe("widthForAllVisible", () => {
  it("names the width at which a count stops needing a sideways scroll", () => {
    // What the readout tells the user, so a workspace that scrolls is never a
    // surprise: eight panes need 8 × 380 px plus the grid padding.
    expect(widthForAllVisible(8)).toBe(
      8 * MIN_PANE_WIDTH_PX + GRID_HORIZONTAL_PADDING_PX,
    );
    // And it agrees with the helper the wizard measures through, rather than
    // being a second opinion about the same threshold.
    expect(columnsWithoutScrolling(widthForAllVisible(8) as number)).toBe(8);
  });

  it("has an answer for every count — no width is ever 'not enough'", () => {
    // The old helper returned null past ten, where the wrap was unavoidable.
    // Nothing wraps now, so every count has a width that shows all of it.
    expect(widthForAllVisible(24)).toBe(
      24 * MIN_PANE_WIDTH_PX + GRID_HORIZONTAL_PADDING_PX,
    );
  });

  it("has nothing to offer when one column is the whole workspace", () => {
    expect(widthForAllVisible(1)).toBeNull();
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
    // The user-facing contract, pinned directly: for any count, adding a column
    // changes no existing placement. It used to hold only below the wrap.
    for (let count = 1; count < 40; count += 1) {
      const before = paneGrid(wizardPanes(count)).placements;
      const after = paneGrid(wizardPanes(count + 1)).placements;
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
