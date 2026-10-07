import { describe, expect, it } from "vitest";
import { COMPANION_COLORS } from "@/components/society/companion/appearance";
import { DARK_INK, LIGHT_INK, bubbleTint, contrast } from "./bubbleTint";

describe("bubbleTint", () => {
  it("keeps every companion colour readable", () => {
    for (const color of COMPANION_COLORS) {
      const tint = bubbleTint(color)!;
      expect(contrast(tint.background, tint.color), color).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("meets AA on any colour a person can pick", () => {
    for (let r = 0; r < 256; r += 17) {
      for (let g = 0; g < 256; g += 17) {
        for (let b = 0; b < 256; b += 17) {
          const hex = `#${[r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("")}`;
          const tint = bubbleTint(hex)!;
          expect(contrast(tint.background, tint.color), hex).toBeGreaterThanOrEqual(4.5);
        }
      }
    }
  });

  it("puts dark ink on a light colour and keeps the colour", () => {
    expect(bubbleTint("#c5dfd4")).toEqual({ background: "#c5dfd4", color: DARK_INK });
  });

  it("puts white ink on a dark colour and keeps the colour", () => {
    expect(bubbleTint("#1d4ed8")).toEqual({ background: "#1d4ed8", color: LIGHT_INK });
  });

  it("darkens a mid-tone just enough for white ink, keeping its hue", () => {
    const tint = bubbleTint("#8b5cf6")!;
    expect(tint.color).toBe(LIGHT_INK);
    expect(tint.background).not.toBe("#8b5cf6");
    expect(contrast(tint.background, LIGHT_INK)).toBeGreaterThanOrEqual(4.5);
  });

  it("returns null for anything that is not a colour", () => {
    expect(bubbleTint("")).toBeNull();
    expect(bubbleTint(undefined)).toBeNull();
    expect(bubbleTint("blue")).toBeNull();
  });
});
