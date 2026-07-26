/**
 * EntityGraph tests — the map of which topics appear together.
 *
 * The canvas library is stubbed (jsdom has no <canvas>), so what is pinned
 * here is the contract around it: the mention floor reaches the backend, the
 * encoding hands the renderer sized and tinted nodes, a click reports the
 * topic, and a floor that hides everything says so instead of showing an
 * empty rectangle the user would read as "nothing is known".
 */
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { EntityGraph } from "@/components/ultrawiki/EntityGraph";

// jsdom ships no ResizeObserver; the graph observes its container to size the
// canvas. Same stand-in the AgenticGrid tests use.
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

function installFetch(body: unknown = PAYLOAD) {
  const fetchMock = vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => body,
  }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderGraph(props: Partial<Parameters<typeof EntityGraph>[0]> = {}) {
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
        {...props}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  forceGraphProps.length = 0;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("asks the backend for the current mention floor", async () => {
  const fetchMock = installFetch();
  renderGraph({ minMentions: 3 });

  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  expect(fetchMock.mock.calls.map(String).join("|")).toContain("min_mentions=3");
});

it("sizes nodes by how often a topic comes up", async () => {
  installFetch();
  renderGraph();

  await waitFor(() => expect(forceGraphProps.length).toBeGreaterThan(0));
  const data = forceGraphProps.at(-1)?.graphData as {
    nodes: Array<{ id: string; radius: number; colour: string }>;
  };
  const bora = data.nodes.find((node) => node.id === "bora bora");
  const tahiti = data.nodes.find((node) => node.id === "tahiti");
  expect(bora!.radius).toBeGreaterThan(tahiti!.radius);
  expect(bora!.colour).toMatch(/^#[0-9a-f]{6}$/);
});

it("reports the topic behind a clicked node", async () => {
  installFetch();
  const onSelect = vi.fn();
  renderGraph({ onSelect });

  await waitFor(() => expect(forceGraphProps.length).toBeGreaterThan(0));
  const handler = forceGraphProps.at(-1)?.onNodeClick as (node: unknown) => void;
  handler({ id: "tahiti" });

  expect(onSelect).toHaveBeenCalledWith("tahiti");
});

it("moves the floor when the slider moves", async () => {
  installFetch();
  const onMinMentionsChange = vi.fn();
  renderGraph({ onMinMentionsChange });

  // A range control emits `input` while it is dragged, which is the event
  // React's onChange is bound to; `change` alone only fires on release.
  fireEvent.input(await screen.findByTestId("explore-graph-floor"), {
    target: { value: "0" },
  });

  expect(onMinMentionsChange).toHaveBeenCalledWith(1);
});

it("says the floor is hiding everything instead of drawing a blank box", async () => {
  installFetch({ ...PAYLOAD, nodes: [], edges: [] });
  renderGraph();

  expect(await screen.findByTestId("explore-graph-empty")).toBeTruthy();
});

it("shows how much of the corpus is on screen", async () => {
  installFetch();
  renderGraph();

  const caption = await screen.findByTestId("explore-graph-shown");
  // The caption exists before the data does, so wait for the loaded numbers
  // rather than for the element.
  await waitFor(() => expect(caption.textContent).toContain("947"));
  expect(caption.textContent).toContain("2");
});

it("expands across the app and restores with Escape", async () => {
  installFetch();
  renderGraph();

  const graph = screen.getByTestId("explore-entity-graph");
  const toggle = screen.getByTestId("explore-graph-expand-toggle");
  expect(graph.getAttribute("data-expanded")).toBe("false");
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(toggle.getAttribute("aria-controls")).toBe("explore-entity-graph");

  fireEvent.click(toggle);

  expect(graph.getAttribute("data-expanded")).toBe("true");
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  expect(graph.className).toContain("fixed");

  fireEvent.keyDown(window, { key: "Escape" });

  expect(graph.getAttribute("data-expanded")).toBe("false");
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
});
