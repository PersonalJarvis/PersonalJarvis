import { describe, expect, it } from "vitest";

import { LevelMeter } from "./levelMeter";

function pushFrames(meter: LevelMeter, rms: number, frames: number): number {
  let level = 0;
  for (let i = 0; i < frames; i++) level = meter.push(rms);
  return level;
}

describe("LevelMeter", () => {
  it("stays near zero on silence", () => {
    const meter = new LevelMeter();
    let level = 0;
    for (let i = 0; i < 60; i++) level = meter.push(0.0001);
    expect(level).toBeLessThan(0.05);
  });

  it("rises well above the floor on speech-level input", () => {
    const meter = new LevelMeter();
    // settle the noise floor on hiss first, like a real quiet room
    for (let i = 0; i < 30; i++) meter.push(0.0005);
    let level = 0;
    for (let i = 0; i < 10; i++) level = meter.push(0.05);
    expect(level).toBeGreaterThan(0.3);
  });

  it("preserves soft, normal, and loud input differences", () => {
    const meter = new LevelMeter();
    pushFrames(meter, 0.0008, 60);

    const soft = pushFrames(meter, 0.02, 12);
    const normal = pushFrames(meter, 0.06, 12);
    const loud = pushFrames(meter, 0.2, 12);

    expect(soft).toBeGreaterThan(0.3);
    expect(soft).toBeLessThan(0.7);
    expect(normal).toBeGreaterThan(soft + 0.12);
    expect(loud).toBeGreaterThan(normal + 0.12);
    expect(loud).toBeGreaterThan(0.9);
  });

  it("does not let one impulse suppress the following voice", () => {
    const meter = new LevelMeter();
    pushFrames(meter, 0.0008, 60);

    meter.push(0.5);
    const recovered = pushFrames(meter, 0.06, 6);

    expect(recovered).toBeGreaterThan(0.65);
  });

  it("attacks faster than it releases", () => {
    const meter = new LevelMeter();
    for (let i = 0; i < 30; i++) meter.push(0.0005);
    const quiet = meter.push(0.0005);
    const afterOneLoud = meter.push(0.08);
    const attackDelta = afterOneLoud - quiet;
    const afterOneQuiet = meter.push(0.0005);
    const releaseDelta = afterOneLoud - afterOneQuiet;
    expect(attackDelta).toBeGreaterThan(0);
    expect(releaseDelta).toBeGreaterThan(0);
    expect(attackDelta).toBeGreaterThan(releaseDelta);
  });

  it("clamps bad input to zero instead of propagating NaN", () => {
    const meter = new LevelMeter();
    expect(meter.push(Number.NaN)).toBeGreaterThanOrEqual(0);
    expect(meter.push(-1)).toBeGreaterThanOrEqual(0);
    expect(Number.isFinite(meter.push(0.01))).toBe(true);
  });

  it("reset returns to the resting state", () => {
    const meter = new LevelMeter();
    for (let i = 0; i < 10; i++) meter.push(0.1);
    meter.reset();
    expect(meter.push(0.0001)).toBeLessThan(0.05);
  });
});
