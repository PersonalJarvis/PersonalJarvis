import { describe, expect, it } from "vitest";
import { boardAtRest, type RestInputs } from "@/lib/deckRest";

const QUIET: RestInputs = {
  runningOutputs: 0,
  liveRuns: 0,
  shellRunning: 0,
  termLines: 0,
  idePanes: 0,
  captureShowing: false,
};

describe("boardAtRest", () => {
  it("is at rest on a machine that was simply switched on", () => {
    expect(boardAtRest(QUIET)).toBe(true);
  });

  it("stays at rest with a full history behind it", () => {
    // The screen this was written for: 84 finished runs, a hundred old
    // outputs, nothing running. History is scale, not activity — the strip
    // prints those figures, so collapsing the row hides nothing.
    expect(boardAtRest({ ...QUIET, runningOutputs: 0, liveRuns: 0 })).toBe(true);
  });

  it.each<[keyof RestInputs, RestInputs[keyof RestInputs]]>([
    ["runningOutputs", 1],
    ["liveRuns", 1],
    ["shellRunning", 1],
    ["termLines", 1],
    ["idePanes", 1],
    ["captureShowing", true],
  ])("leaves rest as soon as %s says something is live", (key, value) => {
    expect(boardAtRest({ ...QUIET, [key]: value })).toBe(false);
  });
});
