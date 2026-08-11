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
  /**
   * Panes as SPLITTING RIGHT leaves them — one column each.
   *
   * Written out here rather than taken from `wizardPanes`, which is no longer
   * the same shape: a wizard workspace opens as columns of two now (see the
   * suite below). The distinction is the whole point of these two suites. A
   * count the user chose in advance, and watched the preview draw, may open in
   * any shape it says it will. A pane added to a workspace already on screen
   * may not move the panes already being read — which is what the reports below
   * were about, and what this suite still pins.
   */
  const splits = (count: number) =>
    Array.from({ length: count }, (_, index) => ({ column: index, slot: 0 }));

  it("keeps splits side by side however many there are", () => {
    // Reported 2026-07-31 as a re-wrap at the fourth split, and again
    // 2026-08-03 at the sixth. Both had one cause: the layout decided how many
    // panes may share a line. It does not any more — the split buttons ARE the
    // user's arrangement, and the window is simply divided between them.
    for (const count of [4, 6, 7, 12, 30]) {
      const grid = paneGrid(splits(count));
      expect(grid.columns).toBe(count);
      expect(grid.placements.every((p) => p.row === 1)).toBe(true);
    }
  });

  it("puts the seventh terminal beside the sixth on a five-column window", () => {
    // The exact case reported on 2026-08-03, with the maintainer's screenshot:
    // five panes across, and the sixth landing on a row of its own below.
    const grid = paneGrid(splits(7));
    expect(grid.placements[5]).toEqual({ column: 6, row: 1, rowSpan: 1 });
    expect(grid.placements[6]).toEqual({ column: 7, row: 1, rowSpan: 1 });
  });
});

/**
 * A workspace OPENS two panes deep — the answer to the 2026-08-11 report.
 *
 * Six terminals in a single row left each pane about 410 px on the maintainer's
 * display. A pane narrower than the 60-column grid its agent draws in is clipped
 * at the tile edge, so all six showed roughly two thirds of themselves and read
 * as terminals shoved behind one another.
 *
 * The count itself is untouched: nothing here refuses a number or reshapes one
 * after the fact. Thirty terminals still open as thirty — as fifteen columns of
 * two, which is simply twice the width of thirty in a row.
 */
describe("a wizard workspace opens two panes deep", () => {
  it("halves the columns the same count is spread over", () => {
    for (const count of [4, 6, 12, 30]) {
      const grid = paneGrid(wizardPanes(count));
      expect(grid.columns).toBe(count / 2);
    }
  });

  it("doubles the width every pane gets, which is what was being clipped", () => {
    // The maintainer's own case: six terminals, and the width each one has to
    // draw its agent's interface in.
    const window = 2560;
    const inARow = paneWidthAt(6, window);
    const twoDeep = paneWidthAt(paneGrid(wizardPanes(6)).columns, window);
    expect(twoDeep).toBeCloseTo(inARow * 2, 6);
  });

  it("carries the same shape into the comfort advice", () => {
    // The readout quotes the COLUMN count, so the warning follows the panes'
    // real width rather than the terminal count. Six on a laptop were cramped
    // in a row and are not in three columns of two.
    const laptop = 1440;
    expect(panesAreComfortable(6, laptop)).toBe(false);
    expect(panesAreComfortable(paneGrid(wizardPanes(6)).columns, laptop)).toBe(
      true,
    );
  });
});
