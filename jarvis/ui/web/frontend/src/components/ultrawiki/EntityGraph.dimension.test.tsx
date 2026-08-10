/**
 * The 2D/3D switch on the UltraWiki topic map.
 *
 * Same contract as the vault Memory Map's, and deliberately the same
 * preference: flipping the switch here flips it there too, because one control
 * labelled "3D" that only applies to the screen you happened to be on is two
 * settings wearing one name. The chrome around the map — mention floor,
 * legend, expand — has to survive the swap, since none of it is a property of
 * the projection.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { EntityGraph } from "@/components/ultrawiki/EntityGraph";
import {
  GRAPH_DIMENSION_KEY,
  resetWebglProbe,
  setGraphDimension,
} from "@/lib/graphDimension";

class ResizeObserverPolyfill {
  constructor(_callback: ResizeObserverCallback) {}
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver =
    ResizeObserverPolyfill as unknown as typeof ResizeObserver;
}

vi.mock("react-force-graph-2d", async () => {
  const { forwardRef } = await import("react");
  return {
    default: forwardRef(function ForceGraphMock(
      _props: Record<string, unknown>,
      _ref,
    ) {
      return <div data-testid="explore-graph-2d-stub" />;
    }),
  };
});

// three.js wants a WebGL context jsdom cannot give; which renderer mounts is
// the whole question here, so a stand-in is the right amount of 3D.
vi.mock("@/components/ultrawiki/EntityGraph3D", () => ({
  EntityGraph3D: () => <div data-testid="explore-graph-3d-stub" />,
}));

const PAYLOAD = {
  ok: true,
  nodes: [
    {
      key: "bora bora",
      label: "Bora Bora",
      mentions: 20,
      first_seen: "2026-06-30T10:00:00Z",
      last_seen: "2026-07-08T10:00:00Z",
    },
    {
      key: "tahiti",
      label: "Tahiti",
      mentions: 4,
      first_seen: "2026-07-01T10:00:00Z",
      last_seen: "2026-07-13T10:00:00Z",
    },
  ],
  edges: [{ source: "bora bora", target: "tahiti", weight: 10 }],
  min_mentions: 2,
  total_entities: 947,
  corpus: { sources: 1, items: 40, distilled: 30 },
  reason: "ok",
};

function installFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, json: async () => PAYLOAD })),
  );
}

/** Make the WebGL probe answer yes, the way a real GPU-backed window does. */
function pretendWebglWorks(): void {
  vi.stubGlobal("WebGLRenderingContext", class {});
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    getExtension: () => null,
  } as unknown as RenderingContext);
  resetWebglProbe();
}

function renderGraph() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <EntityGraph
        minMentions={2}
        onMinMentionsChange={() => {}}
        selectedKey={null}
        onSelect={() => {}}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
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

describe("EntityGraph 2D/3D switch", () => {
  it("draws the flat map by default", async () => {
    pretendWebglWorks();
    installFetch();
    renderGraph();

    expect(await screen.findByTestId("explore-graph-2d-stub")).toBeDefined();
    expect(screen.queryByTestId("explore-graph-3d-stub")).toBeNull();
    expect(
      screen.getByTestId("explore-entity-graph").getAttribute("data-dimension"),
    ).toBe("2d");
  });

  it("swaps to the 3D scene and keeps the map's own controls", async () => {
    pretendWebglWorks();
    installFetch();
    renderGraph();
    await screen.findByTestId("explore-graph-2d-stub");

    fireEvent.click(screen.getByTestId("graph-dimension-3d"));

    expect(await screen.findByTestId("explore-graph-3d-stub")).toBeDefined();
    await waitFor(() => {
      expect(screen.queryByTestId("explore-graph-2d-stub")).toBeNull();
    });
    expect(
      screen.getByTestId("explore-entity-graph").getAttribute("data-dimension"),
    ).toBe("3d");
    // The mention floor and the expand button describe the data and the panel,
    // not the projection — losing them on the way into 3D would be a downgrade.
    expect(screen.getByTestId("explore-graph-floor")).toBeDefined();
    expect(screen.getByTestId("explore-graph-expand-toggle")).toBeDefined();
    expect(screen.getByTestId("explore-graph-shown")).toBeDefined();
  });

  it("stays flat without a WebGL context, whatever the stored preference says", async () => {
    setGraphDimension("3d");
    installFetch();
    renderGraph();

    expect(await screen.findByTestId("explore-graph-2d-stub")).toBeDefined();
    expect(screen.queryByTestId("explore-graph-3d-stub")).toBeNull();
    expect(
      (screen.getByTestId("graph-dimension-3d") as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});
