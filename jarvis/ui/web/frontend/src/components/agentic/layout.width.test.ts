import { describe, expect, it } from "vitest";
import {
  MIN_PANE_WIDTH_PX,
  columnsWithoutScrolling,
  paneGrid,
  workspaceWidthFor,
} from "./layout";

/**
 * Readability is a floor on WIDTH, and the two reports that shaped it pull in
 * opposite directions.
 *
 * Reported 2026-07-25: eight terminals sharing one window left each pane about
 * 18 characters wide. Claude Code then truncates every line and breaks single
 * words across rows ("Clau/de/Max") — measured directly against a real agent.
 *
 * Reported 2026-08-03: the answer to that, a wrap onto a second line, was
 * worse. It paid for the new pane with the HEIGHT of every existing one, so a
 * sixth terminal silently halved the five the user was reading and changed the
 * shape of a workspace they had arranged themselves.
 *
 * So the floor stayed and the wrap went: past it the workspace is drawn wider
 * than the window and scrolls, exactly as it already grows taller and scrolls
 * down when the stacks get short.
 */
describe("the readable floor under every column", () => {
  it("holds at every window size and every count", () => {
    for (const width of [800, 1280, 1440, 1920, 2560, 3840]) {
      for (const columns of [1, 4, 8, 16, 60]) {
        const canvas = workspaceWidthFor(columns, width);
        expect(canvas / columns).toBeGreaterThanOrEqual(MIN_PANE_WIDTH_PX);
      }
    }
  });

  it("would have prevented the 2026-07-25 case, by scrolling rather than squeezing", () => {
    // The workspace in the report was ~2400 px wide with eight panes across.
    const canvas = workspaceWidthFor(8, 2400);
    expect(canvas).toBeGreaterThan(2400);
    expect(canvas / 8).toBeGreaterThanOrEqual(MIN_PANE_WIDTH_PX);
  });

  it("costs nothing while the workspace fits", () => {
    // Four panes on a 2K desktop: the canvas is the window, and no scrollbar
    // appears for a workspace that was never too big.
    expect(workspaceWidthFor(4, 2048)).toBe(2048);
  });
});

describe("splitting never re-deals the workspace", () => {
  it("keeps splits side by side however many there are", () => {
    // Reported 2026-07-31 as a re-wrap at the fourth split, and again
    // 2026-08-03 at the sixth. Both had one cause: the layout decided how many
    // panes may share a line. It does not any more — the split buttons ARE the
    // user's arrangement, and the window only decides how much of it is on
    // screen.
    for (const count of [4, 6, 7, 12, 30]) {
      const panes = Array.from({ length: count }, (_, i) => ({
        column: i,
        slot: 0,
      }));
      const grid = paneGrid(panes);
      expect(grid.columns).toBe(count);
      expect(grid.placements.every((p) => p.row === 1)).toBe(true);
    }
  });

  it("puts the seventh terminal beside the sixth on a five-column window", () => {
    // The exact case reported on 2026-08-03, with the maintainer's screenshot:
    // five panes across, and the sixth landing on a row of its own below.
    const window = 5 * MIN_PANE_WIDTH_PX;
    expect(columnsWithoutScrolling(window)).toBeLessThan(7);
    const panes = Array.from({ length: 7 }, (_, i) => ({ column: i, slot: 0 }));
    const grid = paneGrid(panes);
    expect(grid.placements[5]).toEqual({ column: 6, row: 1, rowSpan: 1 });
    expect(grid.placements[6]).toEqual({ column: 7, row: 1, rowSpan: 1 });
  });
});
