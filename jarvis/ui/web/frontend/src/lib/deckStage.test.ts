import { describe, expect, test } from "vitest";
import { orbSizeFor } from "@/lib/deckStage";

describe("orbSizeFor", () => {
  test("is the maximum until the stage is measured", () => {
    expect(orbSizeFor(0, 0)).toBe(320);
  });

  test("fits the room under the headline and stays inside the bounds", () => {
    expect(orbSizeFor(600, 300)).toBe(200);
    expect(orbSizeFor(220, 900)).toBe(204);
    expect(orbSizeFor(120, 120)).toBe(200);
    expect(orbSizeFor(2000, 2000)).toBe(320);
  });

  test("keeps room under the figure for the wave and the readout row", () => {
    // The stage grew a wave and a readout row below the figure on 2026-08-20;
    // a reserve sized for the headline alone let them run off a short stage.
    expect(orbSizeFor(2000, 400)).toBeLessThanOrEqual(400 - 108);
  });
});
