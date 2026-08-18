import { describe, expect, test } from "vitest";
import { orbSizeFor, stageVignette } from "@/lib/deckStage";

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
});
