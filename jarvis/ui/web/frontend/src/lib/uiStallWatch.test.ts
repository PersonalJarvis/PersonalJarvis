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

  describe("long animation frames", () => {
    /** A long animation frame, as Chromium reports one. */
    function loaf(
      duration: number,
      extra: Partial<{
        startTime: number;
        renderStart: number;
        styleAndLayoutStart: number;
        blockingDuration: number;
        scripts: unknown[];
      }> = {},
    ): PerformanceEntry {
      return {
        entryType: "long-animation-frame",
        name: "long-animation-frame",
        startTime: 0,
        duration,
        ...extra,
      } as never;
    }

    it("names the function and file that blocked the thread", () => {
      // The whole reason this path exists: `longtask` could only ever say
      // "unknown/window", which named nothing and left the bug unfixable.
      const state = installObserver();
      const report = vi.fn();
      watchUiStalls(report);

      state.cb?.({
        getEntries: () => [
          loaf(2400, {
            scripts: [
              {
                duration: 120,
                sourceFunctionName: "tick",
                sourceURL: "http://127.0.0.1:47821/assets/small-abc.js",
              },
              {
                duration: 2200,
                sourceFunctionName: "handlePaneOutput",
                sourceURL: "http://127.0.0.1:47821/assets/AgenticTerminal-xY9.js?v=2",
                invokerType: "event-listener",
                forcedStyleAndLayoutDuration: 310,
              },
            ],
          }),
        ],
      });

      const detail = report.mock.calls[0][0].detail;
      // The worst script wins, not merely the first one reported.
      expect(detail).toContain("script=handlePaneOutput@AgenticTerminal-xY9.js");
      expect(detail).toContain("2200ms");
      expect(detail).toContain("via=event-listener");
      expect(detail).toContain("forced-layout=310ms");
      expect(detail).toContain("+1more");
      // The query string is not part of the file's identity.
      expect(detail).not.toContain("?v=2");
    });

    it("splits the frame into work, render and layout", () => {
      // With several terminal panes mounted, "one handler ran long" and "layout
      // re-ran over a huge DOM" feel identical to the user but need opposite
      // fixes — so the phase split is the point, not a decoration.
      const state = installObserver();
      const report = vi.fn();
      watchUiStalls(report);

      state.cb?.({
        getEntries: () => [
          loaf(3000, {
            startTime: 1000,
            renderStart: 3200,
            styleAndLayoutStart: 3500,
            blockingDuration: 2950,
          }),
        ],
      });

      const detail = report.mock.calls[0][0].detail;
      expect(detail).toContain("work=2200ms"); // 3200 - 1000
      expect(detail).toContain("render=300ms"); // 3500 - 3200
      expect(detail).toContain("layout=500ms"); // (1000 + 3000) - 3500
      expect(detail).toContain("blocking=2950ms");
    });

    it("never sends the invoker string, which can carry DOM ids", () => {
      const state = installObserver();
      const report = vi.fn();
      watchUiStalls(report);

      state.cb?.({
        getEntries: () => [
          loaf(2000, {
            scripts: [
              {
                duration: 2000,
                sourceFunctionName: "onData",
                sourceURL: "/assets/x.js",
                invokerType: "event-listener",
                // A real terminal's uuid arrives here; it is page content.
                invoker: "DIV#agentic-terminal-59c1e52e-679d-483b.onmessage",
              },
            ],
          }),
        ],
      });

      const detail = report.mock.calls[0][0].detail;
      expect(detail).not.toContain("59c1e52e");
      expect(detail).not.toContain("agentic-terminal");
      expect(detail.length).toBeLessThanOrEqual(200);
    });

    it("prefers the attributed entry when both types fire for one block", () => {
      const state = installObserver();
      const report = vi.fn();
      watchUiStalls(report);

      // The long task is LONGER, so a naive "worst first" would pick it and
      // throw the attribution away.
      state.cb?.({
        getEntries: () => [
          entry(5000, [{ name: "unknown", containerType: "window" }]),
          loaf(4000, {
            scripts: [{ duration: 3900, sourceFunctionName: "reflowAll", sourceURL: "/a/p.js" }],
          }),
        ],
      });

      expect(report).toHaveBeenCalledTimes(1);
      expect(report.mock.calls[0][0].detail).toContain("script=reflowAll@p.js");
      expect(report.mock.calls[0][0].blocked_ms).toBe(4000);
    });

    it("survives a frame with no scripts at all", () => {
      // A frame blocked purely by layout or GC reports no scripts.
      const state = installObserver();
      const report = vi.fn();
      watchUiStalls(report);

      state.cb?.({ getEntries: () => [loaf(1500)] });

      expect(report).toHaveBeenCalledTimes(1);
      expect(typeof report.mock.calls[0][0].detail).toBe("string");
    });

    it("still watches long tasks where animation frames are unsupported", () => {
      // Older WebView builds have `longtask` but not `long-animation-frame`;
      // losing the reporter entirely there would be a regression.
      const state: { cb: ObserverCallback | null; observed: string[] } = {
        cb: null,
        observed: [],
      };
      class PartialObserver {
        constructor(fn: ObserverCallback) {
          state.cb = fn;
        }
        observe(options: { type: string }) {
          if (options.type === "long-animation-frame") {
            throw new TypeError("unsupported entry type");
          }
          state.observed.push(options.type);
        }
        disconnect() {}
      }
      vi.stubGlobal("PerformanceObserver", PartialObserver);
      const report = vi.fn();
      watchUiStalls(report);

      expect(state.observed).toEqual(["longtask"]);
      state.cb?.({ getEntries: () => [entry(2000)] });
      expect(report).toHaveBeenCalledTimes(1);
    });
  });
});
