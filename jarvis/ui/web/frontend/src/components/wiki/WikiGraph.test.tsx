/**
 * Tests for the WikiGraph force-graph view.
 *
 * `react-force-graph-2d` is canvas-based and jsdom does not implement
 * `<canvas>`, so we mock the library with a thin DOM-only stand-in that
 * exposes node click + nodeVal in a deterministic way. The component itself
 * also keeps a hidden `<ul>` mirror of the nodes so behaviour (radius scaling
 * on `highlightSlug`, click forwarding, empty/error states) is observable
 * without touching the canvas.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PropsWithChildren } from "react";

import { WikiGraph } from "@/components/wiki/WikiGraph";

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
      return null;
    }),
  };
});

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
}

function Wrapper({ children, client }: PropsWithChildren<{ client: QueryClient }>) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  forceGraphProps.length = 0;
});

describe("WikiGraph", () => {
  it("renders empty state when API returns zero nodes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ ok: true, nodes: [], edges: [], broken: [] }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      ),
    );
    const client = makeClient();
    render(
      <Wrapper client={client}>
        <WikiGraph onNodeClick={() => {}} />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("wiki-graph-empty")).toBeDefined();
    });
    expect(screen.getByTestId("wiki-graph-empty").textContent).toContain(
      "Your memory graph is still empty",
    );
  });

  it("renders 3 nodes when API returns 3 nodes", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            ok: true,
            nodes: [
              { id: "sam", kind: "entity", title: "Sam" },
              { id: "alex", kind: "entity", title: "Alex" },
              { id: "pixel-art-editor", kind: "project", title: "Pixel Art Editor" },
            ],
            edges: [
              { source: "alex", target: "sam", context: "Father" },
              { source: "alex", target: "pixel-art-editor", context: "Working on" },
            ],
            broken: [],
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      ),
    );
    const client = makeClient();
    render(
      <Wrapper client={client}>
        <WikiGraph onNodeClick={() => {}} />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getAllByTestId("wiki-graph-node")).toHaveLength(3);
    });
    const ids = screen
      .getAllByTestId("wiki-graph-node")
      .map((el) => el.getAttribute("data-node-id"));
    expect(ids).toEqual(["sam", "alex", "pixel-art-editor"]);
    const edges = screen.getAllByTestId("wiki-graph-edge");
    expect(edges).toHaveLength(2);
    expect(edges[0].textContent).toContain("Alex → Sam · Father");
    expect(edges[1].textContent).toContain(
      "Alex → Pixel Art Editor · Working on",
    );
  });

  it("provides directional arrows and safe node and relationship details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            ok: true,
            nodes: [
              { id: "sam", kind: "entity", title: "Sam" },
              { id: "alex", kind: "entity", title: "Alex" },
            ],
            edges: [
              {
                source: "alex",
                target: "sam",
                context: "Father <trusted>",
              },
            ],
            broken: [],
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      ),
    );
    const client = makeClient();
    render(
      <Wrapper client={client}>
        <WikiGraph onNodeClick={() => {}} />
      </Wrapper>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("wiki-graph-edge")).toBeDefined();
    });
    const props = forceGraphProps.at(-1)!;
    const graphData = props.graphData as {
      nodes: Array<Record<string, unknown>>;
      links: Array<Record<string, unknown>>;
    };
    const nodeLabel = props.nodeLabel as (node: Record<string, unknown>) => string;
    const linkLabel = props.linkLabel as (link: Record<string, unknown>) => string;

    expect(props.linkDirectionalArrowLength).toBe(4);
    expect(props.linkDirectionalArrowRelPos).toBe(0.82);
    expect(nodeLabel(graphData.nodes[0])).toContain("Sam (entity) · 1 backlink");
    expect(linkLabel(graphData.links[0])).toContain(
      "Alex → Sam · Father &lt;trusted&gt;",
    );
  });

  it("invokes onNodeClick(slug) when a node is clicked", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            ok: true,
            nodes: [{ id: "sam", kind: "entity", title: "Sam" }],
            edges: [],
            broken: [],
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      ),
    );
    const onNodeClick = vi.fn();
    const client = makeClient();
    render(
      <Wrapper client={client}>
        <WikiGraph onNodeClick={onNodeClick} />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("wiki-graph-node")).toBeDefined();
    });
    fireEvent.click(screen.getByRole("button", { name: /Sam/i }));
    expect(onNodeClick).toHaveBeenCalledWith("sam");
  });

  it("renders highlighted node with 1.5x radius", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            ok: true,
            nodes: [
              { id: "sam", kind: "entity", title: "Sam" },
              { id: "alex", kind: "entity", title: "Alex" },
            ],
            // alex → sam gives sam 1 backlink (radius = 10), zero for alex (radius = 8)
            edges: [{ source: "alex", target: "sam", context: "Father" }],
            broken: [],
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      ),
    );
    const client = makeClient();
    render(
      <Wrapper client={client}>
        <WikiGraph onNodeClick={() => {}} highlightSlug="sam" />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getAllByTestId("wiki-graph-node")).toHaveLength(2);
    });
    const samNode = screen
      .getAllByTestId("wiki-graph-node")
      .find((el) => el.getAttribute("data-node-id") === "sam");
    const alexNode = screen
      .getAllByTestId("wiki-graph-node")
      .find((el) => el.getAttribute("data-node-id") === "alex");
    expect(samNode?.getAttribute("data-node-active")).toBe("true");
    expect(alexNode?.getAttribute("data-node-active")).toBe("false");
    const samRadius = Number(samNode?.getAttribute("data-node-radius"));
    const alexRadius = Number(alexNode?.getAttribute("data-node-radius"));
    // sam: base 10 * 1.5 = 15. alex: base 8.
    expect(samRadius).toBe(15);
    expect(alexRadius).toBe(8);
  });
});
