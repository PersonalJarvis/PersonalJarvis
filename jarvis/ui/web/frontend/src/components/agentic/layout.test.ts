import { describe, expect, it } from "vitest";
import {
  GRID_HORIZONTAL_PADDING_PX,
  MAX_PANES_PER_BAND,
  MIN_PANE_WIDTH_PX,
  bandCapacityFor,
  paneColumns,
  paneGrid,
  paneLines,
  widthForOneBand,
  wizardPanes,
  workspaceBandCapacityFor,
} from "./layout";

/*
 * The thresholds are expressed as "content width + the grid's own padding"
 * rather than as the literal pixel numbers they came to.
 *
 * Those literals were what made this file break when the grid was tightened
 * from 12 px of padding a side to 4 — a purely visual change that has no
 * business moving a column threshold, and did not: only the OUTER width at
 * which it is crossed moved, by exactly the padding. Written this way the tests
 * still pin the real contract (both helpers answer for the same window) and
 * stop failing over the frame around it.
 */
const FOUR_COLUMNS_AT = 4 * MIN_PANE_WIDTH_PX;
const SIX_COLUMNS_AT = 6 * MIN_PANE_WIDTH_PX;

describe("workspaceBandCapacityFor", () => {
  it("uses the grid content width on both sides of the four-column threshold", () => {
    const outer = FOUR_COLUMNS_AT + GRID_HORIZONTAL_PADDING_PX;
    expect(workspaceBandCapacityFor(outer - 1)).toBe(3);
    expect(workspaceBandCapacityFor(outer)).toBe(4);
  });

  it("uses the grid content width on both sides of the six-column threshold", () => {
    const outer = SIX_COLUMNS_AT + GRID_HORIZONTAL_PADDING_PX;
    expect(workspaceBandCapacityFor(outer - 1)).toBe(5);
    expect(workspaceBandCapacityFor(outer)).toBe(6);
  });
});

describe("the two width helpers say which width they take", () => {
  it("separates the grid's own content width from the outer width", () => {
    // The defect they exist to prevent: the running grid measures its CONTENT
    // box (padding already excluded) while the wizard measures an unpadded
    // element. Feeding both to one helper made the grid lay itself out a
    // padding's width narrower than it is, so the preview and the workspace
    // changed column count at different window widths and the preview looked
    // like a liar.
    expect(bandCapacityFor(FOUR_COLUMNS_AT)).toBe(4);
    expect(
      workspaceBandCapacityFor(FOUR_COLUMNS_AT + GRID_HORIZONTAL_PADDING_PX),
    ).toBe(4);
    // Same physical window, same answer — which is the whole point.
    expect(bandCapacityFor(FOUR_COLUMNS_AT - 1)).toBe(3);
    expect(
      workspaceBandCapacityFor(
        FOUR_COLUMNS_AT - 1 + GRID_HORIZONTAL_PADDING_PX,
      ),
    ).toBe(3);
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

  it("lays out exactly like the workspace it stands for", () => {
    // 8 terminals in a window wide enough for 4 columns: 4 across, 2 down —
    // the arrangement reported as missing from the preview on 2026-07-26.
    const grid = paneGrid(wizardPanes(8), 4);
    expect(grid.columns).toBe(4);
    expect(paneLines(8, 4)).toBe(2);
    // The same 8 in a narrow window really are 2 across and 4 down. Both are
    // correct; the preview has to say which one it is showing.
    expect(paneGrid(wizardPanes(8), 2).columns).toBe(2);
    expect(paneLines(8, 2)).toBe(4);
  });
});

describe("widthForOneBand", () => {
  it("names the width at which a count stops wrapping", () => {
    // What the readout tells the user so a maximise cannot turn the preview
    // into a broken promise: eight panes need 8 × 380 px plus the grid padding.
    expect(widthForOneBand(8)).toBe(
      8 * MIN_PANE_WIDTH_PX + GRID_HORIZONTAL_PADDING_PX,
    );
    // And it agrees with the helper the wizard measures through, rather than
    // being a second opinion about the same threshold.
    expect(workspaceBandCapacityFor(widthForOneBand(8) as number)).toBe(8);
  });

  it("has nothing to offer when there is no wrap to undo", () => {
    expect(widthForOneBand(1)).toBeNull();
  });

  it("has nothing to offer past the readable cap, where no width is enough", () => {
    expect(widthForOneBand(MAX_PANES_PER_BAND + 1)).toBeNull();
  });
});

describe("paneColumns", () => {
  it("keeps a small workspace on one line", () => {
    // What the grid actually does today for every one of these: one band,
    // columns side by side. The wizard preview has to say the same thing.
    for (const n of [1, 2, 3, 4, 6, 8, 10]) {
      expect(paneColumns(n)).toBe(n);
    }
  });

  it("wraps beyond the readable width instead of shrinking panes further", () => {
    // 12 columns on one line leaves each of them too narrow to read, so the
    // overflow starts a second band: 10 above, 2 below.
    expect(paneColumns(11)).toBe(10);
    expect(paneColumns(12)).toBe(10);
  });

  it("fills the first band up rather than re-balancing the workspace", () => {
    // Balanced bands (21 → 7 + 7 + 7) looked tidier in a still picture, but
    // re-dealt every pane whenever one was added: the user's fourth split
    // moved a terminal they were reading to another row (2026-07-31). Greedy
    // filling keeps every existing column exactly where it is — only the
    // newest can start a band.
    expect(paneColumns(21)).toBe(10);
    expect(paneColumns(20)).toBe(10);
  });

  it("never moves an existing column when one more is opened", () => {
    // The user-facing contract behind greedy filling, pinned directly: for any
    // count, adding a column changes no existing placement.
    for (let count = 1; count < 24; count += 1) {
      const before = paneGrid(wizardPanes(count)).placements;
      const after = paneGrid(wizardPanes(count + 1)).placements;
      expect(after.slice(0, count)).toEqual(before);
    }
  });

  it("has no columns for an empty workspace", () => {
    expect(paneColumns(0)).toBe(0);
  });

  it("exposes the cap it wraps at", () => {
    expect(MAX_PANES_PER_BAND).toBe(10);
  });
});

describe("paneLines", () => {
  it("is one band while the workspace fits", () => {
    expect(paneLines(1)).toBe(1);
    expect(paneLines(10)).toBe(1);
  });

  it("grows with the wrap, so a wrapped workspace gets the height for it", () => {
    expect(paneLines(11)).toBe(2);
    expect(paneLines(12)).toBe(2);
    expect(paneLines(21)).toBe(3);
  });

  it("is nothing for an empty workspace", () => {
    expect(paneLines(0)).toBe(0);
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

  it("wraps a crowded workspace into a second band", () => {
    const panes = Array.from({ length: 12 }, (_, i) => pane(i, 0));
    const grid = paneGrid(panes);
    expect(grid.columns).toBe(10);
    expect(grid.rows).toBe(2);
    // The eleventh column starts the second band, back at the left edge.
    expect(grid.placements[10]).toEqual({ column: 1, row: 2, rowSpan: 1 });
    expect(grid.placements[11]).toEqual({ column: 2, row: 2, rowSpan: 1 });
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
