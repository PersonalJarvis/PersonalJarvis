/**
 * What a drop MEANS — the half of pane dragging that can be wrong silently.
 *
 * Every failure here looks like the grid ignoring the user: a corner that
 * resolves to the wrong edge puts a pane one column off, and a centre zone that
 * is too small turns "swap these two" into "move it to the left of that one",
 * which rearranges the whole row instead of exchanging two panes.
 */
import { describe, expect, it } from "vitest";

import { EDGE_MAX_PX, pickTarget, zoneFor, type PaneRect } from "./paneArrange";

/** A 200×100 pane at the origin — round numbers so the fractions are readable. */
const RECT: PaneRect = { left: 0, top: 0, width: 200, height: 100 };

/**
 * One column of a five-pane workspace, at the shape those actually have: tall
 * and narrow. This is the geometry every regression below is about.
 */
const COLUMN: PaneRect = { left: 500, top: 40, width: 360, height: 900 };

describe("zoneFor", () => {
  it("reads the middle of a pane as a swap", () => {
    expect(zoneFor(RECT, 100, 50)).toBe("swap");
  });

  it("keeps the swap zone big enough to hit without aiming", () => {
    // Anywhere in the middle 40 % × 40 %, which is a large target on purpose:
    // swapping two panes is the move people reach for.
    expect(zoneFor(RECT, 65, 35)).toBe("swap");
    expect(zoneFor(RECT, 135, 65)).toBe("swap");
  });

  it("reads each edge as a placement on that side", () => {
    expect(zoneFor(RECT, 5, 50)).toBe("left");
    expect(zoneFor(RECT, 195, 50)).toBe("right");
    expect(zoneFor(RECT, 100, 3)).toBe("above");
    expect(zoneFor(RECT, 100, 97)).toBe("below");
  });

  it("resolves a corner to the edge it is actually nearer", () => {
    // 4 % across, 20 % down: closer to the left edge than to the top one.
    expect(zoneFor(RECT, 8, 20)).toBe("left");
    // 20 % across, 4 % down: the other way round.
    expect(zoneFor(RECT, 40, 4)).toBe("above");
  });

  it("respects a pane that is not at the origin", () => {
    const offset: PaneRect = { left: 500, top: 300, width: 200, height: 100 };
    expect(zoneFor(offset, 600, 350)).toBe("swap");
    expect(zoneFor(offset, 505, 350)).toBe("left");
  });

  it("reads the middle of a TALL pane as a swap, at any height (BUG-111)", () => {
    // The bug: carrying a pane sideways onto its neighbour and letting go
    // anywhere below the pane's middle produced `below`, which stacked the two
    // in one column instead of exchanging them. Nobody aims vertically while
    // dragging horizontally, so the whole middle band has to be a swap.
    for (const y of [200, 400, 490, 600, 700, 800]) {
      expect(zoneFor(COLUMN, 680, y)).toBe("swap");
    }
  });

  it("keeps an edge band the same size however tall the pane grows", () => {
    // An edge is a place the user aims at; aiming does not get harder because
    // the pane got taller, and a band that grows with it eats the middle.
    const shortPane: PaneRect = { left: 0, top: 0, width: 360, height: 400 };
    const insideBand = EDGE_MAX_PX - 10;
    expect(zoneFor(COLUMN, 680, COLUMN.top + insideBand)).toBe("above");
    expect(zoneFor(shortPane, 180, insideBand)).toBe("above");
    // Just past the ceiling it is the middle again — in BOTH panes, which is
    // the property a share alone could not give.
    const pastBand = EDGE_MAX_PX + 10;
    expect(zoneFor(COLUMN, 680, COLUMN.top + pastBand)).toBe("swap");
    expect(zoneFor(shortPane, 180, pastBand)).toBe("swap");
  });

  it("still reads every edge of a tall pane as a placement", () => {
    expect(zoneFor(COLUMN, 505, 500)).toBe("left");
    expect(zoneFor(COLUMN, 855, 500)).toBe("right");
    expect(zoneFor(COLUMN, 680, 45)).toBe("above");
    expect(zoneFor(COLUMN, 680, 935)).toBe("below");
  });

  it("belongs to an edge only when it is inside THAT edge's band", () => {
    // Deep in the left band but well clear of the top one: the nearest edge in
    // raw distance is the top, yet the point is not in the top's band at all.
    expect(zoneFor(COLUMN, 505, COLUMN.top + 200)).toBe("left");
  });

  it("falls back to swap for a pane with no measurable box", () => {
    // A hidden pane cannot produce a sensible edge, and swap is the one answer
    // that can never render an impossible layout.
    expect(zoneFor({ left: 0, top: 0, width: 0, height: 0 }, 0, 0)).toBe("swap");
  });
});

describe("pickTarget", () => {
  const targets = [
    { name: "Mika", rect: { left: 0, top: 0, width: 200, height: 100 } },
    { name: "Nova", rect: { left: 200, top: 0, width: 200, height: 100 } },
  ];

  it("names the pane under the pointer and what a drop would do", () => {
    expect(pickTarget(targets, "Mika", 300, 50)).toEqual({
      target: "Nova",
      zone: "swap",
    });
  });

  it("never offers the pane that is being carried", () => {
    // Hovering the pane in your hand is not a drop — offering one would put a
    // highlight on the thing being dragged.
    expect(pickTarget(targets, "Mika", 100, 50)).toBeNull();
  });

  it("answers nothing when the pointer is outside every pane", () => {
    expect(pickTarget(targets, "Mika", 900, 900)).toBeNull();
  });

  it("skips panes that are not laid out", () => {
    const hidden = [
      { name: "Nova", rect: { left: 0, top: 0, width: 0, height: 0 } },
    ];
    expect(pickTarget(hidden, "Mika", 0, 0)).toBeNull();
  });
});
