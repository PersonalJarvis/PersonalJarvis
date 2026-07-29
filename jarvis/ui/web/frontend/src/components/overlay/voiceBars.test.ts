import { describe, expect, it } from "vitest";

import {
  BAR_MAX_H,
  BAR_MIN_H,
  PILL_W,
  PILL_X,
  VIEW_W,
  barHeight,
  barWidth,
  clamp01,
  evenlySpaced,
  ringValue,
  smoothLevel,
  sweepGain,
} from "./voiceBars";

describe("evenlySpaced", () => {
  it("centres the row on the pill instead of drifting off to one side", () => {
    const xs = evenlySpaced(VIEW_W / 2, 62, 7);
    expect((xs[0] + xs[xs.length - 1]) / 2).toBeCloseTo(VIEW_W / 2, 6);
  });

  it("keeps every stroke inside the pill", () => {
    const count = 18;
    const half = barWidth(count) / 2;
    for (const x of evenlySpaced(VIEW_W / 2, 62, count)) {
      expect(x - half).toBeGreaterThan(PILL_X);
      expect(x + half).toBeLessThan(PILL_X + PILL_W);
    }
  });

  it("puts a single item exactly on the centre", () => {
    expect(evenlySpaced(50, 62, 1)).toEqual([50]);
  });
});

describe("barHeight", () => {
  it("stays within the pill for any input, including nonsense", () => {
    for (const v of [-5, 0, 0.5, 1, 12, NaN]) {
      const h = barHeight(v);
      expect(h).toBeGreaterThanOrEqual(BAR_MIN_H);
      expect(h).toBeLessThanOrEqual(BAR_MAX_H);
    }
  });
});

describe("smoothLevel", () => {
  it("rises faster than it falls", () => {
    const up = smoothLevel(0, 1, 0.016);
    const down = 1 - smoothLevel(1, 0, 0.016);
    expect(up).toBeGreaterThan(down);
  });

  it("reaches the same place in the same TIME regardless of frame rate", () => {
    // The whole point of the dt-based easing: a 144 Hz monitor must not make
    // the visualizer twitchier than a 60 Hz one.
    let slow = 0;
    for (let i = 0; i < 6; i += 1) slow = smoothLevel(slow, 1, 1 / 60);
    let fast = 0;
    for (let i = 0; i < 15; i += 1) fast = smoothLevel(fast, 1, 1 / 150);
    expect(fast).toBeCloseTo(slow, 3);
  });

  it("snaps to dead zero so the row cannot wiggle in silence", () => {
    let level = 1;
    for (let i = 0; i < 200; i += 1) level = smoothLevel(level, 0, 0.016);
    expect(level).toBe(0);
  });

  it("survives a zero or negative frame delta without moving", () => {
    expect(smoothLevel(0.4, 1, 0)).toBe(0.4);
    expect(smoothLevel(0.4, 1, -1)).toBe(0.4);
  });

  it("never leaves 0..1, whatever the meter reports", () => {
    expect(smoothLevel(0, 5, 1)).toBeLessThanOrEqual(1);
    expect(smoothLevel(0.5, -3, 1)).toBeGreaterThanOrEqual(0);
  });
});

describe("sweepGain", () => {
  it("peaks under the highlight and fades away from it", () => {
    expect(sweepGain(0, 10, 0)).toBeCloseTo(1, 6);
    expect(sweepGain(5, 10, 0)).toBeLessThan(0.1);
  });

  it("wraps around, so the highlight re-enters without a jump", () => {
    // Just past the right edge the leftmost bar must already be lighting up.
    expect(sweepGain(0, 10, 1.02)).toBeGreaterThan(sweepGain(5, 10, 1.02));
    expect(sweepGain(0, 10, 0)).toBeCloseTo(sweepGain(0, 10, 1), 6);
  });
});

describe("ringValue", () => {
  it("reads oldest-first so the newest sample sits at the right edge", () => {
    const history = [10, 20, 30];
    // head points at the slot to be overwritten next — the oldest one.
    expect([0, 1, 2].map((i) => ringValue(history, 1, i))).toEqual([20, 30, 10]);
  });

  it("is safe on an empty history and on out-of-range indices", () => {
    expect(ringValue([], 0, 0)).toBe(0);
    expect(ringValue([1, 2], -3, 0)).toBe(2);
  });
});

describe("clamp01", () => {
  it("treats a broken sample as silence rather than full scale", () => {
    expect(clamp01(NaN)).toBe(0);
    expect(clamp01(Infinity)).toBe(0);
  });
});
