// Pure helpers for the Wiki Memory-Map graph view.
// Keeps colour palette + simple data transforms out of the React component so
// the visual contract (mockup) lives in exactly one place and the helpers are
// unit-testable without spinning up a renderer.

/**
 * Backend node shape returned by `GET /api/wiki/graph`.
 */
export interface WikiGraphNode {
  id: string;
  kind: string;
  title: string;
}

/**
 * Backend edge shape returned by `GET /api/wiki/graph`.
 */
export interface WikiGraphEdge {
  source: string;
  target: string;
  context: string;
}

/**
 * Backend broken-link shape — edge target that does not resolve to a page.
 */
export interface WikiGraphBrokenLink {
  source: string;
  target: string;
}

/**
 * Full payload returned by `GET /api/wiki/graph`.
 */
export interface WikiGraphPayload {
  ok: boolean;
  nodes: WikiGraphNode[];
  edges: WikiGraphEdge[];
  broken: WikiGraphBrokenLink[];
  /**
   * The page the memory map turns around — the user's own entity page, as
   * the route names it (null when the vault has no such page yet). Absent on
   * an older backend, which is why the type allows it to be missing.
   */
  hub?: string | null;
}

/**
 * Node shape after enrichment for react-force-graph-2d.
 * The library mutates positional fields (`x`, `y`, `vx`, `vy`) at runtime.
 */
export interface RenderNode extends WikiGraphNode {
  backlinkCount: number;
  radius: number;
  colour: string;
  x?: number;
  y?: number;
  z?: number;
  vx?: number;
  vy?: number;
  vz?: number;
  /** d3-force pin — the hub is nailed to the origin so the map turns around it. */
  fx?: number;
  fy?: number;
  fz?: number;
}

/**
 * Edge shape after enrichment for react-force-graph-2d.
 * `broken=true` instructs the renderer to draw a dashed rose-tinted line.
 */
export interface RenderEdge {
  source: string;
  target: string;
  context: string;
  broken: boolean;
}

/**
 * Node colour palette — binding visual contract from the mockup.
 *
 *   entity  → accent blue
 *   concept → purple
 *   project → amber
 *   session → green
 *
 * Unknown kinds fall through to `DEFAULT_NODE_COLOUR`.
 */
export const NODE_COLOUR: Record<string, string> = {
  entity: "#6aa9ff",
  concept: "#b48cf2",
  project: "#ffb84d",
  session: "#5bd4a4",
};

export const DEFAULT_NODE_COLOUR = "#8b95a7";

/**
 * Colour used to draw broken (orphan) edges. Matches the `--rose` token in the
 * mockup so the user can spot dangling wikilinks at a glance.
 */
export const BROKEN_EDGE_COLOUR = "#f47fa4";

/**
 * Resolve a node `kind` to its visual colour.
 * Unknown kinds get the neutral grey fallback — never throws.
 */
export function colourForKind(kind: string): string {
  return NODE_COLOUR[kind] ?? DEFAULT_NODE_COLOUR;
}

/** Pixel canvas size of the Memory-Map. */
export interface CanvasSize {
  w: number;
  h: number;
}

/**
 * True only when a new canvas measurement differs from the previous one by at
 * least `threshold` pixels on either axis.
 *
 * The graph canvas is driven by a ResizeObserver. Real layouts emit a stream of
 * sub-pixel measurements (scrollbar flicker, DPI rounding); accepting every one
 * churns React state for no visible benefit and — together with the old
 * remount-on-size-change — used to restart the whole force simulation, which is
 * what made the network flail/oscillate when the window jittered. A small
 * threshold absorbs that noise while still reacting to genuine resizes.
 */
export function sizeChanged(prev: CanvasSize, next: CanvasSize, threshold = 2): boolean {
  const dw = Math.abs(next.w - prev.w);
  const dh = Math.abs(next.h - prev.h);
  if (dw === 0 && dh === 0) return false;
  return dw >= threshold || dh >= threshold;
}

/** A graph-space axis-aligned bounding box, matching `getGraphBbox()`. */
export interface GraphBbox {
  x: [number, number];
  y: [number, number];
}

/** The camera centre in graph coordinates (what `centerAt()` reports). */
export interface GraphCenter {
  x: number;
  y: number;
}

