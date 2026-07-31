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

/** A canvas that remembers nothing but the circles it was asked to draw. */
function fakeCanvas(): { ctx: CanvasRenderingContext2D; radii: number[] } {
  const radii: number[] = [];
  const ctx = {
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 0,
    font: "",
    textAlign: "left",
    textBaseline: "top",
    beginPath: () => {},
    arc: (_x: number, _y: number, radius: number) => {
      radii.push(radius);
    },
    fill: () => {},
    stroke: () => {},
    fillText: () => {},
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, radii };
}

type NodePaint = (
  node: Record<string, unknown>,
  ctx: CanvasRenderingContext2D,
  scale: number,
) => void;
type PointerPaint = (
  node: Record<string, unknown>,
  colour: string,
  ctx: CanvasRenderingContext2D,
  scale: number,
) => void;

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

/** One entity page, as the detail endpoint answers it. */
const DETAIL = {
  ok: true,
  entity: {
    key: "bora bora",
    label: "Bora Bora",
    mentions: 20,
    first_seen: "2026-06-30T10:00:00Z",
    last_seen: "2026-07-08T10:00:00Z",
    neighbors: [{ key: "tahiti", label: "Tahiti", shared: 7 }],
  },
  moments: [],
  total: 0,
  corpus: { sources: 1, items: 40, distilled: 30 },
};

function installFetch(body: unknown = PAYLOAD) {
  const fetchMock = vi.fn(async (url: unknown) => ({
    ok: true,
    status: 200,
    json: async () =>
      String(url).includes("/entities/") ? DETAIL : (body as unknown),
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

it("takes a click anywhere on the dot, not just in its core", async () => {
  // The regression this pins: the renderer derives its hit area from
  // `nodeVal` (radius = sqrt(val) * nodeRelSize), never from the circle
  // nodeCanvasObject actually draws. Left to itself the fattest topic
  // answered only within sqrt(12) of its 12 units — 8 % of the target the
  // user aims at — so most clicks landed on nothing. The hit area has to be
  // painted by hand, and it may never be smaller than what is on screen.
  installFetch();
  renderGraph();

  await waitFor(() => expect(forceGraphProps.length).toBeGreaterThan(0));
  const props = forceGraphProps.at(-1) as Record<string, unknown>;
  const graphData = props.graphData as {
    nodes: Array<{ id: string; radius: number; colour: string }>;
  };
  // The biggest node is where the mismatch hurts most, so measure that one.
  const biggest = graphData.nodes.reduce((peak, node) =>
    node.radius > peak.radius ? node : peak,
  );
  const node = { ...biggest, x: 0, y: 0 } as unknown as Record<string, unknown>;

  const drawn = fakeCanvas();
  (props.nodeCanvasObject as NodePaint)(node, drawn.ctx, 1);

  const target = fakeCanvas();
  const paintHitArea = props.nodePointerAreaPaint as PointerPaint | undefined;
  expect(typeof paintHitArea).toBe("function");
  paintHitArea!(node, "#010203", target.ctx, 1);

  expect(Math.max(...target.radii)).toBeGreaterThanOrEqual(
    Math.max(...drawn.radii),
  );
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

it("says what you picked while the map covers the detail view", async () => {
  // Fullscreen lies on top of the panel that normally answers a click, so a
  // hit had nothing to show for itself but a hairline ring around the dot —
  // indistinguishable from a click that missed. The map has to answer for
  // itself while it owns the screen.
  installFetch();
  const onSelect = vi.fn();
  const view = renderGraph({ selectedKey: "bora bora", onSelect });

  expect(screen.queryByTestId("explore-graph-selection")).toBeNull();

  fireEvent.click(screen.getByTestId("explore-graph-expand-toggle"));

  const card = await screen.findByTestId("explore-graph-selection");
  expect(card.textContent).toContain("Bora Bora");
  await waitFor(() => expect(card.textContent).toContain("20"));

  // Neighbours are the way through the map, so they have to stay reachable
  // from the fullscreen card too.
  fireEvent.click(await screen.findByTestId("explore-graph-neighbor-tahiti"));
  expect(onSelect).toHaveBeenCalledWith("tahiti");

  view.unmount();
});

it("keeps the fullscreen card out of the way when the panel is visible", async () => {
  installFetch();
  renderGraph({ selectedKey: "bora bora" });

  await waitFor(() => expect(forceGraphProps.length).toBeGreaterThan(0));
  // Not expanded: ExplorePanel's own detail view is on screen, so a second
  // copy of the same facts would just be noise.
  expect(screen.queryByTestId("explore-graph-selection")).toBeNull();
});

it("sizes the canvas from its own box, not from the window", async () => {
  // No width prop and the renderer falls back to window.innerWidth: a canvas
  // wider than the column it lives in, drawing the network off-centre. The
  // ResizeObserver callback is a frame away at best — and a tab the OS treats
  // as occluded delivers no frames at all, so the fallback could stand for as
  // long as the window stayed hidden. The first measurement is synchronous.
  installFetch();
  const width = 640;
  const height = 480;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width,
    height,
    top: 0,
    left: 0,
    right: width,
    bottom: height,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });

  renderGraph();

  await waitFor(() => expect(forceGraphProps.length).toBeGreaterThan(0));
  const props = forceGraphProps.at(-1) as Record<string, unknown>;
  expect(props.width).toBe(width);
  expect(props.height).toBe(height);
});

it("gives the map the whole box and floats its chrome on top", async () => {
  // The map used to be a 224 px letterbox: a title bar, a hint line and a
  // control row each took a slice of the little height it had, and a force
  // layout with no area is a smear. Every piece of chrome is now positioned
  // OVER the canvas, so none of it can shrink the map again.
  installFetch();
  renderGraph();

  const stage = screen.getByTestId("explore-entity-graph");
  expect(stage.className).toContain("h-full");
  // The canvas paints at a fixed pixel width; out of flow it can set no
  // floor for the column it lives in.
  expect(screen.getByTestId("explore-graph-canvas").className).toContain(
    "absolute",
  );
  expect(stage.className).toContain("overflow-hidden");
  expect(stage.className).toContain("min-w-0");

  for (const id of ["explore-graph-floor", "explore-graph-shown"]) {
    expect(screen.getByTestId(id).closest(".absolute")).not.toBeNull();
  }
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
