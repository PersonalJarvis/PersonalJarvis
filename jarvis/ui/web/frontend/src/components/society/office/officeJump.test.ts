import { describe, expect, it } from "vitest";
import { JUMP_BUFFER_S, jumpSquash, newJump, pressJump, SPRINT_JUMP_BOOST, stepJump } from "./officeJump";

const DT = 1 / 60;

function hop(running: boolean) {
  const s = newJump();
  pressJump(s);
  let apex = 0, frames = 0, maxMul = 1;
  stepJump(s, DT, false, running);
  while (s.airborne && frames < 600) {
    stepJump(s, DT, false, running);
    apex = Math.max(apex, s.y);
    maxMul = Math.max(maxMul, s.speedMul);
    frames += 1;
  }
  return { s, apex, airS: (frames + 1) * DT, maxMul };
}

describe("office jump", () => {
  it("makes a short hop that lands back on the floor", () => {
    const { s, apex, airS } = hop(false);
    expect(apex).toBeGreaterThan(0.45);
    expect(apex).toBeLessThan(0.65);
    expect(airS).toBeGreaterThan(0.45);
    expect(airS).toBeLessThan(0.6);
    expect(s.y).toBe(0);
    expect(s.airborne).toBe(false);
  });

  it("is smooth: the height never jumps by more than a few centimetres a frame", () => {
    const s = newJump();
    pressJump(s);
    let last = 0;
    for (let i = 0; i < 60; i += 1) {
      stepJump(s, DT, false, false);
      expect(Math.abs(s.y - last)).toBeLessThan(0.08);
      last = s.y;
    }
  });

  it("boosts a sprint jump above sprint speed, easing in rather than snapping", () => {
    const sprint = hop(true);
    expect(sprint.maxMul).toBeGreaterThan(1.2);
    expect(sprint.maxMul).toBeLessThanOrEqual(SPRINT_JUMP_BOOST);
    const s = newJump();
    pressJump(s);
    stepJump(s, DT, false, true);
    expect(s.speedMul).toBeLessThan(1.1);
  });

  it("gives a jump from a walk no extra speed", () => {
    expect(hop(false).maxMul).toBe(1);
  });

  it("drops the boost again after landing", () => {
    const { s } = hop(true);
    for (let i = 0; i < 60; i += 1) stepJump(s, DT, false, true);
    expect(s.speedMul).toBe(1);
  });

  it("keeps a press made just before landing and jumps on touchdown", () => {
    const s = newJump();
    pressJump(s);
    stepJump(s, DT, false, true);
    while (s.vy > 0 || s.y > 0.05) stepJump(s, DT, false, true);
    pressJump(s);
    let again = false;
    for (let t = 0; t < JUMP_BUFFER_S; t += DT) again = stepJump(s, DT, false, true) || again;
    expect(again).toBe(true);
  });

  it("does not jump again in mid-air", () => {
    const s = newJump();
    pressJump(s);
    stepJump(s, DT, false, false);
    const vy = s.vy;
    expect(stepJump(s, DT, true, false)).toBe(false);
    expect(s.vy).toBeLessThan(vy);
  });

  it("squashes and stretches only around take-off and landing", () => {
    const s = newJump();
    expect(jumpSquash(s)).toEqual([1, 1]);
    pressJump(s);
    stepJump(s, 0.08, false, false);
    expect(jumpSquash(s)[1]).toBeGreaterThan(1);
    s.sinceTakeoff = 1;
    s.sinceLanding = 0.09;
    expect(jumpSquash(s)[1]).toBeLessThan(1);
  });
});