/**
 * Clamp the Memory-Map camera centre so the graph can never be panned entirely
 * out of view.
 *
 * Why this exists: react-force-graph allows unbounded background panning, and a
 * pure pan does NOT reheat the simulation — so the `onEngineStop` re-fit never
 * fires to rescue a graph the user has dragged off-screen. The result was the
 * reported bug: drag the network toward an edge and it vanishes ("the right
 * wall disappears"), with no way back except the Zentrieren button.
 *
 * The guarantee: after a pan, at least `minVisibleFraction` of each viewport
 * dimension keeps overlapping the graph's bounding box — but never more overlap
 * than the graph actually spans (a graph smaller than the viewport stays FULLY
 * visible instead of being stranded the moment its centre leaves the screen).
 *
 * Pure + framework-free so it is unit-testable without a canvas. The component
 * feeds it `centerAt()` / `zoom()` / `getGraphBbox()` from `onZoomEnd` and only
 * issues a corrective `centerAt()` when the returned centre actually moved.
 *
 * @param center  current camera centre, graph coordinates
 * @param zoom    current zoom factor (screen px per graph unit)
 * @param bbox    graph bounding box, graph coordinates
 * @param view    viewport size in CSS pixels
 * @param minVisibleFraction  fraction of each viewport axis kept over the graph
 * @returns the clamped centre (identical values when already in bounds)
 */
export function clampCenterToView(
  center: GraphCenter,
  zoom: number,
  bbox: GraphBbox,
  view: CanvasSize,
  minVisibleFraction = 0.25,
): GraphCenter {
  // No usable zoom yet (canvas not laid out) → never touch the centre.
  if (!Number.isFinite(zoom) || zoom <= 0) return center;

  const halfW = view.w / (2 * zoom);
  const halfH = view.h / (2 * zoom);
  const bboxW = bbox.x[1] - bbox.x[0];
  const bboxH = bbox.y[1] - bbox.y[0];

  // How much of the graph must stay on screen, in graph units. Capped at the
  // graph's own span so a small graph is kept wholly visible, not half-off.
  const keepX = Math.min((view.w * minVisibleFraction) / zoom, bboxW);
  const keepY = Math.min((view.h * minVisibleFraction) / zoom, bboxH);

  const clampAxis = (c: number, lo: number, hi: number): number =>
    lo > hi ? (lo + hi) / 2 : Math.min(hi, Math.max(lo, c));

  return {
    x: clampAxis(center.x, bbox.x[0] - halfW + keepX, bbox.x[1] + halfW - keepX),
    y: clampAxis(center.y, bbox.y[0] - halfH + keepY, bbox.y[1] + halfH - keepY),
  };
}

/**
 * Compute a node radius in canvas pixels from its inbound link count.
 *
 * The clamp window (8..24) matches the §4.2 spec; the linear slope keeps the
 * hub nodes visually prominent without letting a single super-connector swamp
 * the canvas.
 */
export function nodeRadius(backlinkCount: number): number {
  return Math.max(8, Math.min(24, 8 + backlinkCount * 2));
}

/**
 * The id at one end of a link.
 *
 * Links arrive from the API as plain id strings and are REPLACED in place by
 * the renderer with references to the node objects themselves once the
 * simulation ingests them. Any code that reads an endpoint therefore has to
 * cope with both shapes, and every renderer needs it — hence one definition.
 */
export function endpointId(endpoint: unknown): string {
  if (typeof endpoint === "string" || typeof endpoint === "number") {
    return String(endpoint);
  }
  if (endpoint && typeof endpoint === "object" && "id" in endpoint) {
    const id = (endpoint as { id?: unknown }).id;
    return typeof id === "string" || typeof id === "number" ? String(id) : "";
  }
  return "";
}

/**
 * How big a node draws, relative to its peers — the shared size encoding for
 * BOTH the flat map and the 3D one.
 *
 * The range is 1.0 (a leaf) to 2.6 (a hub with eight or more backlinks), and
 * the selected node is half again as large so it can be found without reading
 * a single label. It lives here rather than in either renderer so a hub does
 * not silently change meaning when the user flips the map into space.
 */
