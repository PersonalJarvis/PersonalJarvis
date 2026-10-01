import { describe, expect, it } from "vitest";
import { blendingCanvasZ } from "./blendingLayer";

describe("blendingCanvasZ", () => {
  it("matches the canvas z-index drei gives a blending <Html>", () => {
    expect(blendingCanvasZ([10, 0])).toBe("5");
    expect(blendingCanvasZ([7, 0])).toBe("3");
  });
});
