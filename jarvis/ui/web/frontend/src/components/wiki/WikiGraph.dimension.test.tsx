/**
 * The 2D/3D switch on the Memory Map.
 *
 * Three things have to hold, and the third is the one that keeps the feature
 * honest on machines that are not the developer's:
 *
 *   1. Flat is what you get by default — no WebGL chunk downloaded to read a
 *      map that never needed one.
 *   2. Choosing 3D actually swaps the renderer, and the choice is remembered.
 *   3. On a machine with no WebGL context to give, the 3D segment is visibly
 *      disabled and the map stays flat — even when a stored preference (say,
 *      from a profile synced off a machine that could) asks for 3D.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PropsWithChildren } from "react";

import { WikiGraph } from "@/components/wiki/WikiGraph";
import {
  GRAPH_DIMENSION_KEY,
  resetWebglProbe,
  setGraphDimension,
} from "@/lib/graphDimension";

const { forceGraphProps } = vi.hoisted(() => ({
  forceGraphProps: [] as Array<Record<string, unknown>>,
}));

vi.mock("react-force-graph-2d", async () => {
  const { forwardRef } = await import("react");
  return {
    default: forwardRef(function ForceGraphMock(
      props: Record<string, unknown>,
      _ref,
    ) {
      forceGraphProps.push(props);
      return <div data-testid="wiki-graph-2d-stub" />;
    }),
  };
});

// The real 3D component pulls in three.js and asks for a WebGL context, which
// jsdom cannot give. What this file tests is which renderer gets mounted, so a
// stand-in is exactly the right amount of 3D.
vi.mock("@/components/wiki/WikiGraph3D", () => ({
  WikiGraph3D: (props: { width: number; height: number }) => (
    <div
      data-testid="wiki-graph-3d-stub"
      data-width={props.width}
      data-height={props.height}
    />
  ),
}));

const PAYLOAD = {
  ok: true,
  nodes: [
    { id: "ruben", kind: "entity", title: "Ruben" },
    { id: "harald", kind: "entity", title: "Harald" },
  ],
  edges: [{ source: "ruben", target: "harald", context: "knows" }],
  broken: [],
};

/** Make the WebGL probe answer yes, the way a real GPU-backed window does. */
function pretendWebglWorks(): void {
  vi.stubGlobal("WebGLRenderingContext", class {});
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    getExtension: () => null,
  } as unknown as RenderingContext);
  resetWebglProbe();
}

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
}

function Wrapper({ children, client }: PropsWithChildren<{ client: QueryClient }>) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function renderGraph() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify(PAYLOAD), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    ),
  );
  return render(
    <Wrapper client={makeClient()}>
      <WikiGraph onNodeClick={() => {}} />
    </Wrapper>,
  );
}

beforeEach(() => {
  forceGraphProps.length = 0;
  window.localStorage.removeItem(GRAPH_DIMENSION_KEY);
  setGraphDimension("2d");
  resetWebglProbe();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  resetWebglProbe();
  window.localStorage.removeItem(GRAPH_DIMENSION_KEY);
});

describe("WikiGraph 2D/3D switch", () => {
  it("draws the flat map by default", async () => {
    pretendWebglWorks();
    renderGraph();

    expect(await screen.findByTestId("wiki-graph-2d-stub")).toBeDefined();
    expect(screen.queryByTestId("wiki-graph-3d-stub")).toBeNull();
    expect(
      screen.getByTestId("graph-dimension-toggle").getAttribute("data-dimension"),
    ).toBe("2d");
  });

  it("swaps to the 3D scene when the switch is flipped, and remembers it", async () => {
    pretendWebglWorks();
    renderGraph();
    await screen.findByTestId("wiki-graph-2d-stub");

    fireEvent.click(screen.getByTestId("graph-dimension-3d"));

    expect(await screen.findByTestId("wiki-graph-3d-stub")).toBeDefined();
    await waitFor(() => {
      expect(screen.queryByTestId("wiki-graph-2d-stub")).toBeNull();
    });
    expect(window.localStorage.getItem(GRAPH_DIMENSION_KEY)).toBe("3d");
  });

  it("hands the measured canvas size to the 3D scene", async () => {
    pretendWebglWorks();
    setGraphDimension("3d");
    renderGraph();

    const stub = await screen.findByTestId("wiki-graph-3d-stub");
    expect(Number(stub.getAttribute("data-width"))).toBeGreaterThan(0);
    expect(Number(stub.getAttribute("data-height"))).toBeGreaterThan(0);
  });

  it("stays flat and disables the 3D segment without a WebGL context", async () => {
    // No WebGL globals stubbed: this is the headless/locked-down machine.
    setGraphDimension("3d");
    renderGraph();

    expect(await screen.findByTestId("wiki-graph-2d-stub")).toBeDefined();
    expect(screen.queryByTestId("wiki-graph-3d-stub")).toBeNull();
    const segment = screen.getByTestId("graph-dimension-3d") as HTMLButtonElement;
    expect(segment.disabled).toBe(true);
    expect(segment.getAttribute("title")).toContain("cannot render 3D");
  });
});
