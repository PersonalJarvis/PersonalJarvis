import { describe, expect, it } from "vitest";
import { countTicks } from "./WorkspaceShape";

/*
 * The legend under the terminal-count track.
 *
 * These assertions exist because the bug they cover was invisible in code
 * review and obvious on screen: the legend was a hard-coded list, the track's
 * maximum came from the backend, and the two silently disagreed the moment
 * MAX_TERMINALS moved from 12 to 100. What is pinned here is therefore not the
 * exact numbers so much as the two properties that make a legend a legend —
 * it starts where the track starts and ends where the track ends.
 */
describe("countTicks", () => {
  it("labels the workspace maximum, whatever it is", () => {
    for (const max of [2, 5, 8, 12, 16, 24, 50, 64, 100, 256]) {
      const ticks = countTicks(max);
      expect(ticks[0]).toBe(1);
      expect(ticks[ticks.length - 1]).toBe(max);
    }
  });

  it("rises, never repeats, and stays inside the track", () => {
    for (const max of [2, 5, 8, 12, 16, 24, 50, 64, 100, 256]) {
      const ticks = countTicks(max);
      expect(new Set(ticks).size).toBe(ticks.length);
      expect([...ticks].sort((a, b) => a - b)).toEqual(ticks);
      expect(ticks.every((n) => n >= 1 && n <= max)).toBe(true);
      // More than a handful and the labels touch on a narrow window.
      expect(ticks.length).toBeLessThanOrEqual(7);
    }
  });

  it("reads as round numbers at the maximum the backend ships", () => {
    // MAX_TERMINALS is 100 (jarvis/agentic_ide/session.py).
    expect(countTicks(100)).toEqual([1, 25, 50, 75, 100]);
  });

  it("keeps the old every-other-pane rhythm on a small track", () => {
    expect(countTicks(12)).toEqual([1, 2, 4, 6, 8, 10, 12]);
  });

  it("drops a step tick that would sit on top of the end label", () => {
    // 15 and 16 would be a pixel apart; the end of the track wins.
    expect(countTicks(16)).toEqual([1, 5, 10, 16]);
  });

  it("has nothing to divide for a one-terminal or unmeasured maximum", () => {
    expect(countTicks(1)).toEqual([1]);
    expect(countTicks(0)).toEqual([1]);
    expect(countTicks(Number.NaN)).toEqual([1]);
  });
});
