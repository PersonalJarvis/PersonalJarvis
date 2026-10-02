import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetConnectBudgetForTests } from "./connectBudget";
import { FOCUS_COALESCE_MS, onReturnToWindow, onSharedReturnToWindow } from "./focusRefresh";

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers();
  resetConnectBudgetForTests();
  setVisibility("visible");
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  resetConnectBudgetForTests();
});

describe("onReturnToWindow", () => {
  it("coalesces focus and visibilitychange into one call", () => {
    const run = vi.fn();
    const stop = onReturnToWindow(run, { random: () => 0.5 });

    window.dispatchEvent(new Event("focus"));
    setVisibility("visible");
    window.dispatchEvent(new Event("focus"));
    vi.advanceTimersByTime(FOCUS_COALESCE_MS * 2);

    expect(run).toHaveBeenCalledTimes(1);
    stop();
  });

  it("waits a jittered delay instead of firing in the same tick", () => {
    const run = vi.fn();
    const stop = onReturnToWindow(run, { random: () => 0.9 });

    window.dispatchEvent(new Event("focus"));
    expect(run).not.toHaveBeenCalled();
    // spreadDelay(250) = 125 + 0.9 * 250 = 350
    vi.advanceTimersByTime(300);
    expect(run).not.toHaveBeenCalled();
    vi.advanceTimersByTime(100);
    expect(run).toHaveBeenCalledTimes(1);
    stop();
  });

  it("does nothing while the window is hidden and never polls", () => {
    const run = vi.fn();
    const stop = onReturnToWindow(run);

    setVisibility("hidden");
    window.dispatchEvent(new Event("focus"));
    vi.advanceTimersByTime(10 * 60_000);

    expect(run).not.toHaveBeenCalled();
    stop();
  });

  it("skips a short absence when a minimum is set, and runs after a long one", () => {
    let clock = 1_000_000;
    const run = vi.fn();
    const stop = onReturnToWindow(run, { minHiddenMs: 30_000, random: () => 0, now: () => clock });

    window.dispatchEvent(new Event("blur"));
    clock += 5_000;
    window.dispatchEvent(new Event("focus"));
    vi.advanceTimersByTime(1_000);
    expect(run).not.toHaveBeenCalled();

    setVisibility("hidden");
    clock += 31_000;
    setVisibility("visible");
    vi.advanceTimersByTime(1_000);
    expect(run).toHaveBeenCalledTimes(1);
    stop();
  });

  it("stops reacting once unsubscribed, and cancels a call already scheduled", () => {
    const run = vi.fn();
    const stop = onReturnToWindow(run, { random: () => 0.5 });

    window.dispatchEvent(new Event("focus"));
    stop();
    vi.advanceTimersByTime(1_000);
    window.dispatchEvent(new Event("focus"));
    vi.advanceTimersByTime(1_000);

    expect(run).not.toHaveBeenCalled();
  });
});

describe("onSharedReturnToWindow", () => {
  it("fans one return out to every subscriber from ONE scheduled tick", () => {
    const a = vi.fn();
    const b = vi.fn();
    const c = vi.fn();
    const stops = [onSharedReturnToWindow(a), onSharedReturnToWindow(b), onSharedReturnToWindow(c)];

    window.dispatchEvent(new Event("focus"));
    document.dispatchEvent(new Event("visibilitychange"));
    // One timer for three subscribers (a per-instance listener would have scheduled three).
    expect(vi.getTimerCount()).toBe(1);
    vi.advanceTimersByTime(FOCUS_COALESCE_MS * 2);

    expect([a, b, c].map((fn) => fn.mock.calls.length)).toEqual([1, 1, 1]);
    stops.forEach((stop) => stop());
  });

  it("keeps the minimum absence per subscriber", () => {
    let clock = 5_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => clock);
    const always = vi.fn();
    const afterLong = vi.fn();
    const stops = [onSharedReturnToWindow(always), onSharedReturnToWindow(afterLong, { minHiddenMs: 30_000 })];

    window.dispatchEvent(new Event("blur"));
    clock += 5_000;
    window.dispatchEvent(new Event("focus"));
    vi.advanceTimersByTime(1_000);
    expect(always).toHaveBeenCalledTimes(1);
    expect(afterLong).not.toHaveBeenCalled();

    window.dispatchEvent(new Event("blur"));
    clock += 31_000;
    window.dispatchEvent(new Event("focus"));
    vi.advanceTimersByTime(1_000);
    expect(always).toHaveBeenCalledTimes(2);
    expect(afterLong).toHaveBeenCalledTimes(1);
    stops.forEach((stop) => stop());
  });

  it("one failing subscriber does not stop the others, and the listener leaves with the last one", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const ok = vi.fn();
    const stopBad = onSharedReturnToWindow(() => {
      throw new Error("boom");
    });
    const stopOk = onSharedReturnToWindow(ok);

    window.dispatchEvent(new Event("focus"));
    vi.advanceTimersByTime(1_000);
    expect(ok).toHaveBeenCalledTimes(1);

    stopBad();
    stopOk();
    window.dispatchEvent(new Event("focus"));
    vi.advanceTimersByTime(1_000);
    expect(ok).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });
});
