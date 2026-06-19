// Tests for the pure size-change guard used by the Wiki Memory-Map.
//
// The graph canvas is sized from a ResizeObserver measurement. To stop tiny
// sub-pixel fluctuations (scrollbar flicker, DPI rounding) from churning React
// state — and, before the remount was removed, from restarting the whole
// simulation — we only accept a measurement that moved by a real margin. The
// math is pulled out here so it is unit-testable without a renderer.
import { describe, expect, it } from "vitest";

import { clampCenterToView, sizeChanged } from "@/lib/wikiGraph";

// How many graph-units of the bbox stay inside the viewport on a given axis,
// for a camera centred at `c` with zoom `k`. Mirrors the maths inside
// clampCenterToView so the tests assert the *guarantee*, not the formula.
function visibleOverlapX(
  c: { x: number; y: number },
  k: number,
  bbox: { x: [number, number]; y: [number, number] },
  view: { w: number; h: number },
): number {
  const half = view.w / (2 * k);
  return Math.min(c.x + half, bbox.x[1]) - Math.max(c.x - half, bbox.x[0]);
}
function visibleOverlapY(
  c: { x: number; y: number },
  k: number,
  bbox: { x: [number, number]; y: [number, number] },
  view: { w: number; h: number },
): number {
  const half = view.h / (2 * k);
  return Math.min(c.y + half, bbox.y[1]) - Math.max(c.y - half, bbox.y[0]);
}

describe("sizeChanged", () => {
  it("returns false for an identical size", () => {
    expect(sizeChanged({ w: 800, h: 600 }, { w: 800, h: 600 })).toBe(false);
  });

  it("ignores a sub-threshold width jitter (1px) with default threshold", () => {
    expect(sizeChanged({ w: 800, h: 600 }, { w: 801, h: 600 })).toBe(false);
  });

  it("accepts a real width change", () => {
    expect(sizeChanged({ w: 800, h: 600 }, { w: 820, h: 600 })).toBe(true);
  });

  it("accepts a real height change", () => {
    expect(sizeChanged({ w: 800, h: 600 }, { w: 800, h: 612 })).toBe(true);
  });

  it("treats a shrink the same as a grow", () => {
    expect(sizeChanged({ w: 800, h: 600 }, { w: 760, h: 600 })).toBe(true);
  });

  it("honours an explicit threshold of 0 (any change counts)", () => {
    expect(sizeChanged({ w: 800, h: 600 }, { w: 801, h: 600 }, 0)).toBe(true);
    expect(sizeChanged({ w: 800, h: 600 }, { w: 800, h: 600 }, 0)).toBe(false);
  });
});

describe("clampCenterToView", () => {
  // A graph wider than the viewport, so the clamp keeps a *fraction* visible
  // rather than the whole thing. keepX = min(w*frac/k, bboxWidth).
  const wideBbox = { x: [-1000, 1000] as [number, number], y: [-1000, 1000] as [number, number] };
  const view = { w: 1000, h: 800 };

  it("leaves the centre untouched when the graph is already in view", () => {
    const c = { x: 0, y: 0 };
    expect(clampCenterToView(c, 1, wideBbox, view)).toEqual({ x: 0, y: 0 });
  });

  it("clamps an over-pan to the right so the graph cannot slide off the viewport", () => {
    // Camera centred far to the left in graph-space pushes the graph fully off
    // the right edge — exactly the live-reproduced bug (centre x = -280).
    const out = clampCenterToView({ x: 5000, y: 0 }, 1, wideBbox, view);
    // keepX = min(1000*0.25/1, 2000) = 250 → maxCx = bx1 + halfW - keepX = 1000 + 500 - 250
    expect(out.x).toBeCloseTo(1250, 5);
    expect(visibleOverlapX(out, 1, wideBbox, view)).toBeCloseTo(250, 5);
  });

  it("clamps an over-pan to the left symmetrically", () => {
    const out = clampCenterToView({ x: -5000, y: 0 }, 1, wideBbox, view);
    expect(out.x).toBeCloseTo(-1250, 5);
    expect(visibleOverlapX(out, 1, wideBbox, view)).toBeCloseTo(250, 5);
  });

  it("clamps a vertical over-pan independently of the horizontal axis", () => {
    const out = clampCenterToView({ x: 0, y: 9000 }, 1, wideBbox, view);
    expect(out.x).toBe(0); // horizontal already in view
    // keepY = min(800*0.25/1, 2000) = 200 → maxCy = 1000 + 400 - 200 = 1200
    expect(out.y).toBeCloseTo(1200, 5);
    expect(visibleOverlapY(out, 1, wideBbox, view)).toBeCloseTo(200, 5);
  });

  it("keeps a small graph FULLY visible (never demands more overlap than the graph spans)", () => {
    // A compact 19-node graph is smaller than the viewport. The clamp must keep
    // the whole bbox reachable, not strand it the moment its centre leaves.
    const smallBbox = { x: [-100, 100] as [number, number], y: [-100, 100] as [number, number] };
    const out = clampCenterToView({ x: 9999, y: 0 }, 1, smallBbox, view);
    // keepX = min(250, 200) = 200 (full width) → maxCx = 100 + 500 - 200 = 400
    expect(out.x).toBeCloseTo(400, 5);
    expect(visibleOverlapX(out, 1, smallBbox, view)).toBeCloseTo(200, 5); // entire bbox visible
  });

  it("honours the zoom level — overlap is measured in screen pixels, not graph units", () => {
    // At higher zoom the same screen fraction is fewer graph-units, so the graph
    // may be panned further in graph-space before the clamp bites.
    const out = clampCenterToView({ x: 5000, y: 0 }, 5, wideBbox, view);
    // halfW = 1000/(2*5) = 100; keepX = min(1000*0.25/5, 2000) = 50
    // maxCx = 1000 + 100 - 50 = 1050
    expect(out.x).toBeCloseTo(1050, 5);
  });

  it("is a no-op for a non-positive zoom (guards divide-by-zero)", () => {
    const c = { x: 5000, y: 5000 };
    expect(clampCenterToView(c, 0, wideBbox, view)).toEqual(c);
    expect(clampCenterToView(c, -1, wideBbox, view)).toEqual(c);
  });

  it("respects an explicit minVisibleFraction", () => {
    // A larger fraction means the clamp bites sooner (more must stay visible).
    const out = clampCenterToView({ x: 5000, y: 0 }, 1, wideBbox, view, 0.5);
    // keepX = min(1000*0.5/1, 2000) = 500 → maxCx = 1000 + 500 - 500 = 1000
    expect(out.x).toBeCloseTo(1000, 5);
    expect(visibleOverlapX(out, 1, wideBbox, view)).toBeCloseTo(500, 5);
  });
});
