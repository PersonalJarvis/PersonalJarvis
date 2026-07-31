/**
 * What a drop MEANS — the half of pane dragging that can be wrong silently.
 *
 * Every failure here looks like the grid ignoring the user: a corner that
 * resolves to the wrong edge puts a pane one column off, and a centre zone that
 * is too small turns "swap these two" into "move it to the left of that one",
 * which rearranges the whole row instead of exchanging two panes.
 */
import { describe, expect, it } from "vitest";

import { pickTarget, zoneFor, type PaneRect } from "./paneArrange";

/** A 200×100 pane at the origin — round numbers so the fractions are readable. */
const RECT: PaneRect = { left: 0, top: 0, width: 200, height: 100 };

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