export function nodeSizeScore(backlinkCount: number, isActive: boolean): number {
  const score = 1.0 + Math.min(Math.max(backlinkCount, 0), 8) * 0.2;
  return isActive ? score * 1.5 : score;
}

/**
 * Count how often each `target` appears as the destination of a wikilink.
 * Returns a Map keyed by node `id`. Edges to unknown nodes are ignored
 * (those are surfaced via the `broken` channel instead).
 */
export function countBacklinks(
  nodes: readonly WikiGraphNode[],
  edges: readonly WikiGraphEdge[],
): Map<string, number> {
  const known = new Set(nodes.map((n) => n.id));
  const counts = new Map<string, number>();
  for (const n of nodes) counts.set(n.id, 0);
  for (const e of edges) {
    if (known.has(e.target)) counts.set(e.target, (counts.get(e.target) ?? 0) + 1);
  }
  return counts;
}

/**
 * Build the render-ready nodes/links arrays that react-force-graph-2d expects.
 *
 * Pure function — no React imports, no DOM access. The component just passes
 * its API response through this and hands the result to the library.
 */
export function toGraphData(payload: WikiGraphPayload): {
  nodes: RenderNode[];
  links: RenderEdge[];
} {
  const backlinks = countBacklinks(payload.nodes, payload.edges);
  const nodes: RenderNode[] = payload.nodes.map((n) => {
    const count = backlinks.get(n.id) ?? 0;
    return {
      ...n,
      backlinkCount: count,
      radius: nodeRadius(count),
      colour: colourForKind(n.kind),
    };
  });
  const links: RenderEdge[] = [
    ...payload.edges.map((e) => ({
      source: e.source,
      target: e.target,
      context: e.context,
      broken: false,
    })),
    ...payload.broken.map((e) => ({
      source: e.source,
      target: e.target,
      context: "",
      broken: true,
    })),
  ];

  // react-force-graph-2d throws "node not found" (and keeps throwing as the d3
  // simulation ticks) if any link references an id that is not in `nodes`.
  // Broken/dangling wikilinks point at pages that don't exist in the vault, so
  // we materialise a lightweight phantom node for every missing endpoint. This
  // is what lets the rose dashed "broken edge" actually render instead of
  // crashing the whole Memory-Map on mount.
  const known = new Set(nodes.map((n) => n.id));
  const phantomIds = new Set<string>();
  for (const link of links) {
    for (const endpoint of [link.source, link.target]) {
      if (typeof endpoint === "string" && !known.has(endpoint)) {
        phantomIds.add(endpoint);
      }
    }
  }
  for (const id of phantomIds) {
    nodes.push({
      id,
      kind: "broken",
      title: id,
      backlinkCount: 0,
      radius: nodeRadius(0),
      colour: BROKEN_EDGE_COLOUR,
    });
  }

  return { nodes, links };
}


/**
 * Tooltip text for one node, one edge, and the HTML escape both need.
 *
 * These live here, beside the data they describe, rather than in
 * `components/wiki/WikiGraph` where they were written. The deck's wiki card
 * needs exactly these three strings and nothing else from that component — and
 * importing them from there pulled `react-force-graph-2d` with its whole d3
 * stack into the STARTUP chunk, roughly 150 KB parsed before the first paint,
 * for three pure string functions. This module imports nothing, so the graph
 * library now travels only in the chunk that actually draws a graph.
 */
export function escapeTooltipText(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function nodeDetails(node: RenderNode, t: (key: string) => string): string {
  const backlinks = node.backlinkCount ?? 0;
  const suffix = t(
    backlinks === 1 ? "wiki_graph.backlink_one" : "wiki_graph.backlink_many",
  );
  return t("wiki_graph.node_details")
    .replace("{0}", node.title)
    .replace("{1}", node.kind)
    .replace("{2}", String(backlinks))
    .replace("{3}", suffix);
}

export function edgeDetails(
  edge: RenderEdge,
  titles: ReadonlyMap<string, string>,
): string {
  const sourceId = endpointId(edge.source);
  const targetId = endpointId(edge.target);
  const source = titles.get(sourceId) ?? sourceId;
  const target = titles.get(targetId) ?? targetId;
  const relationship = edge.context.trim();
  return `${source} → ${target}${relationship ? ` · ${relationship}` : ""}`;
}
