import { describe, expect, it } from "vitest";
import { cutout, fitToView, padded, placeCard } from "./tourSteps";

describe("cutout", () => {
  it("keeps the same number of points for any hole, so it can animate", () => {
    const a = cutout({ x: 0, y: 0, w: 1200, h: 800 });
    const b = cutout({ x: 40, y: 60, w: 200, h: 36 });
    expect(a.split(",").length).toBe(b.split(",").length);
    expect(b).toContain("40px 60px");
    expect(b).toContain("240px 96px");
    expect(b.startsWith("polygon(evenodd,")).toBe(true);
  });
});

describe("placeCard", () => {
  const view = { w: 1280, h: 800 };
  const card = { w: 320, h: 160 };

  it("sits to the right of a sidebar row", () => {
    const pos = placeCard({ x: 8, y: 200, w: 220, h: 36 }, "right", card, view);
    expect(pos.x).toBe(8 + 220 + 14);
    expect(pos.y).toBe(200 + 18 - 80);
  });

  it("stays inside the window near an edge", () => {
    const pos = placeCard({ x: 8, y: 770, w: 220, h: 20 }, "right", card, view);
    expect(pos.y + card.h).toBeLessThanOrEqual(view.h - 12);
  });

  it("flips above when there is no room below", () => {
    const pos = placeCard({ x: 400, y: 700, w: 400, h: 60 }, "below", card, view);
    expect(pos.y).toBe(700 - 14 - 160);
  });

  it("centres the card without an element", () => {
    expect(placeCard(null, "below", card, view)).toEqual({ x: 480, y: 320 });
  });
});

describe("fitToView", () => {
  const view = { w: 1280, h: 800 };

  it("pulls a whole-page ring back inside the window", () => {
    const ring = fitToView(padded({ x: 0, y: 32, w: 1280, h: 768 }), view);
    expect(ring).toEqual({ x: 6, y: 24, w: 1268, h: 770 });
  });

  it("leaves a ring that already fits untouched", () => {
    const ring = padded({ x: 100, y: 100, w: 200, h: 40 });
    expect(fitToView(ring, view)).toEqual(ring);
  });
});
