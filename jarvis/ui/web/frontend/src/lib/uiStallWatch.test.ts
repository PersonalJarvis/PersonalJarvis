/**
 * The UI-stall reporter.
 *
 * It exists because a blocked browser main thread is invisible inside a WebView
 * — the user gets "Not responding" in the title bar and nothing else. So the
 * two properties that matter are pinned here: it must stay silent through
 * ordinary slow moments, and it must never become a source of load itself.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { watchUiStalls } from "./uiStallWatch";

type ObserverCallback = (list: { getEntries: () => PerformanceEntry[] }) => void;

/** Stand-in for the browser's observer, driven by the test. */
function installObserver() {
  const state: { cb: ObserverCallback | null; observed: unknown[]; disconnected: number } = {
    cb: null,
    observed: [],
    disconnected: 0,
  };
  class FakeObserver {
    constructor(cb: ObserverCallback) {
      state.cb = cb;
    }
    observe(options: unknown) {
      state.observed.push(options);
    }
    disconnect() {
      state.disconnected += 1;
    }
  }
  vi.stubGlobal("PerformanceObserver", FakeObserver);
  return state;
}

function entry(duration: number, attribution?: unknown[]): PerformanceEntry {
  return { duration, name: "self", entryType: "longtask", startTime: 0, attribution } as never;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("watchUiStalls", () => {
  it("stays silent for tasks below the reporting bar", () => {
    const state = installObserver();
    const report = vi.fn();
    watchUiStalls(report);

    // A GC pause, a large terminal write, a pane reflow — all survivable.
    state.cb?.({ getEntries: () => [entry(60), entry(240), entry(950)] });

    expect(report).not.toHaveBeenCalled();
  });

  it("reports a multi-second block", () => {
    const state = installObserver();
    const report = vi.fn();
    watchUiStalls(report);

    state.cb?.({ getEntries: () => [entry(4200)] });

    expect(report).toHaveBeenCalledTimes(1);
    expect(report.mock.calls[0][0].blocked_ms).toBe(4200);
  });

  it("rate-limits a thread that keeps blocking", () => {
    const state = installObserver();
    const report = vi.fn();
    watchUiStalls(report);

    // Each report is itself a request the same thread must make; a stall storm
    // must not turn into a request storm.
    for (let i = 0; i < 20; i++) state.cb?.({ getEntries: () => [entry(3000)] });

    expect(report).toHaveBeenCalledTimes(1);
  });

  it("sends fixed labels only, never page content", () => {
    const state = installObserver();
    const report = vi.fn();
    watchUiStalls(report);

    state.cb?.({
      getEntries: () => [
        entry(2000, [{ name: "script", containerType: "iframe", containerName: "x" }]),
      ],
    });

    const payload = report.mock.calls[0][0];
    expect(payload.detail).toBe("script/iframe/x");
    expect(typeof payload.panes).toBe("number");
    expect(payload.detail.length).toBeLessThanOrEqual(120);
  });

  it("survives an entry with no attribution", () => {
    const state = installObserver();
    const report = vi.fn();
    watchUiStalls(report);

    state.cb?.({ getEntries: () => [entry(2000)] });

    expect(report.mock.calls[0][0].detail).toBe("");
  });

  it("is a no-op where the API does not exist", () => {
    vi.stubGlobal("PerformanceObserver", undefined);
    const report = vi.fn();
    const stop = watchUiStalls(report);
    expect(() => stop()).not.toThrow();
    expect(report).not.toHaveBeenCalled();
  });

  it("is a no-op when the entry type is unsupported", () => {
    class Refusing {
      constructor(_cb: ObserverCallback) {}
      observe() {
        throw new TypeError("longtask is not a valid entry type");
      }
      disconnect() {}
    }
    vi.stubGlobal("PerformanceObserver", Refusing);
    const report = vi.fn();
    expect(() => watchUiStalls(report)()).not.toThrow();
    expect(report).not.toHaveBeenCalled();
  });

  it("stops observing when told to", () => {
    const state = installObserver();
    const stop = watchUiStalls(vi.fn());
    stop();
    expect(state.disconnected).toBe(1);
  });
});
