import { renderHook, act } from "@testing-library/react";
import { describe, expect, it, afterEach } from "vitest";

import { clampSize, useResizablePane } from "./useResizablePane";

afterEach(() => {
  window.localStorage.clear();
  document.body.style.cursor = "";
  document.body.style.userSelect = "";
});

/**
 * One drag, start to finish, on whichever axis the pane uses.
 *
 * `MouseEvent` rather than `PointerEvent` because jsdom does not ship the
 * latter everywhere; the hook only ever reads `clientX`/`clientY`, which both
 * carry.
 */
function drag(
  pane: { startResize: (e: React.PointerEvent) => void },
  from: { x: number; y: number },
  to: { x: number; y: number },
) {
  act(() => {
    pane.startResize({
      clientX: from.x,
      clientY: from.y,
      preventDefault: () => {},
    } as unknown as React.PointerEvent);
  });
  act(() => {
    window.dispatchEvent(
      new MouseEvent("pointermove", { clientX: to.x, clientY: to.y }),
    );
  });
  act(() => {
    window.dispatchEvent(new MouseEvent("pointerup"));
  });
}

describe("clampSize", () => {
  it("clamps below min and above max", () => {
    expect(clampSize(50, 200, 480)).toBe(200);
    expect(clampSize(900, 200, 480)).toBe(480);
    expect(clampSize(300, 200, 480)).toBe(300);
  });

  it("rounds fractional sizes to whole pixels", () => {
    expect(clampSize(300.7, 200, 480)).toBe(301);
  });

  it("falls back to min on NaN", () => {
    expect(clampSize(Number.NaN, 200, 480)).toBe(200);
  });

  it("lets a measured ceiling win over the floor on a tiny window", () => {
    // A prompt bar whose max is derived from the window height can legitimately
    // end up below its own minimum. Honouring the minimum there would overflow
    // the frame and push the grid out of the bottom of the screen.
    expect(clampSize(200, 120, 60)).toBe(60);
  });
});

describe("useResizablePane", () => {
  const opts = { storageKey: "test.size", defaultSize: 260, min: 200, max: 480 };

  it("starts at the default size when storage is empty", () => {
    const { result } = renderHook(() => useResizablePane(opts));
    expect(result.current.size).toBe(260);
  });

  it("restores a persisted size on mount", () => {
    window.localStorage.setItem("test.size", "320");
    const { result } = renderHook(() => useResizablePane(opts));
    expect(result.current.size).toBe(320);
  });

  it("clamps an out-of-band persisted size back into the band", () => {
    window.localStorage.setItem("test.size", "9999");
    const { result } = renderHook(() => useResizablePane(opts));
    expect(result.current.size).toBe(480);
  });

  it("keeps a wider preference while a measured maximum is temporarily smaller", () => {
    window.localStorage.setItem("test.size", "460");
    const { result, rerender } = renderHook(
      ({ max }) => useResizablePane({ ...opts, max }),
      { initialProps: { max: 480 } },
    );
    expect(result.current.size).toBe(460);

    rerender({ max: 300 });
    expect(result.current.size).toBe(300);
    expect(window.localStorage.getItem("test.size")).toBe("460");

    // Interaction starts from the visible ceiling, not the hidden preference.
    drag(result.current, { x: 300, y: 0 }, { x: 280, y: 0 });
    expect(result.current.size).toBe(280);
    expect(window.localStorage.getItem("test.size")).toBe("280");
  });

  it("reset() returns to the default and persists it", () => {
    window.localStorage.setItem("test.size", "300");
    const { result } = renderHook(() => useResizablePane(opts));
    act(() => result.current.reset());
    expect(result.current.size).toBe(260);
    expect(window.localStorage.getItem("test.size")).toBe("260");
  });

  it("grows a width when the grip on its right edge is dragged right", () => {
    const { result } = renderHook(() => useResizablePane(opts));
    drag(result.current, { x: 260, y: 0 }, { x: 340, y: 0 });
    expect(result.current.size).toBe(340);
    expect(window.localStorage.getItem("test.size")).toBe("340");
  });

  it("grows a height when the grip on its TOP edge is dragged UP", () => {
    // The inverted axis is the whole reason `handle` exists: a prompt bar sits
    // below its seam, so the pointer travelling to a SMALLER y makes it taller.
    const { result } = renderHook(() =>
      useResizablePane({
        storageKey: "test.height",
        defaultSize: 176,
        min: 34,
        max: 520,
        axis: "y",
        handle: "start",
      }),
    );
    drag(result.current, { x: 0, y: 800 }, { x: 0, y: 700 });
    expect(result.current.size).toBe(276);
  });

  it("collapses that height when the same grip is dragged to the bottom", () => {
    const { result } = renderHook(() =>
      useResizablePane({
        storageKey: "test.height",
        defaultSize: 176,
        min: 34,
        max: 520,
        axis: "y",
        handle: "start",
      }),
    );
    drag(result.current, { x: 0, y: 700 }, { x: 0, y: 1400 });
    expect(result.current.size).toBe(34);
  });

  /*
   * A burst of pointer moves costs ONE size change, not one each.
   *
   * This size is a layout — in the Agentic IDE it sets the height of every
   * terminal above the prompt bar — and a pointer emits far more moves than
   * there are frames to draw them in. So the moves are coalesced and the last
   * one wins, which is what the size standing still until the frame arrives
   * shows here.
   */
  it("coalesces a burst of pointer moves into one size change per frame", async () => {
    const { result } = renderHook(() => useResizablePane(opts));
    act(() => {
      result.current.startResize({
        clientX: 260,
        clientY: 0,
        preventDefault: () => {},
      } as unknown as React.PointerEvent);
    });

    act(() => {
      for (const x of [300, 320, 340]) {
        window.dispatchEvent(new MouseEvent("pointermove", { clientX: x }));
      }
    });
    expect(result.current.size).toBe(260);

    await act(async () => {
      await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
    });
    expect(result.current.size).toBe(340);
  });

  it("finishes a drag on pointer cancellation or window blur", () => {
    document.body.style.cursor = "crosshair";
    document.body.style.userSelect = "text";
    const { result } = renderHook(() => useResizablePane(opts));

    act(() => {
      result.current.startResize({
        clientX: 260,
        clientY: 0,
        preventDefault: () => {},
      } as unknown as React.PointerEvent);
    });
    expect(result.current.isResizing).toBe(true);
    expect(document.body.style.cursor).toBe("col-resize");
    expect(document.body.style.userSelect).toBe("none");

    act(() => window.dispatchEvent(new MouseEvent("pointercancel")));
    expect(result.current.isResizing).toBe(false);
    expect(document.body.style.cursor).toBe("crosshair");
    expect(document.body.style.userSelect).toBe("text");

    act(() => {
      result.current.startResize({
        clientX: 260,
        clientY: 0,
        preventDefault: () => {},
      } as unknown as React.PointerEvent);
    });
    act(() => window.dispatchEvent(new Event("blur")));
    expect(result.current.isResizing).toBe(false);
    expect(document.body.style.cursor).toBe("crosshair");
    expect(document.body.style.userSelect).toBe("text");
  });

  it("nudge() moves the seam by whole pixels and stays inside the band", () => {
    const { result } = renderHook(() => useResizablePane(opts));
    act(() => result.current.nudge(16));
    expect(result.current.size).toBe(276);
    act(() => result.current.nudge(-9999));
    expect(result.current.size).toBe(200);
  });
});
