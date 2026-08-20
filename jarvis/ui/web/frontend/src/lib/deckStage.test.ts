import { describe, expect, test } from "vitest";
import { orbSizeFor, stageVignette, stageWashSize } from "@/lib/deckStage";

describe("orbSizeFor", () => {
  test("is the maximum until the stage is measured", () => {
    expect(orbSizeFor(0, 0)).toBe(320);
  });

  test("fits the room under the headline and stays inside the bounds", () => {
    expect(orbSizeFor(600, 300)).toBe(228);
    expect(orbSizeFor(220, 900)).toBe(204);
    expect(orbSizeFor(120, 120)).toBe(200);
    expect(orbSizeFor(2000, 2000)).toBe(320);
  });
});

describe("stageVignette", () => {
  test("uses the theme ground colour and scales with the reticle", () => {
    const css = stageVignette(300);
    expect(css).toContain("hsl(var(--background)");
    expect(css).toContain("165px");
    expect(css).toContain("315px");
  });

  test("is centred on its own element, not offset for a column", () => {
    // It rode a wide, short column at "50% 46%" and got clipped top and
    // bottom while still opaque — a bright slab in light mode.
    expect(stageVignette(300)).toContain("circle at 50% 50%");
  });

  test("the element is wide enough for the circle to reach zero inside it", () => {
    // Anything smaller and an edge cuts the wash — the defect this pair fixes.
    for (const size of [200, 240, 300, 320]) {
      const outer = Number(/ (\d+)px\)$/.exec(stageVignette(size))?.[1]);
      expect(stageWashSize(size)).toBeGreaterThanOrEqual(outer * 2);
    }
  });
});
