import { describe, expect, it } from "vitest";
import { startOfficeFrameBudget } from "./officeFrameBudget";

function clock() {
  let pending: FrameRequestCallback | undefined;
  let count = 0;
  let nextId = 0;
  const stop = startOfficeFrameBudget(
    () => { count += 1; }, 30,
    (callback) => { pending = callback; return ++nextId; },
    () => { pending = undefined; },
  );
  return {
    step(now: number) { const callback = pending; pending = undefined; callback?.(now); },
    get count() { return count; },
    get pending() { return pending; },
    stop,
  };
}

describe("office side-panel frame budget", () => {
  it.each([60, 120, 144])("renders 30 frames on a %i Hz display", (hz) => {
    const timer = clock();
    for (let frame = 0; frame < hz * 60; frame += 1) timer.step(frame * 1000 / hz);
    expect(timer.count).toBe(30 * 60);
    timer.stop();
  });

  it("does not burst to catch up after the main thread stalls", () => {
    const timer = clock();
    timer.step(0);
    timer.step(10000);
    timer.step(10001);
    expect(timer.count).toBe(2);
    timer.stop();
  });

  it("stops scheduling when hidden or unmounted, including an in-flight callback", () => {
    const timer = clock();
    const inFlight = timer.pending;
    timer.stop();
    inFlight?.(100);
    expect(timer.count).toBe(0);
    expect(timer.pending).toBeUndefined();
  });
});
