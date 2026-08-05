import { describe, expect, it } from "vitest";
import {
  COMFORTABLE_PANE_WIDTH_PX,
  GRID_HORIZONTAL_PADDING_PX,
  paneGrid,
  paneWidthAt,
  panesAreComfortable,
  wizardPanes,
} from "./layout";

/**
 * The workspace is ALWAYS one screenful — the rule, and the two failures it
 * replaced.
 *
 * Reported 2026-07-25: eight terminals sharing one window left each pane about
 * 18 characters wide, and Claude Code truncated every line.
 *
 * Reported 2026-08-03: the first answer to that — wrapping onto a second line —
 * was worse. It paid for the new pane with the HEIGHT of every existing one, so
 * a sixth terminal silently halved the five the user was reading.
 *
 * Reported 2026-08-04: the second answer — growing the canvas past the window
 * and scrolling to the rest — was worse again. The seventh terminal was opened
 * somewhere off to the right, and watching eight agents at once became a matter
 * of scrolling between them. A wall of terminals you scroll is not a wall of
 * terminals.
 *
 * So the panes shrink, and the readable width survives only as advice the
 * wizard gives BEFORE the user commits to a count.
 */
describe("the workspace never grows past its window", () => {
  it("gives every pane a share of the window, at every size and count", () => {
    for (const width of [800, 1280, 1440, 1920, 2560, 3840]) {
      for (const columns of [1, 4, 8, 16, 60]) {
        const content = width - GRID_HORIZONTAL_PADDING_PX;
        // Every pane on screen, and the panes together are exactly the window:
        // never wider (a scrollbar), never narrower (wasted room).
        expect(paneWidthAt(columns, width) * columns).toBeCloseTo(content, 6);
      }
    }
  });

  it("makes the seventh terminal narrow the other six rather than land off screen", () => {
    // The 2026-08-04 report in its exact shape: six panes, one more opened, and
    // the maintainer having to scroll sideways to see it.
    const window = 6 * COMFORTABLE_PANE_WIDTH_PX + GRID_HORIZONTAL_PADDING_PX;
    const before = paneWidthAt(6, window);
    const after = paneWidthAt(7, window);
    expect(after).toBeLessThan(before);
    expect(after * 7).toBeCloseTo(before * 6, 6);
  });

  it("still says out loud when that leaves the panes cramped", () => {
    // Shrinking without saying so would be the 2026-07-25 report again, just
    // quieter. The wizard warns; nothing about the layout changes.
    const laptop = 1440;
    expect(panesAreComfortable(3, laptop)).toBe(true);
    expect(panesAreComfortable(8, laptop)).toBe(false);
  });
});

describe("splitting never re-deals the workspace", () => {
  it("keeps splits side by side however many there are", () => {
    // Reported 2026-07-31 as a re-wrap at the fourth split, and again
    // 2026-08-03 at the sixth. Both had one cause: the layout decided how many
    // panes may share a line. It does not any more — the split buttons ARE the
    // user's arrangement, and the window is simply divided between them.
    for (const count of [4, 6, 7, 12, 30]) {
      const grid = paneGrid(wizardPanes(count));
      expect(grid.columns).toBe(count);
      expect(grid.placements.every((p) => p.row === 1)).toBe(true);
    }
  });

  it("puts the seventh terminal beside the sixth on a five-column window", () => {
    // The exact case reported on 2026-08-03, with the maintainer's screenshot:
    // five panes across, and the sixth landing on a row of its own below.
    const grid = paneGrid(wizardPanes(7));
    expect(grid.placements[5]).toEqual({ column: 6, row: 1, rowSpan: 1 });
    expect(grid.placements[6]).toEqual({ column: 7, row: 1, rowSpan: 1 });
  });
});
