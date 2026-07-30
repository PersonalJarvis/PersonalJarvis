import { describe, expect, it } from "vitest";
import {
  MAX_PANES_PER_BAND,
  MIN_PANE_WIDTH_PX,
  bandCapacityFor,
  bandCapacityForArea,
  paneColumns,
  paneGrid,
} from "./layout";

/**
 * Reported 2026-07-25: eight terminals in one band left each pane about 18
 * characters wide. Claude Code then truncates every line and breaks single words
 * across rows ("Clau/de/Max"), so the panes were unreadable — measured directly
 * against a real agent at 20 columns.
 *
 * The cause was a FIXED cap of ten panes per band. Ten is fine on a 4K display
 * and ruinous on a laptop, so capacity has to come from the available width.
 */
describe("bandCapacityFor", () => {
  it("keeps every pane at least as wide as the readable floor", () => {
    for (const width of [800, 1280, 1440, 1920, 2560, 3840]) {
      const capacity = bandCapacityFor(width);
      expect(width / capacity).toBeGreaterThanOrEqual(MIN_PANE_WIDTH_PX);
    }
  });

  it("would have prevented the reported case", () => {
    // The workspace in the report was ~2400 px wide with eight panes in one band.
    expect(bandCapacityFor(2400)).toBeLessThan(8);
  });

  it("gives a laptop fewer columns than a large display", () => {
    expect(bandCapacityFor(1440)).toBeLessThan(bandCapacityFor(2560));
  });

  it("never returns zero — one cramped pane still beats none", () => {
    expect(bandCapacityFor(120)).toBe(1);
    expect(bandCapacityFor(1)).toBe(1);
  });

  it("falls back to the static ceiling when the width is unknown", () => {
    // Before the first measurement the grid reports 0; wrapping to a single
    // column then would make the layout jump on mount.
    expect(bandCapacityFor(0)).toBe(MAX_PANES_PER_BAND);
    expect(bandCapacityFor(Number.NaN)).toBe(MAX_PANES_PER_BAND);
  });

  it("never exceeds the static ceiling", () => {
    expect(bandCapacityFor(100_000)).toBe(MAX_PANES_PER_BAND);
  });
});

describe("bandCapacityForArea", () => {
  it("turns four tall terminal columns into a balanced two-by-two workspace", () => {
    // Reported 2026-07-30: at roughly this 2K desktop size, Claude Code kept
    // its status at the bottom of each full-height TUI and its answer at the
    // top. The width-only layout accepted four 500 x 1,100 panes, leaving a
    // conspicuous empty half-screen between them.
    const capacity = bandCapacityForArea(2048, 1100, 4);
    expect(paneColumns(4, capacity)).toBe(2);
  });

  it("keeps three panes side by side in the same window", () => {
    const capacity = bandCapacityForArea(2048, 1100, 3);
    expect(paneColumns(3, capacity)).toBe(3);
  });

  it("still permits four across on a genuinely ultrawide display", () => {
    const capacity = bandCapacityForArea(3840, 1000, 4);
    expect(paneColumns(4, capacity)).toBe(4);
  });

  it("uses the width-only layout until height has been measured", () => {
    expect(bandCapacityForArea(2048, 0, 4)).toBe(bandCapacityFor(2048));
  });

  it("does not collapse to one column before width has been measured", () => {
    expect(bandCapacityForArea(0, 1100, 4)).toBe(MAX_PANES_PER_BAND);
  });
});

describe("wrapping with a measured capacity", () => {
  const panes = Array.from({ length: 8 }, (_, i) => ({ column: i, slot: 0 }));

  it("spreads eight panes over several bands on a normal window", () => {
    const capacity = bandCapacityFor(1920);
    const grid = paneGrid(panes, capacity);
    expect(grid.columns).toBeLessThanOrEqual(capacity);
    const bands = new Set(grid.placements.map((p) => Math.floor((p.row - 1) / p.rowSpan)));
    expect(bands.size).toBeGreaterThan(1);
  });

  it("balances the bands instead of leaving a stub row", () => {
    // 8 panes at capacity 5 → 4 + 4, not 5 + 3.
    expect(paneColumns(8, 5)).toBe(4);
  });

  it("still places every pane exactly once", () => {
    const grid = paneGrid(panes, bandCapacityFor(1440));
    expect(grid.placements).toHaveLength(panes.length);
    expect(grid.placements.every((p) => p.column >= 1 && p.row >= 1)).toBe(true);
  });
});
