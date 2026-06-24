// Force-directed 2D graph of the Obsidian-vault wikilink network.
//
// Owned by Agent C of Phase B3. Pure view component — it fetches
// `/api/wiki/graph` via React Query and renders nodes/edges with
// `react-force-graph-2d`. No filter chips, no custom force tweaks; this is the
// landing view inside the Wiki tab, so it stays minimal and fast.
//
// Visual contract: docs/plans/b3/00-OVERVIEW.md §3 + the HTML mockup at
// C:\Users\Administrator\Desktop\b3-wiki-view-mockup.html. Node colours live in
// `lib/wikiGraph.ts:NODE_COLOUR` — never hardcoded here.
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import ForceGraph2D from "react-force-graph-2d";
import type { ForceGraphMethods, NodeObject } from "react-force-graph-2d";

import {
  BROKEN_EDGE_COLOUR,
  clampCenterToView,
  NODE_COLOUR,
  sizeChanged,
  toGraphData,
  type RenderEdge,
  type RenderNode,
  type WikiGraphPayload,
} from "@/lib/wikiGraph";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

const GRAPH_QUERY_KEY = ["wiki", "graph"] as const;

async function fetchGraph(): Promise<WikiGraphPayload> {
  const res = await fetch("/api/wiki/graph");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export interface WikiGraphProps {
  onNodeClick: (slug: string) => void;
  /** When set, render that node enlarged with a glow. */
  highlightSlug?: string;
}

/**
 * Memory-Map force-graph. Mounts the canvas lazily when the Wiki tab renders
 * (parent owns mounting) and freezes the simulation after a short warm-up so
 * an idle Wiki tab stops repainting the canvas.
 */
export function WikiGraph({ onNodeClick, highlightSlug }: WikiGraphProps): JSX.Element {
  const assistantName = useEventStore((s) => s.assistantName);
  const t = useT();
  const { data, isLoading, isError } = useQuery({
    queryKey: GRAPH_QUERY_KEY,
    queryFn: fetchGraph,
    staleTime: 30_000,
  });

  const graphData = useMemo(() => {
    if (!data?.ok) return { nodes: [] as RenderNode[], links: [] as RenderEdge[] };
    const out = toGraphData(data);
    // CRITICAL — pre-spread initial positions on a tight circle so
    // force-graph-2d's simulation has direction vectors from frame 1.
    // Tight radius (60) means the graph fits inside the viewport even
    // when the canvas dimensions are still stale on first paint.
    const radius = 60;
    out.nodes.forEach((node, idx) => {
      const angle = (idx / Math.max(1, out.nodes.length)) * Math.PI * 2;
      // Use deterministic angles plus a small jitter so identical runs
      // produce identical layouts (helps tests) while still avoiding
      // perfect-circle artefacts in the settled graph.
      const jitter = ((idx * 31) % 13) - 6;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const n = node as any;
      n.x = Math.cos(angle) * (radius + jitter);
      n.y = Math.sin(angle) * (radius + jitter);
    });
    return out;
  }, [data]);

  const graphRef = useRef<ForceGraphMethods<RenderNode, RenderEdge> | undefined>(undefined);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  // Canvas dimensions match the wrap container EXACTLY so the graph's
  // (0,0) origin lands at the centre of the visible area.
  //
  // Measurement strategy that finally works:
  //  1. Initial state seeded with a sensible default (window minus
  //     sidebar+backlinks ~= window - 600).
  //  2. RAF-driven polling loop that re-measures on every frame.
  //     Stops once we get a positive measurement, then re-subscribes
  //     to ResizeObserver for live updates. The continuous polling
  //     phase exists because some layout passes report clientWidth=0
  //     for several frames after mount, and a single ResizeObserver
  //     registration doesn't fire if the size never changes.
  const [winSize, setWinSize] = useState<{ w: number; h: number }>(() => ({
    w: typeof window !== "undefined" ? Math.max(800, window.innerWidth - 600) : 800,
    h: typeof window !== "undefined" ? Math.max(600, window.innerHeight - 120) : 600,
  }));
  useEffect(() => {
    let rafId: number | null = null;
    let observer: ResizeObserver | null = null;
    let stopped = false;
    const apply = (w: number, h: number) => {
      // Absorb sub-pixel jitter (scrollbar flicker, DPI rounding) so a noisy
      // ResizeObserver stream doesn't churn React state for no visible change.
      setWinSize((prev) => (sizeChanged(prev, { w, h }) ? { w, h } : prev));
    };
    const poll = () => {
      if (stopped) return;
      const el = wrapRef.current;
      if (el) {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) {
          apply(Math.floor(r.width), Math.floor(r.height));
          // Once we have a real size, switch from polling to observer.
          if (!observer) {
            observer = new ResizeObserver(() => {
              const rr = el.getBoundingClientRect();
              if (rr.width > 0 && rr.height > 0) {
                apply(Math.floor(rr.width), Math.floor(rr.height));
              }
            });
            observer.observe(el);
          }
          return; // stop polling
        }
      }
      rafId = window.requestAnimationFrame(poll);
    };
    poll();
    return () => {
      stopped = true;
      if (rafId !== null) window.cancelAnimationFrame(rafId);
      observer?.disconnect();
    };
  }, []);

  // Freeze the simulation after 8s so the canvas stops repainting on idle.
  // The library re-energises it automatically on node drag.
  useEffect(() => {
    if (graphData.nodes.length === 0) return;
    const handle = window.setTimeout(() => {
      const sim = graphRef.current?.d3Force("simulation") as unknown as
        | { alphaTarget?: (a: number) => { restart: () => void } }
        | undefined;
      if (sim && typeof sim.alphaTarget === "function") {
        sim.alphaTarget(0).restart();
      }
    }, 8000);
    return () => window.clearTimeout(handle);
  }, [graphData.nodes.length]);

  // Auto-fit ONCE, ~2.5 s after the graph data lands. We deliberately
  // do NOT keep firing zoomToFit on a schedule — that destroyed the
  // user's pan: as soon as they dragged the canvas, a pending fit
  // would yank everything back to centre. One initial fit is enough
  // for landing the graph in the viewport; after that the user owns
  // the view. The Zentrieren button is still there for an explicit
  // reset. We also bail out of the auto-fit if the user has already
  // touched the canvas (panInteracted ref).
  const panInteractedRef = useRef(false);
  // Guards the programmatic centerAt() inside onZoomEnd from re-entering itself:
  // centerAt re-emits zoom events, which would otherwise re-fire the boundary
  // clamp in a feedback loop.
  const correctingRef = useRef(false);
  useEffect(() => {
    if (graphData.nodes.length === 0) return;
    const timer = window.setTimeout(() => {
      if (!panInteractedRef.current) {
        graphRef.current?.zoomToFit(400, 120);
      }
    }, 2500);
    return () => window.clearTimeout(timer);
  }, [graphData.nodes.length]);

  // Re-frame after a resize. Now that the canvas resizes in place (no remount),
  // the settled graph keeps its positions but can sit off-centre in the new
  // viewport — so once the size stops changing we fit it back into view. The
  // 220 ms debounce collapses a whole window-drag into a single fit at the end
  // instead of one per intermediate size. A resize is an explicit layout
  // change, so this re-fit intentionally overrides a prior manual pan.
  useEffect(() => {
    if (graphData.nodes.length === 0) return;
    const timer = window.setTimeout(() => {
      panInteractedRef.current = false;
      graphRef.current?.zoomToFit(400, 80);
    }, 220);
    return () => window.clearTimeout(timer);
  }, [winSize.w, winSize.h, graphData.nodes.length]);

  if (isLoading) {
    return (
      <div
        data-testid="wiki-graph-loading"
        className="flex h-full items-center justify-center text-sm text-muted-foreground"
      >
        {t("wiki_graph.loading")}
      </div>
    );
  }

  if (isError || !data?.ok) {
    return (
      <div
        data-testid="wiki-graph-error"
        className="flex h-full items-center justify-center text-sm text-muted-foreground"
      >
        {t("wiki_graph.load_error")}
      </div>
    );
  }

  if (graphData.nodes.length === 0) {
    return (
      <div
        data-testid="wiki-graph-empty"
        className="flex h-full items-center justify-center px-8 text-center text-sm text-muted-foreground"
      >
        {t("wiki_graph.empty_prefix")}
        {assistantName}
        {t("wiki_graph.empty_suffix")}
      </div>
    );
  }

  const handleResetView = (): void => {
    const ref = graphRef.current;
    if (!ref) return;
    // User explicitly asked for centring → clear the pan-touched flag
    // so the post-reheat zoomToFit actually fires.
    panInteractedRef.current = false;
    // Two-stage reset: first re-spread nodes onto a fresh circle and
    // reheat the simulation (alpha=1), then zoomToFit after the layout
    // has had a chance to spread out. Without the reheat step a
    // collapsed graph (all nodes at one pixel) stays collapsed after
    // zoomToFit because the math is "fit a 1px-wide bounding box into
    // the viewport" -> infinite zoom.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const anyRef = ref as any;
    const radius = Math.max(180, graphData.nodes.length * 28);
    graphData.nodes.forEach((node, idx) => {
      const angle = (idx / Math.max(1, graphData.nodes.length)) * Math.PI * 2;
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const n = node as any;
      n.x = Math.cos(angle) * radius;
      n.y = Math.sin(angle) * radius;
      // Clear pinned positions + velocity so the reheat starts cleanly.
      // These are d3-internals not in the RenderNode TS type but they
      // exist at runtime on every node force-graph-2d touches.
      n.fx = null;
      n.fy = null;
      n.vx = 0;
      n.vy = 0;
    });
    const sim = anyRef.d3Force?.("simulation");
    if (sim?.alpha && sim?.restart) {
      sim.alpha(1).restart();
    }
    // Give the sim ~1.5 s to spread, then fit.
    window.setTimeout(() => ref.zoomToFit(600, 100), 1500);
  };

  // Keep the graph from being panned/zoomed entirely out of view.
  //
  // react-force-graph allows unbounded background panning, and a pure pan does
  // NOT reheat the simulation — so the onEngineStop re-fit can never rescue a
  // graph the user dragged off-screen. That was the reported bug: drag the
  // network toward an edge and it vanishes ("the right wall disappears"), with
  // no way back except the Zentrieren button. Here we re-clamp the camera on
  // every pan/zoom tick so the graph "sticks" at the viewport edge (a slice
  // always stays visible; the whole graph stays visible when it is smaller than
  // the viewport — see clampCenterToView).
  //
  // The correction is IMMEDIATE (duration 0). A transitioned centerAt is
  // unreliable here: force-graph's tween is driven by its render loop, which
  // throttles once the simulation freezes, so an eased correction stalls
  // mid-flight (verified live). A synchronous set holds. The guard absorbs the
  // nested zoom event that translateTo re-emits, so this can't loop.
  const clampViewToBounds = (): void => {
    if (correctingRef.current) return;
    const ref = graphRef.current;
    if (!ref) return;
    const bbox = ref.getGraphBbox();
    if (!bbox) return;
    const center = ref.screen2GraphCoords(winSize.w / 2, winSize.h / 2);
    const target = clampCenterToView(center, ref.zoom(), bbox, {
      w: winSize.w,
      h: winSize.h,
    });
    if (Math.abs(target.x - center.x) > 0.5 || Math.abs(target.y - center.y) > 0.5) {
      correctingRef.current = true;
      ref.centerAt(target.x, target.y, 0);
      correctingRef.current = false;
    }
  };

  return (
    <div ref={wrapRef} data-testid="wiki-graph-wrap" className="relative h-full w-full overflow-hidden">
      <button
        type="button"
        onClick={handleResetView}
        data-testid="wiki-graph-reset-view"
        className="absolute top-3 right-3 z-10 rounded-md border border-border bg-card/80 px-3 py-1.5 text-xs text-muted-foreground backdrop-blur transition hover:text-foreground hover:bg-card"
        title={t("wiki_graph.reset_view_title")}
      >
        {t("wiki_graph.center")}
      </button>
      {/* Hidden DOM mirror — keeps the canvas-based graph testable without a
          full canvas mock. The visible canvas remains the source of truth for
          users; this list is purely a behaviour anchor for RTL + a11y readers. */}
      <ul data-testid="wiki-graph-node-list" className="sr-only">
        {graphData.nodes.map((node) => {
          const isActive = node.id === highlightSlug;
          const renderRadius = isActive ? node.radius * 1.5 : node.radius;
          return (
            <li
              key={node.id}
              data-testid="wiki-graph-node"
              data-node-id={node.id}
              data-node-kind={node.kind}
              data-node-radius={renderRadius}
              data-node-active={isActive ? "true" : "false"}
            >
              <button
                type="button"
                onClick={() => onNodeClick(node.id)}
                aria-label={`${node.title} (${node.kind})`}
              >
                {node.title}
              </button>
            </li>
          );
        })}
      </ul>

      <ForceGraph2D<RenderNode, RenderEdge>
        // NO remount key. react-force-graph-2d (via react-kapsule) maps the
        // width/height props onto live `.width()/.height()` calls that resize
        // the canvas WITHOUT restarting the simulation, so node positions
        // survive a resize. A `key={WxH}` here used to unmount+remount the
        // whole graph on every pixel of size change, restarting the force sim
        // from scratch — so any stream of resize events (window drag/maximise,
        // DPI rounding, scrollbar flicker) made the network flail and fly off
        // screen. The settled layout is preserved now; a debounced zoomToFit
        // (see the winSize effect above) just re-frames it after a real resize.
        ref={graphRef}
        graphData={graphData}
        width={winSize.w}
        height={winSize.h}
        backgroundColor="rgba(0,0,0,0)"
        nodeId="id"
        // Pan + zoom + drag — all interactions enabled so the user
        // can move freely. NO bounding box — the previous bounding
        // box implementation created an "invisible wall" the user
        // could hit and we removed it deliberately.
        enablePanInteraction={true}
        enableZoomInteraction={true}
        enableNodeDrag={true}
        minZoom={0.1}
        maxZoom={8}
        onZoom={() => {
          // Any user-driven zoom/pan cancels future auto-fits. Without
          // this, the 2.5 s pending fit would yank the canvas back to
          // centre right after the user finished dragging.
          panInteractedRef.current = true;
          // Live wall — keep the graph reachable WHILE the user drags, so it
          // sticks at the edge instead of sliding off and vanishing.
          clampViewToBounds();
        }}
        onZoomEnd={() => {
          // Safety net for any pan/zoom that slipped past the live clamp
          // (e.g. a wheel-zoom that shifts the centre).
          clampViewToBounds();
        }}
        onNodeDrag={() => {
          panInteractedRef.current = true;
        }}
        onBackgroundClick={(event) => {
          // Double-click on empty canvas re-centres the view.
          // Cheaper than reaching for the Zentrieren button.
          if ((event as MouseEvent).detail >= 2) {
            panInteractedRef.current = false;
            handleResetView();
          }
        }}
        // Compact, Obsidian-like node size.
        nodeRelSize={2}
        nodeVal={(node: NodeObject<RenderNode>) => {
          const isActive = node.id === highlightSlug;
          const backlinks = (node as RenderNode).backlinkCount ?? 0;
          // Range 1.0 ... 2.6 → with nodeRelSize=2 that's a node radius
          // of roughly 2.8 px (leaf) ... 4.6 px (hub).
          const sizeScore = 1.0 + Math.min(backlinks, 8) * 0.2;
          return isActive ? sizeScore * 1.5 : sizeScore;
        }}
        nodeColor={(node: NodeObject<RenderNode>) =>
          node.colour ?? NODE_COLOUR.entity ?? "#8b95a7"
        }
        nodeCanvasObjectMode={() => "after"}
        nodeCanvasObject={(node, ctx, globalScale) => {
          const x = node.x ?? 0;
          const y = node.y ?? 0;
          const isActive = node.id === highlightSlug;
          // Match the nodeVal calculation so label distance stays
          // consistent with the dot edge across all node sizes.
          const backlinks = (node as RenderNode).backlinkCount ?? 0;
          const sizeScore = 1.0 + Math.min(backlinks, 8) * 0.2;
          const radius = (isActive ? sizeScore * 1.5 : sizeScore) * 2;
          if (isActive) {
            ctx.save();
            ctx.beginPath();
            ctx.arc(x, y, radius + 4, 0, 2 * Math.PI, false);
            ctx.fillStyle = "rgba(106, 169, 255, 0.18)";
            ctx.fill();
            ctx.restore();
          }
          // Always-visible labels in screen-space (divide font size by
          // globalScale so labels look ~10 px regardless of zoom).
          const label = (node as RenderNode).title ?? (node.id as string | undefined) ?? "";
          if (label) {
            ctx.save();
            const fontSize = 10 / globalScale;
            ctx.font = `${fontSize}px ui-sans-serif, system-ui, sans-serif`;
            ctx.textAlign = "center";
            ctx.textBaseline = "top";
            ctx.fillStyle = isActive ? "#e6ecf5" : "#a8b0c0";
            ctx.shadowColor = "rgba(0,0,0,0.85)";
            ctx.shadowBlur = 3 / globalScale;
            ctx.fillText(label, x, y + radius + 3 / globalScale);
            ctx.restore();
          }
        }}
        linkColor={(link) => ((link as RenderEdge).broken ? BROKEN_EDGE_COLOUR : "rgba(106, 169, 255, 0.45)")}
        linkWidth={(link) => ((link as RenderEdge).broken ? 1.0 : 1.0)}
        linkLineDash={(link) => ((link as RenderEdge).broken ? [4, 4] : null)}
        // Force-sim tuning for dense graphs (~10+ edges per node).
        // Calibrated for a vault with ~14 backlinks per node:
        //  - charge -500: strong repulsion so dense clusters spread out
        //  - linkDistance 110: long edges for label whitespace
        //  - velocityDecay 0.5: damp oscillation in dense graphs
        //  - alphaDecay 0.02: settle time ~4s
        //  - cooldownTicks 400: matches alphaDecay
        //  - warmupTicks 60: enough pre-paint settling that the first
        //    visible frame is already mostly in shape
        cooldownTicks={200}
        d3VelocityDecay={0.6}
        d3AlphaDecay={0.04}
        warmupTicks={40}
        onEngineTick={() => {
          // Force-sim tuning. NO hard bounding box — the user can
          // drag any node anywhere. The forces are calibrated so the
          // graph naturally settles into a compact cluster without a
          // wall.
          const ref = graphRef.current;
          if (!ref) return;
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const anyRef = ref as any;
          // Repulsion: each pair pushes each other away with strength
          // -180 if closer than 220 px, then falls off. Settled
          // envelope is ~300x300 px.
          const chargeForce = anyRef.d3Force?.("charge");
          if (chargeForce && typeof chargeForce.strength === "function") {
            chargeForce.strength(-180);
            if (typeof chargeForce.distanceMax === "function") {
              chargeForce.distanceMax(220);
            }
          }
          // Links stay short so labels stay close enough to read.
          const linkForce = anyRef.d3Force?.("link");
          if (linkForce && typeof linkForce.distance === "function") {
            linkForce.distance(55);
            if (typeof linkForce.strength === "function") {
              linkForce.strength(0.85);
            }
          }
          // Very soft centering — strength 0.08 keeps the cluster
          // from drifting infinitely, but is gentle enough that the
          // user can drag a node far away and it stays where they
          // dropped it. Higher values (0.3-0.5) caused dragged nodes
          // to spring back to the centre, which felt broken when the
          // user wanted to lay the graph out manually.
          // Soft centering: every node feels a gentle pull towards
          // (0,0) — this replaces the hard bounding box. Strength 0.5
          // is firm enough that even after dragging, releasing a node
          // brings it back home.
          const centerForce = anyRef.d3Force?.("center");
          if (centerForce && typeof centerForce.strength === "function") {
            centerForce.strength(0.08);
          }
        }}
        onEngineStop={() => {
          // First chance the simulation finished spreading — fit the
          // bounding box of the settled nodes into the viewport. 80px
          // padding leaves room for labels at all four edges.
          graphRef.current?.zoomToFit(500, 80);
        }}
        onNodeClick={(node) => {
          const slug = (node.id as string | undefined) ?? "";
          if (slug) onNodeClick(slug);
        }}
      />
    </div>
  );
}

export default WikiGraph;
