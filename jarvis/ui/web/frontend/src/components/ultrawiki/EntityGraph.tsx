/**
 * The map of which topics appear together.
 *
 * A node is a topic, an edge means "these two came up in the same moment",
 * and the edge is thicker the more often that happened. Node size is mention
 * count, node brightness is recency — both computed in lib/entityGraph.ts so
 * the list and the map speak the same visual language.
 *
 * The mention floor is the whole reason this is readable. On a real corpus
 * 977 topics collapse to 313 at two mentions and 77 at five; drawing every
 * one-off at once is a hairball, not a map. The slider makes the trade
 * explicit and reversible instead of hiding data silently.
 */
import { useEffect, useMemo, useRef, useState } from "react";
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
import { corpusSpan, nodeRadius, recencyTint } from "@/lib/entityGraph";
import {
  fetchExploreEntity,
  fetchExploreGraph,
} from "@/lib/ultrawikiExploreApi";

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

  const query = useQuery({
    queryKey: ["ultrawiki", "explore", "graph", minMentions],
    queryFn: () => fetchExploreGraph(minMentions),
    staleTime: 30_000,
  });

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (!box) return;
      const next = { w: Math.round(box.width), h: Math.round(box.height) };
      // Sub-pixel churn would restart the force simulation and make the
      // network flail; the shared threshold helper absorbs it.
      setSize((prev) => (sizeChanged(prev, next) ? next : prev));
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
      className={cn(
        "flex h-full flex-col bg-background",
        isExpanded && "fixed inset-0 z-[100]",
      )}
    >
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-1.5">
        <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
          {t("ultrawiki.explore.graph_title")}
        </p>
        <div className="flex items-center gap-2">
          <span
            data-testid="explore-graph-shown"
            className="text-[11px] tabular-nums text-muted-foreground"
          >
            {t("ultrawiki.explore.graph_shown")
              .replace("{0}", String(shown))
              .replace("{1}", String(total))}
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
            className="h-1 w-24 cursor-pointer appearance-none rounded-full bg-muted accent-primary"
          />
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
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-border bg-background/70 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
          >
            {isExpanded ? (
              <Minimize2 className="h-3.5 w-3.5" aria-hidden />
            ) : (
              <Maximize2 className="h-3.5 w-3.5" aria-hidden />
            )}
          </button>
        </div>
      </div>

      <div ref={containerRef} className="relative min-h-0 flex-1">
        {shown === 0 ? (
          <p
            data-testid="explore-graph-empty"
            className="flex h-full items-center justify-center px-6 text-center text-xs text-muted-foreground"
          >
            {t("ultrawiki.explore.graph_empty")}
          </p>
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
            linkColor={() => "rgba(125, 184, 255, 0.16)"}
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
                ctx.lineWidth = 1.5 / scale;
                ctx.strokeStyle = "#ffffff";
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
                  : "rgba(255,255,255,0.55)";
                ctx.fillText(node.label, node.x, node.y + node.radius + 1.5);
              }
            }}
          />
        )}

        {isExpanded && selection && (
          <div
            data-testid="explore-graph-selection"
            className="absolute bottom-3 left-3 max-w-xs rounded-lg border border-border bg-background/90 px-3 py-2 shadow-lg backdrop-blur-sm"
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
              <div className="mt-2">
                <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
                  {t("ultrawiki.explore.neighbors")}
                </p>
                {detail.neighbors.length === 0 ? (
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {t("ultrawiki.explore.no_neighbors")}
                  </p>
                ) : (
                  <div className="mt-1 flex flex-wrap gap-1">
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
                        className="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground hover:border-primary/50 hover:text-foreground"
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

      <p className="border-t border-border px-4 py-1 text-[10px] text-muted-foreground">
        {t("ultrawiki.explore.graph_hint")}
      </p>
    </div>
  );
}

export default EntityGraph;
