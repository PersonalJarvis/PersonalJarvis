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

vi.mock("react-force-graph-2d", () => {
  return {
    default: () => null,
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
              { id: "harald", kind: "entity", title: "Harald" },
              { id: "ruben", kind: "entity", title: "Ruben" },
              { id: "pixel-art-editor", kind: "project", title: "Pixel Art Editor" },
            ],
            edges: [
              { source: "ruben", target: "harald", context: "Father" },
              { source: "ruben", target: "pixel-art-editor", context: "Working on" },
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
    expect(ids).toEqual(["harald", "ruben", "pixel-art-editor"]);
  });

  it("invokes onNodeClick(slug) when a node is clicked", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            ok: true,
            nodes: [{ id: "harald", kind: "entity", title: "Harald" }],
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
    fireEvent.click(screen.getByRole("button", { name: /Harald/i }));
    expect(onNodeClick).toHaveBeenCalledWith("harald");
  });

  it("renders highlighted node with 1.5x radius", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            ok: true,
            nodes: [
              { id: "harald", kind: "entity", title: "Harald" },
              { id: "ruben", kind: "entity", title: "Ruben" },
            ],
            // ruben → harald gives harald 1 backlink (radius = 10), zero for ruben (radius = 8)
            edges: [{ source: "ruben", target: "harald", context: "Father" }],
            broken: [],
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      ),
    );
    const client = makeClient();
    render(
      <Wrapper client={client}>
        <WikiGraph onNodeClick={() => {}} highlightSlug="harald" />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getAllByTestId("wiki-graph-node")).toHaveLength(2);
    });
    const haraldNode = screen
      .getAllByTestId("wiki-graph-node")
      .find((el) => el.getAttribute("data-node-id") === "harald");
    const rubenNode = screen
      .getAllByTestId("wiki-graph-node")
      .find((el) => el.getAttribute("data-node-id") === "ruben");
    expect(haraldNode?.getAttribute("data-node-active")).toBe("true");
    expect(rubenNode?.getAttribute("data-node-active")).toBe("false");
    const haraldRadius = Number(haraldNode?.getAttribute("data-node-radius"));
    const rubenRadius = Number(rubenNode?.getAttribute("data-node-radius"));
    // harald: base 10 * 1.5 = 15. ruben: base 8.
    expect(haraldRadius).toBe(15);
    expect(rubenRadius).toBe(8);
  });
});
