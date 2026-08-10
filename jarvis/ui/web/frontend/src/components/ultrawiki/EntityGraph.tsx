/**
 * The map of which topics appear together — the centre of Explore, not a
 * banner above it.
 *
 * A node is a topic, an edge means "these two came up in the same moment",
 * and the edge is thicker the more often that happened. Node size is mention
 * count, node brightness is recency — both computed in lib/entityGraph.ts so
 * the list and the map speak the same visual language.
 *
 * Two things make it readable, and both are structural rather than cosmetic:
 *
 * 1. AREA. A force layout spreads its nodes in two dimensions; squeezed into
 *    a 224 px letterbox it collapses into a smear no zoom can undo. The
 *    component now fills whatever box the panel gives it and every chrome
 *    element floats ON the map instead of stealing height from it.
 * 2. The mention FLOOR. On a real corpus 977 topics collapse to 313 at two
 *    mentions and 77 at five; drawing every one-off at once is a hairball,
 *    not a map. The slider makes the trade explicit and reversible instead of
 *    hiding data silently.
 *
 * The canvas is absolutely positioned inside its container on purpose: the
 * renderer paints at a FIXED pixel width, and a fixed-width child inside a
 * flex row sets that row's floor — which is how the whole section grew a
 * horizontal scrollbar it could never shrink back out of.
 */
import {
  Suspense,
  lazy,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useQuery } from "@tanstack/react-query";
import { Maximize2, Minimize2 } from "lucide-react";
import ForceGraph2D from "react-force-graph-2d";
import type {
  ForceGraphMethods,
  LinkObject,
  NodeObject,
} from "react-force-graph-2d";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { sizeChanged } from "@/lib/wikiGraph";
import { useGraphDimension } from "@/lib/graphDimension";
import { GraphDimensionToggle } from "@/components/wiki/GraphDimensionToggle";
import { corpusSpan, nodeRadius, recencyTint } from "@/lib/entityGraph";
import {
  fetchExploreEntity,
  fetchExploreGraph,
} from "@/lib/ultrawikiExploreApi";

// The WebGL renderer is the heaviest dependency in the app. Behind `lazy` it
// is fetched the first time somebody switches this map into space, and never
// otherwise — including for everyone who only ever reads the flat one.
const EntityGraph3D = lazy(() =>
  import("@/components/ultrawiki/EntityGraph3D").then((mod) => ({
    default: mod.EntityGraph3D,
  })),
);

export interface EntityGraphProps {
  minMentions: number;
  onMinMentionsChange: (value: number) => void;
  selectedKey: string | null;
  onSelect: (key: string) => void;
}

interface RenderNode {
  id: string;
  label: string;
  mentions: number;
  radius: number;
  colour: string;
  x?: number;
  y?: number;
}

interface RenderEdge {
  source: string;
  target: string;
  weight: number;
}

/** Floors the slider offers — 1 is always reachable, so nothing is hidden for good. */
const FLOORS = [1, 2, 3, 5, 8] as const;

/**
 * Slack around a node's hit area, in graph units.
 *
 * A topic is a small target on a crowded map and the pointer is never exactly
 * where the eye is. A rim slightly wider than the dot costs nothing — the
 * nodes it could steal from are further away than this — and it is the
 * difference between "I clicked it" and "I clicked next to it".
 */
const POINTER_PAD = 2;

/** The recency ramp, sampled for the legend so it cannot drift from the map. */
const LEGEND_SPAN = { start: 0, end: 100 };
const LEGEND_STOPS = [0, 25, 50, 75, 100].map((position) =>
  recencyTint(new Date(position).toISOString(), LEGEND_SPAN),
);

