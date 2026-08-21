import { renderHook, act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useGraphAwake, type GraphEngineApi } from "./useGraphAwake";

/**
 * jsdom reports the document as focused and visible by default, which is the
 * watched state. These push it the other way and back.
 */
let hidden = false;
let focused = true;

function pretend(state: { hidden?: boolean; focused?: boolean }): void {
  if (state.hidden !== undefined) hidden = state.hidden;
  if (state.focused !== undefined) focused = state.focused;
  vi.spyOn(document, "hidden", "get").mockReturnValue(hidden);
  vi.spyOn(document, "hasFocus").mockReturnValue(focused);
}

function engine(): GraphEngineApi & {
  pauseAnimation: ReturnType<typeof vi.fn>;
  resumeAnimation: ReturnType<typeof vi.fn>;
} {
  return { pauseAnimation: vi.fn(), resumeAnimation: vi.fn() };
}

afterEach(() => {
  vi.restoreAllMocks();
  hidden = false;
  focused = true;
});

describe("useGraphAwake", () => {
  it("leaves a watched map running", () => {
    const graph = engine();
    pretend({ hidden: false, focused: true });

    renderHook(() => useGraphAwake({ current: graph }));

    expect(graph.pauseAnimation).not.toHaveBeenCalled();
    expect(graph.resumeAnimation).not.toHaveBeenCalled();
  });

  it("pauses the engine when the window loses focus", () => {
    const graph = engine();
    pretend({ focused: true });
    renderHook(() => useGraphAwake({ current: graph }));

    act(() => {
      pretend({ focused: false });
      window.dispatchEvent(new Event("blur"));
    });

    expect(graph.pauseAnimation).toHaveBeenCalledTimes(1);
  });

  it("pauses the engine when the document is hidden", () => {
    const graph = engine();
    pretend({ hidden: false });
    renderHook(() => useGraphAwake({ current: graph }));

    act(() => {
      pretend({ hidden: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });

    expect(graph.pauseAnimation).toHaveBeenCalledTimes(1);
  });

  it("resumes when the window comes back", () => {
    const graph = engine();
    pretend({ focused: true });
    renderHook(() => useGraphAwake({ current: graph }));

    act(() => {
      pretend({ focused: false });
      window.dispatchEvent(new Event("blur"));
    });
    act(() => {
      pretend({ focused: true });
      window.dispatchEvent(new Event("focus"));
    });

    expect(graph.pauseAnimation).toHaveBeenCalledTimes(1);
    expect(graph.resumeAnimation).toHaveBeenCalledTimes(1);
  });

  it("does not pause an already paused engine", () => {
    const graph = engine();
    pretend({ focused: true });
    renderHook(() => useGraphAwake({ current: graph }));

    act(() => {
      pretend({ focused: false });
      window.dispatchEvent(new Event("blur"));
      window.dispatchEvent(new Event("blur"));
      document.dispatchEvent(new Event("visibilitychange"));
    });

    expect(graph.pauseAnimation).toHaveBeenCalledTimes(1);
  });

  it("never hands the scene back paused", () => {
    const graph = engine();
    pretend({ focused: true });
    const view = renderHook(() => useGraphAwake({ current: graph }));

    act(() => {
      pretend({ focused: false });
      window.dispatchEvent(new Event("blur"));
    });
    view.unmount();

    expect(graph.resumeAnimation).toHaveBeenCalledTimes(1);
  });

  it("stays out of the way when disabled", () => {
    const graph = engine();
    pretend({ focused: true });
    renderHook(() => useGraphAwake({ current: graph }, false));

    act(() => {
      pretend({ focused: false });
      window.dispatchEvent(new Event("blur"));
    });

    expect(graph.pauseAnimation).not.toHaveBeenCalled();
  });

  it("pauses without any event at all", () => {
    // The case the first cut missed entirely: a window the user never clicked
    // into never fires `blur` when they work elsewhere. Measured in the real
    // WebView, `hasFocus()` was already false while the page painted 60 fps,
    // because nothing had asked. The timer is what asks.
    vi.useFakeTimers();
    try {
      const graph = engine();
      pretend({ focused: true });
      renderHook(() => useGraphAwake({ current: graph }));

      pretend({ focused: false });
      expect(graph.pauseAnimation).not.toHaveBeenCalled();

      act(() => {
        vi.advanceTimersByTime(2_500);
      });

      expect(graph.pauseAnimation).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("resumes without any event at all", () => {
    vi.useFakeTimers();
    try {
      const graph = engine();
      pretend({ focused: false });
      renderHook(() => useGraphAwake({ current: graph }));
      act(() => {
        vi.advanceTimersByTime(2_500);
      });
      expect(graph.pauseAnimation).toHaveBeenCalledTimes(1);

      pretend({ focused: true });
      act(() => {
        vi.advanceTimersByTime(2_500);
      });

      expect(graph.resumeAnimation).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("polls at most once per state change, not once per tick", () => {
    vi.useFakeTimers();
    try {
      const graph = engine();
      pretend({ focused: false });
      renderHook(() => useGraphAwake({ current: graph }));

      act(() => {
        vi.advanceTimersByTime(30_000);
      });

      expect(graph.pauseAnimation).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops polling once unmounted", () => {
    vi.useFakeTimers();
    try {
      const graph = engine();
      pretend({ focused: true });
      const view = renderHook(() => useGraphAwake({ current: graph }));
      view.unmount();

      pretend({ focused: false });
      act(() => {
        vi.advanceTimersByTime(30_000);
      });

      expect(graph.pauseAnimation).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("survives a ref that has no instance yet", () => {
    const ref: { current: GraphEngineApi | undefined } = { current: undefined };
    pretend({ focused: true });

    expect(() => {
      const view = renderHook(() => useGraphAwake(ref));
      act(() => {
        window.dispatchEvent(new Event("blur"));
      });
      view.unmount();
    }).not.toThrow();
  });
});
