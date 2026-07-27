/**
 * Render-loop and camera guards for the Memory-Map.
 *
 * `WikiGraph.test.tsx` mocks `react-force-graph-2d` with a prop recorder and
 * no imperative handle, which is enough for data/click behaviour but blind to
 * everything the component does THROUGH the library instance. These tests
 * install a handle so the expensive paths are observable:
 *
 *   1. d3 forces are configured once per data generation, not per engine tick
 *      (the tick handler used to re-initialise every force on every frame).
 *   2. a settled engine does not yank the camera back after the user panned.
 *   3. the component's own fit animation is not mistaken for a user pan.
 *   4. the render loop is parked once nothing needs frames any more.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PropsWithChildren } from "react";

import { WikiGraph } from "@/components/wiki/WikiGraph";

const { forceGraphProps, handle } = vi.hoisted(() => {
  const chargeForce = { strength: vi.fn(), distanceMax: vi.fn() };
  const linkForce = { distance: vi.fn(), strength: vi.fn() };
  const centerForce = { strength: vi.fn() };
  return {
    forceGraphProps: [] as Array<Record<string, unknown>>,
    handle: {
      chargeForce,
      linkForce,
      centerForce,
      d3Force: vi.fn((name: string) => {
        if (name === "charge") return chargeForce;
        if (name === "link") return linkForce;
        if (name === "center") return centerForce;
        return undefined;
      }),
      zoomToFit: vi.fn(),
      centerAt: vi.fn(),
      zoom: vi.fn(() => 1),
      getGraphBbox: vi.fn(() => ({ x: [-100, 100], y: [-100, 100] })),
      screen2GraphCoords: vi.fn(() => ({ x: 0, y: 0 })),
      pauseAnimation: vi.fn(),
      resumeAnimation: vi.fn(),
    },
  };
});

vi.mock("react-force-graph-2d", async () => {
  const { forwardRef, useImperativeHandle } = await import("react");
  return {
    default: forwardRef(function ForceGraphMock(
      props: Record<string, unknown>,
      ref,
    ) {
      forceGraphProps.push(props);
      useImperativeHandle(ref, () => handle, []);
      return null;
    }),
  };
});

const PAYLOAD = {
  ok: true,
  nodes: [
    { id: "ruben", kind: "entity", title: "Ruben" },
    { id: "harald", kind: "entity", title: "Harald" },
    { id: "golf", kind: "concept", title: "Golf" },
  ],
  edges: [{ source: "ruben", target: "harald", context: "knows" }],
  broken: [],
};

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
}

function Wrapper({ children, client }: PropsWithChildren<{ client: QueryClient }>) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

/** Latest props the component handed to the library. */
function latest(): Record<string, unknown> {
  return forceGraphProps[forceGraphProps.length - 1];
}

async function renderGraph() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify(PAYLOAD), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ),
  );
  const client = makeClient();
  render(
    <Wrapper client={client}>
      <WikiGraph onNodeClick={() => {}} />
    </Wrapper>,
  );
  await waitFor(() => {
    expect(forceGraphProps.length).toBeGreaterThan(0);
  });
  // Let the pending auto-fit / resize-fit timers settle so each test starts
  // from a quiet camera.
  await act(async () => {
    vi.advanceTimersByTime(3000);
  });
  vi.clearAllMocks();
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  forceGraphProps.length = 0;
});

describe("WikiGraph render loop", () => {
  it("configures the d3 forces once, not on every engine tick", async () => {
    await renderGraph();
    const onEngineTick = latest().onEngineTick as () => void;

    await act(async () => {
      for (let i = 0; i < 12; i += 1) onEngineTick();
    });

    // Each of these setters walks every node (charge) or every link (link
    // distance) inside d3, so calling them per frame is an O(n) tax on the
    // whole simulation.
    expect(handle.chargeForce.strength).toHaveBeenCalledTimes(1);
    expect(handle.linkForce.distance).toHaveBeenCalledTimes(1);
    expect(handle.centerForce.strength).toHaveBeenCalledTimes(1);
  });

  it("re-fits the camera when the engine settles and the user has not panned", async () => {
    await renderGraph();
    const props = latest();

    await act(async () => {
      (props.onEngineStop as () => void)();
    });

    expect(handle.zoomToFit).toHaveBeenCalled();
  });

  it("does not yank the camera back after the user panned", async () => {
    await renderGraph();
    const props = latest();

    await act(async () => {
      (props.onZoom as () => void)(); // user drags the canvas
      (props.onEngineStop as () => void)(); // a reheat settles afterwards
    });

    expect(handle.zoomToFit).not.toHaveBeenCalled();
  });

  it("does not treat its own fit animation as a user pan", async () => {
    await renderGraph();
    const props = latest();

    await act(async () => {
      (props.onEngineStop as () => void)();
    });
    expect(handle.zoomToFit).toHaveBeenCalledTimes(1);

    // zoomToFit eases over its duration and emits a zoom event per frame.
    // Those are ours; a later settle must still be allowed to re-fit.
    await act(async () => {
      (props.onZoom as () => void)();
      (props.onEngineStop as () => void)();
    });

    expect(handle.zoomToFit).toHaveBeenCalledTimes(2);
  });

  it("parks the render loop once the layout has settled", async () => {
    await renderGraph();
    const props = latest();

    await act(async () => {
      (props.onEngineStop as () => void)();
    });
    // The fit animation still needs frames — pausing is deferred, not skipped.
    expect(handle.pauseAnimation).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(1500);
    });

    expect(handle.pauseAnimation).toHaveBeenCalled();
  });

  it("wakes the render loop back up when the pointer returns", async () => {
    await renderGraph();
    const props = latest();

    await act(async () => {
      (props.onEngineStop as () => void)();
      vi.advanceTimersByTime(1500);
    });
    expect(handle.pauseAnimation).toHaveBeenCalled();

    const wrap = document.querySelector('[data-testid="wiki-graph-wrap"]');
    expect(wrap).not.toBeNull();
    await act(async () => {
      wrap?.dispatchEvent(new Event("pointerenter"));
    });

    expect(handle.resumeAnimation).toHaveBeenCalled();
  });
});
