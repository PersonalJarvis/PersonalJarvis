/**
 * What the maps do once a graphics context is gone for good.
 *
 * A lost context is not one card's problem: the browser hands them out per
 * page, so a scene that could not get one back is telling every other scene
 * what is about to happen to it. So the flag is shared — one dead surface and
 * every map on screen goes flat, with the switch saying WHY in the user's own
 * language, instead of each of them painting the browser's broken-canvas
 * placeholder in turn.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PropsWithChildren } from "react";

import { WikiGraph } from "@/components/wiki/WikiGraph";
import {
  GRAPH_DIMENSION_KEY,
  reportWebglLost,
  resetWebglProbe,
  setGraphDimension,
} from "@/lib/graphDimension";

vi.mock("react-force-graph-2d", async () => {
  const { forwardRef } = await import("react");
  return {
    default: forwardRef(function ForceGraphMock(_props: Record<string, unknown>, _ref) {
      return <div data-testid="wiki-graph-2d-stub" />;
    }),
  };
});

vi.mock("@/components/wiki/WikiGraph3D", () => ({
  WikiGraph3D: () => <div data-testid="wiki-graph-3d-stub" />,
}));

const PAYLOAD = {
  ok: true,
  nodes: [
    { id: "user", kind: "entity", title: "User" },
    { id: "log", kind: "meta", title: "Log" },
  ],
  edges: [{ source: "user", target: "log", context: "wrote" }],
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

function Wrapper({ children, client }: PropsWithChildren<{ client: QueryClient }>) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function renderGraph() {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify(PAYLOAD), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    ),
  );
  return render(
    <Wrapper
      client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}
    >
      <WikiGraph onNodeClick={() => {}} />
    </Wrapper>,
  );
}

beforeEach(() => {
  reportWebglLost(false);
  window.localStorage.removeItem(GRAPH_DIMENSION_KEY);
  setGraphDimension("3d");
  resetWebglProbe();
});

afterEach(() => {
  cleanup();
  reportWebglLost(false);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  resetWebglProbe();
  window.localStorage.removeItem(GRAPH_DIMENSION_KEY);
});

describe("a graphics context lost for good", () => {
  it("takes the map flat instead of leaving a dead canvas on screen", async () => {
    pretendWebglWorks();
    renderGraph();
    // A generous wait: the lazy 3D module resolves on a microtask, and a busy
    // machine running the whole suite at once can be a beat behind.
    await screen.findByTestId("wiki-graph-3d-stub", {}, { timeout: 5_000 });

    act(() => reportWebglLost(true));

    await waitFor(
      () => {
        expect(screen.queryByTestId("wiki-graph-3d-stub")).toBeNull();
      },
      { timeout: 5_000 },
    );
    expect(screen.getByTestId("wiki-graph-2d-stub")).toBeDefined();
  });

  it("says a context was lost, not that the machine cannot do 3D", async () => {
    pretendWebglWorks();
    renderGraph();
    // A generous wait: the lazy 3D module resolves on a microtask, and a busy
    // machine running the whole suite at once can be a beat behind.
    await screen.findByTestId("wiki-graph-3d-stub", {}, { timeout: 5_000 });

    act(() => reportWebglLost(true));

    const segment = (await screen.findByTestId(
      "graph-dimension-3d",
    )) as HTMLButtonElement;
    await waitFor(() => expect(segment.disabled).toBe(true), { timeout: 5_000 });
    expect(segment.getAttribute("title")).toContain("lost its graphics context");
    expect(segment.getAttribute("title")).not.toContain("cannot render 3D");
    expect(
      screen.getByTestId("graph-dimension-toggle").getAttribute("data-webgl-state"),
    ).toBe("lost");
  });

  it("keeps the stored preference, so a reload offers the spatial map again", async () => {
    pretendWebglWorks();
    renderGraph();
    // A generous wait: the lazy 3D module resolves on a microtask, and a busy
    // machine running the whole suite at once can be a beat behind.
    await screen.findByTestId("wiki-graph-3d-stub", {}, { timeout: 5_000 });

    act(() => reportWebglLost(true));
    await waitFor(
      () => {
        expect(screen.queryByTestId("wiki-graph-3d-stub")).toBeNull();
      },
      { timeout: 5_000 },
    );

    expect(window.localStorage.getItem(GRAPH_DIMENSION_KEY)).toBe("3d");
  });
});
