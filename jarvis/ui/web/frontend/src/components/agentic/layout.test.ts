import { describe, expect, it } from "vitest";
import { MAX_PANES_PER_BAND, paneColumns, paneGrid, paneLines } from "./layout";

describe("paneColumns", () => {
  it("keeps a small workspace on one line", () => {
    // What the grid actually does today for every one of these: one band,
    // columns side by side. The wizard preview has to say the same thing.
    for (const n of [1, 2, 3, 4, 6, 8, 10]) {
      expect(paneColumns(n)).toBe(n);
    }
  });

  it("wraps beyond the readable width instead of shrinking panes further", () => {
    // 12 columns on one line leaves each of them too narrow to read, so they
    // break into two even bands: 6 above, 6 below.
    expect(paneColumns(11)).toBe(6);
    expect(paneColumns(12)).toBe(6);
  });

  it("keeps the bands even rather than filling the first one up", () => {
    // 21 columns could be 10 + 10 + 1; an almost empty last line next to two
    // full ones looks broken, so every band gets the same width.
    expect(paneColumns(21)).toBe(7);
    expect(paneColumns(20)).toBe(10);
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
    expect(grid.columns).toBe(6);
    expect(grid.rows).toBe(2);
    // The seventh column starts the second band, back at the left edge.
    expect(grid.placements[6]).toEqual({ column: 1, row: 2, rowSpan: 1 });
    expect(grid.placements[11]).toEqual({ column: 6, row: 2, rowSpan: 1 });
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
