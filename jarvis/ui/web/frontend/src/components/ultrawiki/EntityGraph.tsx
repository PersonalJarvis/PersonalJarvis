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
import ForceGraph2D from "react-force-graph-2d";
import type {
  ForceGraphMethods,
  LinkObject,
  NodeObject,
} from "react-force-graph-2d";

import { useT } from "@/i18n";
import { sizeChanged } from "@/lib/wikiGraph";
import { corpusSpan, nodeRadius, recencyTint } from "@/lib/entityGraph";
import { fetchExploreGraph } from "@/lib/ultrawikiExploreApi";

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

  return (
    <div className="flex h-full flex-col">
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
      </div>

      <p className="border-t border-border px-4 py-1 text-[10px] text-muted-foreground">
        {t("ultrawiki.explore.graph_hint")}
      </p>
    </div>
  );
}

export default EntityGraph;
