import { describe, expect, it } from "vitest";

import { clampRect, rectFrom, strokeWidths, textSize, viewport, type Op } from "@/lib/appshotEditorModel";

describe("appshot editor model", () => {
  it("builds the same rectangle for a drag in any direction", () => {
    expect(rectFrom({ x: 50, y: 40 }, { x: 10, y: 10 })).toEqual({ x: 10, y: 10, w: 40, h: 30 });
  });

  it("clamps a rectangle to the picture and drops one that leaves nothing", () => {
    expect(clampRect({ x: -10, y: 5, w: 50, h: 500 }, 100, 80)).toEqual({ x: 0, y: 5, w: 40, h: 75 });
    expect(clampRect({ x: 120, y: 0, w: 20, h: 20 }, 100, 80)).toBeNull();
  });

  it("shows the last crop, and the whole picture again once it is undone", () => {
    const crop: Op = { kind: "crop", rect: { x: 10, y: 10, w: 50, h: 40 } };
    const pen: Op = { kind: "pen", points: [{ x: 1, y: 1 }], color: "#fff", width: 3 };
    expect(viewport([pen, crop], 200, 100)).toEqual(crop.rect);
    expect(viewport([pen], 200, 100)).toEqual({ x: 0, y: 0, w: 200, h: 100 });
  });

  it("scales stroke and text sizes with the picture", () => {
    const [small, , large] = strokeWidths(1400, 800);
    expect(small).toBe(3);
    expect(large).toBe(11);
    expect(strokeWidths(2800, 1600)[0]).toBe(6);
    expect(textSize(6)).toBe(30);
  });
});
