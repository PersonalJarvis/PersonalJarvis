import { describe, expect, it } from "vitest";
import { RETRO_STEP } from "./retroGame";
import { MAX_STEPS_PER_FRAME, createRetroClock, resetClock, stepsDue } from "./retroLoop";

describe("retro fixed-step clock", () => {
  it("runs one step per 60 Hz frame without drifting", () => {
    const clock = createRetroClock();
    let total = 0;
    for (let i = 0; i < 600; i++) total += stepsDue(clock, RETRO_STEP);
    expect(total).toBe(600);
  });

  it("runs every other frame on a 120 Hz display", () => {
    const clock = createRetroClock();
    const steps = Array.from({ length: 8 }, () => stepsDue(clock, RETRO_STEP / 2));
    expect(steps.reduce((a, b) => a + b, 0)).toBe(4);
    expect(Math.max(...steps)).toBe(1);
  });

  it("catches up after a short hitch", () => {
    const clock = createRetroClock();
    expect(stepsDue(clock, RETRO_STEP * 3)).toBe(3);
    expect(stepsDue(clock, RETRO_STEP)).toBe(1);
  });

  it("drops the backlog after a long stall instead of fast-forwarding", () => {
    const clock = createRetroClock();
    expect(stepsDue(clock, 2)).toBe(MAX_STEPS_PER_FRAME);
    expect(clock.acc).toBe(0);
    expect(stepsDue(clock, RETRO_STEP)).toBe(1);
  });

  it("ignores negative and non-finite time", () => {
    const clock = createRetroClock();
    expect(stepsDue(clock, -1)).toBe(0);
    expect(stepsDue(clock, Number.NaN)).toBe(0);
    expect(stepsDue(clock, Number.POSITIVE_INFINITY)).toBe(0);
    expect(clock.acc).toBe(0);
  });

  it("forgets collected time on reset", () => {
    const clock = createRetroClock();
    stepsDue(clock, RETRO_STEP * 0.9);
    resetClock(clock);
    expect(stepsDue(clock, RETRO_STEP * 0.5)).toBe(0);
  });
});