export function EntityGraph({
  minMentions,
  onMinMentionsChange,
  selectedKey,
  onSelect,
}: EntityGraphProps): JSX.Element {
  const t = useT();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<
    | ForceGraphMethods<NodeObject<RenderNode>, LinkObject<RenderNode, RenderEdge>>
    | undefined
  >(undefined);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [isExpanded, setIsExpanded] = useState(false);
  // Flat canvas or WebGL scene — the same preference the vault Memory Map
  // reads, already degraded back to flat where 3D cannot be rendered at all.
  const { dimension } = useGraphDimension();
  const isSpatial = dimension === "3d";

  const query = useQuery({
    queryKey: ["ultrawiki", "explore", "graph", minMentions],
    queryFn: () => fetchExploreGraph(minMentions),
    staleTime: 30_000,
  });

  useLayoutEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const apply = (w: number, h: number) => {
      const next = { w: Math.round(w), h: Math.round(h) };
      // Sub-pixel churn would restart the force simulation and make the
      // network flail; the shared threshold helper absorbs it.
      setSize((prev) => (sizeChanged(prev, next) ? next : prev));
    };
    // Measure BEFORE the observer's first callback. Without a width the
    // renderer falls back to `window.innerWidth` — a canvas wider than the
    // column it sits in, drawing the network off-centre and, until this
    // column got a `min-w-0`, dragging a horizontal scrollbar across the
    // whole app. The observer callback is one frame away at best, and a tab
    // the OS considers occluded produces no frames at all, so that fallback
    // could stand indefinitely. The box is already laid out here.
    const box = element.getBoundingClientRect();
    apply(box.width, box.height);
    const observer = new ResizeObserver((entries) => {
      const measured = entries[0]?.contentRect;
      if (!measured) return;
      apply(measured.width, measured.height);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!isExpanded) return;
    const restoreOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsExpanded(false);
    };
    window.addEventListener("keydown", restoreOnEscape);
    return () => window.removeEventListener("keydown", restoreOnEscape);
  }, [isExpanded]);

  const graphData = useMemo(() => {
    const nodes = query.data?.nodes ?? [];
    const span = corpusSpan(nodes);
    const max = nodes.reduce((peak, node) => Math.max(peak, node.mentions), 1);
    return {
      nodes: nodes.map<RenderNode>((node) => ({
        id: node.key,
        label: node.label,
        mentions: node.mentions,
        radius: nodeRadius(node.mentions, max),
        colour: recencyTint(node.last_seen, span),
      })),
      links: (query.data?.edges ?? []).map((edge) => ({
        source: edge.source,
        target: edge.target,
        weight: edge.weight,
      })),
    };
  }, [query.data]);

  const shown = graphData.nodes.length;
  const total = query.data?.total_entities ?? 0;

  // Fullscreen covers the panel that normally answers a click, so the map has
  // to answer for itself. The label comes from the node already in hand, which
  // is what makes the click feel instant; the counts and neighbours arrive
  // when the request does. Same query key as ExplorePanel's detail view, so
  // the two share one cached answer rather than each fetching their own.
  const selectedNode = useMemo(
    () => graphData.nodes.find((node) => node.id === selectedKey) ?? null,
    [graphData, selectedKey],
  );
  const detailQuery = useQuery({
    queryKey: ["ultrawiki", "explore", "entity", selectedKey],
    queryFn: () => fetchExploreEntity(selectedKey as string),
    enabled: isExpanded && selectedKey !== null,
    staleTime: 30_000,
  });
  const detail =
    detailQuery.data?.entity.key === selectedKey ? detailQuery.data.entity : null;
  // A topic can be selected from the list while the mention floor keeps it off
  // the map, so fall back to what the request brought rather than showing
  // nothing at all.
  const selection = selectedNode ?? detail;

  return (
    <div
      id="explore-entity-graph"
      data-testid="explore-entity-graph"
      data-expanded={isExpanded ? "true" : "false"}
      data-dimension={dimension}
      ref={containerRef}
      className={cn(
        "uw-stage relative h-full min-h-0 w-full min-w-0 overflow-hidden",
        isExpanded && "fixed inset-0 z-[100]",
      )}
    >
      {/* Behind the canvas, carrying no data — see .uw-stage in index.css. */}
      <div className="uw-stage-glow pointer-events-none absolute inset-0" aria-hidden />

      <div className="absolute inset-0" data-testid="explore-graph-canvas">
        {shown === 0 ? (
          <p
            data-testid="explore-graph-empty"
            className="flex h-full items-center justify-center px-6 text-center text-xs text-muted-foreground"
          >
            {t("ultrawiki.explore.graph_empty")}
          </p>
        ) : isSpatial ? (
          <Suspense
            fallback={
              <p
                data-testid="explore-graph-3d-loading"
                className="flex h-full items-center justify-center px-6 text-center text-xs text-muted-foreground"
              >
                {t("wiki_graph.loading_3d")}
              </p>
            }
          >
            <EntityGraph3D
              graphData={graphData}
              width={size.w}
              height={size.h}
              selectedKey={selectedKey}
              onSelect={onSelect}
            />
          </Suspense>
        ) : (
          <ForceGraph2D<RenderNode, RenderEdge>
            ref={graphRef}
            width={size.w || undefined}
            height={size.h || undefined}
            graphData={graphData}
            backgroundColor="transparent"
            cooldownTicks={80}
            nodeRelSize={1}
            nodeVal={(node: NodeObject<RenderNode>) => node.radius}
            nodeLabel={(node: NodeObject<RenderNode>) => node.label}
            linkColor={() => "rgba(255, 214, 10, 0.11)"}
            linkWidth={(link) =>
              Math.min(0.4 + (link.weight ?? 1) * 0.15, 2.4)
            }
            onNodeClick={(node) => {
              if (node?.id !== undefined) onSelect(String(node.id));
            }}
            // Which pixels belong to which node is decided on a second,
            // invisible canvas the renderer paints in per-node id colours and
            // then samples under the pointer. It does NOT know what
            // nodeCanvasObject drew there: left alone it stamps its own circle
            // of sqrt(nodeVal) * nodeRelSize, so a dot drawn at radius 12
            // answered only within 3.5 — barely 8 % of the area a user aims
            // at, and clicking the biggest topics did nothing at all. Painting
            // the hit area ourselves is what keeps the target and the drawing
            // the same shape.
            nodePointerAreaPaint={(node, colour, ctx) => {
              if (node.x === undefined || node.y === undefined) return;
              ctx.fillStyle = colour;
              ctx.beginPath();
              ctx.arc(node.x, node.y, node.radius + POINTER_PAD, 0, 2 * Math.PI);
              ctx.fill();
            }}
            nodeCanvasObjectMode={() => "after"}
            nodeCanvasObject={(node, ctx, scale) => {
              if (node.x === undefined || node.y === undefined) return;
              const selected = node.id === selectedKey;
              ctx.beginPath();
              ctx.arc(node.x, node.y, node.radius, 0, 2 * Math.PI);
              ctx.fillStyle = node.colour;
              ctx.fill();
              if (selected) {
                // Two rings: a white core edge that reads against a yellow
                // dot, and a wide soft halo that finds the selection again
                // once the map is zoomed out and the dot is three pixels.
                ctx.lineWidth = 1.5 / scale;
                ctx.strokeStyle = "#ffffff";
                ctx.stroke();
                ctx.beginPath();
                ctx.arc(
                  node.x,
                  node.y,
                  node.radius + 4 + 2 / scale,
                  0,
                  2 * Math.PI,
                );
                ctx.lineWidth = 1 / scale;
                ctx.strokeStyle = "rgba(255, 214, 10, 0.55)";
                ctx.stroke();
              }
              // Labels only once the user has zoomed in, and only for the
              // nodes that carry weight — a wall of overlapping text is how
              // graph views become unreadable.
              if (scale > 1.4 || selected || node.radius > 8) {
                ctx.font = `${Math.max(9 / scale, 2.4)}px ui-sans-serif, system-ui, sans-serif`;
                ctx.textAlign = "center";
                ctx.textBaseline = "top";
                ctx.fillStyle = selected
                  ? "rgba(255,255,255,0.95)"
                  : "rgba(255,255,255,0.62)";
                ctx.fillText(node.label, node.x, node.y + node.radius + 1.5);
              }
            }}
          />
        )}
      </div>

      {/* Edges of a force layout carry no meaning, so they get darkened to
          push the eye to the middle. Above the canvas, below the controls. */}
      <div
        className="uw-stage-vignette pointer-events-none absolute inset-0"
        aria-hidden
      />

      {/* --- Floating chrome. None of it takes height from the map. --- */}

      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-3 p-3">
        <div className="uw-stage-pill pointer-events-auto rounded-lg px-3 py-1.5">
          <p className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            {t("ultrawiki.explore.graph_title")}
          </p>
          <p
            data-testid="explore-graph-shown"
            className="text-[11px] tabular-nums text-foreground"
          >
            {t("ultrawiki.explore.graph_shown")
              .replace("{0}", String(shown))
              .replace("{1}", String(total))}
          </p>
        </div>

        <div className="pointer-events-auto flex shrink-0 items-center gap-2">
        <GraphDimensionToggle className="uw-stage-pill border-transparent bg-transparent" />
        <button
          type="button"
          data-testid="explore-graph-expand-toggle"
          onClick={() => setIsExpanded((expanded) => !expanded)}
          aria-controls="explore-entity-graph"
          aria-expanded={isExpanded}
          aria-label={t(
            isExpanded
              ? "wiki_graph.restore_view_title"
              : "wiki_graph.expand_view_title",
          )}
          title={t(
            isExpanded
              ? "wiki_graph.restore_view_title"
              : "wiki_graph.expand_view_title",
          )}
          className="uw-stage-pill pointer-events-auto inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
        >
          {isExpanded ? (
            <Minimize2 className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <Maximize2 className="h-3.5 w-3.5" aria-hidden />
          )}
        </button>
        </div>
      </div>

      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-wrap items-end justify-between gap-2 p-3">
        {/* The legend earns its place: without it the two encodings on screen
            (size, warmth) are decoration the reader has to guess at. */}
        <div className="uw-stage-pill pointer-events-auto rounded-lg px-3 py-2">
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1.5" aria-hidden>
              <span className="block h-1.5 w-1.5 rounded-full bg-muted-foreground" />
              <span className="block h-2.5 w-2.5 rounded-full bg-muted-foreground" />
              <span className="block h-3.5 w-3.5 rounded-full bg-muted-foreground" />
            </span>
            <span className="text-[10px] text-muted-foreground">
              {t("ultrawiki.explore.legend_size")}
            </span>
          </div>
          <div className="mt-1.5 flex items-center gap-2">
            <span
              className="block h-1.5 w-14 rounded-full"
              style={{
                backgroundImage: `linear-gradient(90deg, ${LEGEND_STOPS.join(", ")})`,
              }}
              aria-hidden
            />
            <span className="text-[10px] text-muted-foreground">
              {t("ultrawiki.explore.legend_recency")}
            </span>
          </div>
        </div>

        <div
          className="uw-stage-pill pointer-events-auto flex items-center gap-2.5 rounded-lg px-3 py-2"
          title={t("ultrawiki.explore.graph_hint")}
        >
          <span className="text-[10px] text-muted-foreground">
            {t("ultrawiki.explore.graph_hint_short")}
          </span>
          <input
            type="range"
            data-testid="explore-graph-floor"
            min={0}
            max={FLOORS.length - 1}
            step={1}
            value={Math.max(FLOORS.indexOf(minMentions as (typeof FLOORS)[number]), 0)}
            onChange={(event) =>
              onMinMentionsChange(FLOORS[Number(event.target.value)] ?? 1)
            }
            aria-label={t("ultrawiki.explore.min_mentions").replace(
              "{0}",
              String(minMentions),
            )}
            title={t("ultrawiki.explore.min_mentions").replace(
              "{0}",
              String(minMentions),
            )}
            className="h-1 w-28 cursor-pointer appearance-none rounded-full bg-muted accent-primary"
          />
          <span className="w-8 text-right text-[10px] tabular-nums text-foreground">
            {t("ultrawiki.explore.mentions").replace("{0}", String(minMentions))}
          </span>
        </div>
      </div>

      {isExpanded && selection && (
        <div
          data-testid="explore-graph-selection"
          className="uw-stage-pill absolute bottom-24 left-3 max-w-xs rounded-xl px-3.5 py-3 shadow-2xl"
        >
          <p className="text-sm text-foreground">{selection.label}</p>
          <p className="mt-0.5 text-[11px] tabular-nums text-muted-foreground">
            {t("ultrawiki.explore.mentions_long").replace(
              "{0}",
              String(selection.mentions),
            )}
            {detail?.first_seen && (
              <>
                {" · "}
                {t("ultrawiki.explore.period")
                  .replace("{0}", detail.first_seen.slice(0, 10))
                  .replace("{1}", detail.last_seen.slice(0, 10))}
              </>
            )}
          </p>

          {detail && (
            <div className="mt-2.5">
              <p className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
                {t("ultrawiki.explore.neighbors")}
              </p>
              {detail.neighbors.length === 0 ? (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {t("ultrawiki.explore.no_neighbors")}
                </p>
              ) : (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {detail.neighbors.slice(0, 8).map((neighbor) => (
                    <button
                      key={neighbor.key}
                      type="button"
                      data-testid={`explore-graph-neighbor-${neighbor.key}`}
                      onClick={() => onSelect(neighbor.key)}
                      title={t("ultrawiki.explore.shared").replace(
                        "{0}",
                        String(neighbor.shared),
                      )}
                      className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground"
                    >
                      {neighbor.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default EntityGraph;
