/**
 * What a drop MEANS — the half of pane dragging that can be wrong silently.
 *
 * Every failure here looks like the grid ignoring the user, or worse, doing
 * something they did not ask for: dragging is how a person says "put this pane
 * over there", and the one answer that must never come back unbidden is a swap,
 * which sends the target the other way and moves TWO panes (BUG-111).
 */
import { describe, expect, it } from "vitest";

import { pickTarget, zoneFor, type PaneRect } from "./paneArrange";

/** A 200×100 pane at the origin — round numbers so the arithmetic is readable. */
const RECT: PaneRect = { left: 0, top: 0, width: 200, height: 100 };

/**
 * One column of a five-pane workspace, at the shape those actually have: tall
 * and narrow. This is the geometry the regressions below are about.
 */
const COLUMN: PaneRect = { left: 500, top: 40, width: 360, height: 900 };

describe("zoneFor", () => {
  it("reads the halves of a pane as the side to land on", () => {
    expect(zoneFor(RECT, 40, 50)).toBe("left");
    expect(zoneFor(RECT, 160, 50)).toBe("right");
  });

  it("never answers a plain drag with a swap (BUG-111)", () => {
    // Dragging MOVES one pane. Answering "put it over there" with an exchange
    // sends the target back the other way, so the user asked for one pane to
    // move and two of them did.
    for (const x of [10, 60, 100, 140, 190]) {
      for (const y of [50, 60, 40]) {
        expect(zoneFor(RECT, x, y)).not.toBe("swap");
      }
    }
  });

  it("gives every point on a TALL pane's FLANKS a side, at any height", () => {
    // Nobody aims vertically while dragging sideways, so the part of a grid
    // column a sideways drag crosses has to be a landing place at every height
    // — otherwise carrying a pane past this one stacks it by accident.
    for (const y of [50, 200, 400, 490, 600, 800, 930]) {
      expect(zoneFor(COLUMN, 560, y)).toBe("left");
      expect(zoneFor(COLUMN, 800, y)).toBe("right");
    }
  });

  it("reads the middle of a pane as joining the target's column", () => {
    expect(zoneFor(RECT, 100, 3)).toBe("above");
    expect(zoneFor(RECT, 100, 97)).toBe("below");
    expect(zoneFor(COLUMN, 680, 45)).toBe("above");
    expect(zoneFor(COLUMN, 680, 935)).toBe("below");
  });

  it("stacks anywhere down a tall pane's middle, not just at its edges", () => {
    // The bug this replaced (reported 2026-08-03): the vertical zones were two
    // 88 px bands, which in a 900 px column is under a tenth of the pane at
    // each end, so "put this terminal underneath that one" was a shot nobody
    // could land. Pointing at the middle of the lower half is the obvious way
    // to ask for it, and it is now the answer at ANY depth.
    for (const y of [500, 600, 700, 800, 900]) {
      expect(zoneFor(COLUMN, 680, y)).toBe("below");
    }
    for (const y of [50, 150, 300, 400, 480]) {
      expect(zoneFor(COLUMN, 680, y)).toBe("above");
    }
  });

  it("keeps the stack-it stripe the same size however tall the pane grows", () => {
    // A pane's width barely moves as the grid fills — its height is what
    // changes — so the target the user aims at is measured across the axis that
    // holds still, and a taller pane is not a harder one to stack into.
    const shortPane: PaneRect = { left: 0, top: 0, width: 360, height: 400 };
    expect(zoneFor(COLUMN, 680, COLUMN.top + 300)).toBe("above");
    expect(zoneFor(shortPane, 180, 120)).toBe("above");
    // Out on the flanks it is a sideways landing again — in BOTH panes, at the
    // same fraction of the width, which is the property the bands could not
    // give at two different heights.
    expect(zoneFor(COLUMN, 560, COLUMN.top + 300)).toBe("left");
    expect(zoneFor(shortPane, 60, 120)).toBe("left");
  });

  it("swaps only when the modifier asks for it", () => {
    expect(zoneFor(RECT, 40, 50, { swap: true })).toBe("swap");
    // Even on an edge: the modifier is the user saying what they want, and it
    // outranks where they happen to be pointing.
    expect(zoneFor(RECT, 100, 3, { swap: true })).toBe("swap");
  });

  it("respects a pane that is not at the origin", () => {
    const offset: PaneRect = { left: 500, top: 300, width: 200, height: 100 };
    expect(zoneFor(offset, 505, 350)).toBe("left");
    expect(zoneFor(offset, 695, 350)).toBe("right");
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
    expect(pickTarget(targets, "Mika", 380, 50)).toEqual({
      target: "Nova",
      zone: "right",
    });
  });

  it("carries the swap modifier through to the answer", () => {
    expect(pickTarget(targets, "Mika", 380, 50, { swap: true })).toEqual({
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
