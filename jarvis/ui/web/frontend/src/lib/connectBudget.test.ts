/**
 * The connect budget is the thing standing between a wake and a frozen PC.
 *
 * BUG-215: coming back to the machine after a while made every socket client
 * in every window reconnect in the same millisecond, which emptied the
 * operating system's ephemeral-port pool and stopped EVERYTHING on the
 * computer from connecting for a couple of minutes.
 *
 * Two properties have to hold, and only the pair of them is enough:
 *
 * 1. Retries are jittered, so clients that started together do not stay
 *    together.
 * 2. A hard ceiling on connections per second, so that even when they do
 *    collide the total stays survivable.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BURST,
  CONNECTS_PER_SECOND,
  jitteredDelay,
  requestConnect,
  resetConnectBudgetForTests,
  spreadDelay,
} from "./connectBudget";

beforeEach(() => {
  vi.useFakeTimers();
  resetConnectBudgetForTests();
});

afterEach(() => {
  resetConnectBudgetForTests();
  vi.useRealTimers();
});

describe("jitteredDelay", () => {
  it("picks from the whole range below the exponential ceiling", () => {
    // Full jitter, not half: the low end must be reachable, or clients keep a
    // shared floor and so keep a (thinner) grid.
    expect(jitteredDelay(0, 500, 10_000, () => 0)).toBe(0);
    expect(jitteredDelay(0, 500, 10_000, () => 0.999)).toBe(499);
  });

  it("doubles the ceiling per attempt, up to the cap", () => {
    const top = (attempt: number) => jitteredDelay(attempt, 500, 10_000, () => 0.999999);
    expect(top(0)).toBe(499);
    expect(top(1)).toBe(999);
    expect(top(2)).toBe(1999);
    expect(top(20)).toBe(9999);
  });

  it("survives an absurd attempt count instead of returning NaN", () => {
    // 2 ** 1024 is Infinity and Infinity * base is NaN — a NaN delay makes
    // setTimeout fire immediately, which is the storm this file prevents.
    const delay = jitteredDelay(5000, 500, 10_000, () => 0.5);
    expect(Number.isFinite(delay)).toBe(true);
    expect(delay).toBeLessThanOrEqual(10_000);
  });

  it("treats a negative attempt as the first one", () => {
    expect(jitteredDelay(-3, 500, 10_000, () => 0.999)).toBe(499);
  });
});

describe("spreadDelay", () => {
  it("keeps the average interval and destroys the phase", () => {
    // A slow knock must stay slow. Full jitter would halve it.
    expect(spreadDelay(30_000, () => 0)).toBe(15_000);
    expect(spreadDelay(30_000, () => 0.5)).toBe(30_000);
    expect(spreadDelay(30_000, () => 0.999999)).toBe(44_999);
  });
});

describe("requestConnect", () => {
  it("lets an ordinary page load through without pacing it", () => {
    const opened: number[] = [];
    for (let i = 0; i < BURST; i += 1) {
      requestConnect(() => opened.push(i), 0);
    }

    vi.advanceTimersByTime(0);

    expect(opened).toHaveLength(BURST);
  });

  it("caps a wake storm at the budget instead of letting it through", () => {
    // The BUG-215 shape: twenty clients, no delay, all in the same tick.
    const opened: number[] = [];
    for (let i = 0; i < 20; i += 1) {
      requestConnect(() => opened.push(i), 0);
    }

    vi.advanceTimersByTime(0);
    expect(opened).toHaveLength(BURST);

    // The rest arrive at the refill rate, not all at once.
    vi.advanceTimersByTime(1000);
    expect(opened.length).toBeLessThanOrEqual(BURST + CONNECTS_PER_SECOND + 1);

    // ...and none of them is dropped: the budget delays, it never refuses.
    vi.advanceTimersByTime(5000);
    expect(opened).toHaveLength(20);
  });

  it("preserves the order clients asked in", () => {
    const opened: number[] = [];
    for (let i = 0; i < 20; i += 1) {
      requestConnect(() => opened.push(i), 0);
    }

    vi.advanceTimersByTime(10_000);

    expect(opened).toEqual([...Array(20).keys()]);
  });

  it("cancels a pending connect", () => {
    const opened: string[] = [];
    const cancel = requestConnect(() => opened.push("a"), 1000);
    requestConnect(() => opened.push("b"), 1000);

    cancel();
    vi.advanceTimersByTime(5000);

    expect(opened).toEqual(["b"]);
  });

  it("cancels a connect already queued behind the budget", () => {
    const opened: number[] = [];
    const cancels: Array<() => void> = [];
    for (let i = 0; i < 20; i += 1) {
      cancels.push(requestConnect(() => opened.push(i), 0));
    }
    vi.advanceTimersByTime(0);
    // Everything past the burst is waiting for a token; drop it all.
    for (const cancel of cancels) cancel();

    vi.advanceTimersByTime(10_000);

    expect(opened).toHaveLength(BURST);
  });

  it("survives a client that throws on connect", () => {
    // One broken client must not stall every socket behind it in the queue.
    const opened: string[] = [];
    requestConnect(() => {
      throw new Error("WebSocket constructor blew up");
    }, 0);
    requestConnect(() => opened.push("after"), 0);

    vi.advanceTimersByTime(0);

    expect(opened).toEqual(["after"]);
  });

  it("cancelling twice is safe", () => {
    const cancel = requestConnect(() => undefined, 1000);
    cancel();
    expect(() => cancel()).not.toThrow();
  });

  it("jittered retries from one wake do not land in the same millisecond", () => {
    // The end-to-end property: twenty panes wake together, each schedules its
    // own retry through `jitteredDelay`, and the arrivals spread out.
    const arrivals: number[] = [];
    let seed = 0;
    const random = () => {
      seed += 1;
      return (seed % 17) / 17;
    };
    for (let i = 0; i < 20; i += 1) {
      const delay = jitteredDelay(0, 500, 10_000, random);
      requestConnect(() => arrivals.push(delay), delay);
    }

    vi.advanceTimersByTime(10_000);

    expect(arrivals).toHaveLength(20);
    expect(new Set(arrivals).size).toBeGreaterThan(10);
  });
});
