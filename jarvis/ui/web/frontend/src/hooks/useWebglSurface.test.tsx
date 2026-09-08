/**
 * The guard that keeps a 3D map from turning into a white rectangle.
 *
 * Three promises, and the first one is the one that caused the bug this hook
 * exists for (2026-08-21): a scene that goes away has to hand its WebGL
 * context BACK. `WebGLRenderer.dispose()` does not, so every trip into the
 * Wiki section and back leaked one of the browser's sixteen contexts, and once
 * they ran out the browser killed the oldest scene on the page — the deck's
 * wiki card — and painted its own broken-canvas placeholder over it.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { useRef } from "react";

import { MAX_CONTEXT_RECOVERIES, useWebglSurface } from "@/hooks/useWebglSurface";
import { isWebglLost, reportWebglLost } from "@/lib/graphDimension";

/** A canvas whose context can be asked for and taken away, jsdom-style. */
function fakeCanvas(): { canvas: HTMLCanvasElement; loseContext: () => void } {
  const canvas = document.createElement("canvas");
  const loseContext = vi.fn();
  const context = {
    getExtension: (name: string) =>
      name === "WEBGL_lose_context" ? { loseContext } : null,
  };
  // jsdom's canvas has no WebGL at all; a context and one extension is the
  // whole surface this hook touches.
  canvas.getContext = ((type: string) =>
    /webgl/.test(type) ? context : null) as HTMLCanvasElement["getContext"];
  return { canvas, loseContext };
}

/** A host div with the renderer's canvas already inside it. */
function Surface({ canvas }: { canvas: HTMLCanvasElement }) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const { generation } = useWebglSurface(hostRef);
  return (
    <div
      ref={(node) => {
        hostRef.current = node;
        if (node && !node.contains(canvas)) node.appendChild(canvas);
      }}
      data-testid="host"
      data-generation={generation}
    />
  );
}

/** Fire the event the browser fires when it takes a context away. */
function dropContext(canvas: HTMLCanvasElement): Event {
  const event = new Event("webglcontextlost", { cancelable: true });
  act(() => {
    canvas.dispatchEvent(event);
  });
  return event;
}

afterEach(() => {
  cleanup();
  reportWebglLost(false);
  vi.restoreAllMocks();
});

describe("useWebglSurface", () => {
  it("hands the WebGL context back when the scene goes away", async () => {
    const { canvas, loseContext } = fakeCanvas();
    const view = render(<Surface canvas={canvas} />);
    await waitFor(() => expect(canvas.parentElement).not.toBeNull());

    view.unmount();

    expect(loseContext).toHaveBeenCalledTimes(1);
  });

  it("survives a lost context: prevents the default and rebuilds the scene", async () => {
    const { canvas } = fakeCanvas();
    const view = render(<Surface canvas={canvas} />);
    await waitFor(() => expect(canvas.parentElement).not.toBeNull());

    const event = dropContext(canvas);

    // Without preventDefault the browser writes the context off for good and
    // never fires `webglcontextrestored` — the canvas stays a sad face.
    expect(event.defaultPrevented).toBe(true);
    await waitFor(() => {
      expect(view.getByTestId("host").getAttribute("data-generation")).toBe("1");
    });
    expect(isWebglLost()).toBe(false);
  });

  it("gives up after one loss too many and degrades every graph to the flat map", async () => {
    const { canvas } = fakeCanvas();
    const view = render(<Surface canvas={canvas} />);
    await waitFor(() => expect(canvas.parentElement).not.toBeNull());

    for (let attempt = 1; attempt <= MAX_CONTEXT_RECOVERIES; attempt++) {
      dropContext(canvas);
      await waitFor(() => {
        expect(view.getByTestId("host").getAttribute("data-generation")).toBe(
          String(attempt),
        );
      });
    }
    // One more than the surface is willing to rebuild.
    dropContext(canvas);

    await waitFor(() => expect(isWebglLost()).toBe(true));
    expect(view.getByTestId("host").getAttribute("data-generation")).toBe(
      String(MAX_CONTEXT_RECOVERIES),
    );
  });
});
