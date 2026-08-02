// Force-directed graph of the Obsidian-vault wikilink network.
//
// Owned by Agent C of Phase B3. Pure view component — it fetches
// `/api/wiki/graph` via React Query and renders nodes/edges with
// `react-force-graph-2d`. No filter chips, no custom force tweaks; this is the
// landing view inside the Wiki tab, so it stays minimal and fast.
//
// This file owns the DATA and the chrome; the projection is a choice. The flat
// canvas below is the default and lives here; the 3D scene is `WikiGraph3D`,
// lazily loaded so nobody downloads a WebGL renderer to read a flat map. Both
// draw the same nodes, the same colours and the same size encoding out of
// `lib/wikiGraph.ts`, so switching between them changes the projection and
// nothing else.
//
// Visual contract: docs/plans/b3/00-OVERVIEW.md §3. Node colours live in
// `lib/wikiGraph.ts:NODE_COLOUR` — never hardcoded here.
import {
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useQuery } from "@tanstack/react-query";
import ForceGraph2D from "react-force-graph-2d";
import type { ForceGraphMethods, NodeObject } from "react-force-graph-2d";

import {
  BROKEN_EDGE_COLOUR,
  clampCenterToView,
  NODE_COLOUR,
  endpointId,
  nodeSizeScore,
  sizeChanged,
  toGraphData,
  type RenderEdge,
  type RenderNode,
  type WikiGraphPayload,
} from "@/lib/wikiGraph";
import { useGraphDimension } from "@/lib/graphDimension";
import { GraphDimensionToggle } from "@/components/wiki/GraphDimensionToggle";
import { useEventStore } from "@/store/events";
import { useT, useUiLanguage } from "@/i18n";

// Three.js and the WebGL renderer are by far the heaviest thing this app can
// pull in. Keeping them behind `lazy` means the chunk is fetched the first
// time somebody actually asks for the 3D map, and never otherwise.
const WikiGraph3D = lazy(() =>
  import("@/components/wiki/WikiGraph3D").then((mod) => ({
    default: mod.WikiGraph3D,
  })),
);

const GRAPH_QUERY_KEY = ["wiki", "graph"] as const;

/** Extra render-loop controls the library exposes but the typings omit. */
interface RenderControls {
  pauseAnimation?: () => void;
  resumeAnimation?: () => void;
}

async function fetchGraph(): Promise<WikiGraphPayload> {
  const res = await fetch("/api/wiki/graph");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function escapeTooltipText(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function nodeDetails(node: RenderNode, t: (key: string) => string): string {
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

function edgeDetails(
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

/**
 * Node radius in graph units — shared by the hit area and the label offset.
 * Range 1.0 … 2.6, so with `nodeRelSize=2` a dot is roughly 2.8 px (leaf) to
 * 4.6 px (hub). The scale itself lives in `lib/wikiGraph.ts` so the 3D map
 * sizes its spheres by exactly the same rule.
 */
function sizeOf(node: RenderNode, isActive: boolean): number {
  return nodeSizeScore(node.backlinkCount ?? 0, isActive);
}

export interface WikiGraphProps {
  onNodeClick: (slug: string) => void;
  /** When set, render that node enlarged with a glow. */
  highlightSlug?: string;
}

/**
 * Memory-Map force-graph. Mounts the canvas lazily when the Wiki tab renders
 * (parent owns mounting) and parks the render loop once the layout has
 * settled and the pointer is elsewhere, so an idle Wiki tab stops burning a
 * frame budget it cannot use.
 */
export function WikiGraph({ onNodeClick, highlightSlug }: WikiGraphProps): JSX.Element {
  const assistantName = useEventStore((s) => s.assistantName);
  const uiLanguage = useUiLanguage();
  const t = useT();
  // `useT()` returns a FRESH closure on every render, so using it directly in
  // a dependency array silently defeats every memo below — and this component
  // re-renders on each canvas measurement. Keep a stable callable whose
  // identity only changes when the language or the assistant name changes.
  const tRef = useRef(t);
  tRef.current = t;
  const tStable = useCallback(
    (key: string) => tRef.current(key),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- identity is
    // deliberately pinned to what actually changes the translation output.
    [uiLanguage, assistantName],
  );

  // Same reasoning for the click handler: the parent re-creates it on every
  // one of ITS renders, which would invalidate the node-list memo each time.
  const onNodeClickRef = useRef(onNodeClick);
  onNodeClickRef.current = onNodeClick;
  const selectNode = useCallback((slug: string) => {
    if (slug) onNodeClickRef.current(slug);
  }, []);

  // Flat canvas or WebGL scene. Shared with the UltraWiki map, and already
  // degraded back to flat on a machine that cannot render 3D at all.
  const { dimension } = useGraphDimension();
  const isSpatial = dimension === "3d";
  // The Center button drives two very different cameras. The flat one is reset
  // imperatively below; the 3D scene watches this counter and re-frames itself.
  const [resetTick, setResetTick] = useState(0);

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

  const nodeTitles = useMemo(
    () => new Map(graphData.nodes.map((node) => [node.id, node.title])),
    [graphData.nodes],
  );

  const graphRef = useRef<ForceGraphMethods<RenderNode, RenderEdge> | undefined>(undefined);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  // The canvas draw callbacks read the highlight through a ref so a selection
  // change does not have to swap the prop functions on the library (which
  // makes it re-ingest every accessor); the next painted frame just reads the
  // new value.
  const highlightRef = useRef(highlightSlug);
  highlightRef.current = highlightSlug;

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
  // Wall-clock deadline while OUR OWN camera animation is running. zoomToFit
  // eases over its whole duration and emits a zoom event per frame; without
  // this the very first auto-fit marked the view as "the user panned" and
  // suppressed every later auto-fit (including the one after a resize).
  const programmaticUntilRef = useRef(0);
  // Render-loop bookkeeping. `renderingRef` mirrors the library's animation
  // state; the others decide when parking it is safe.
  const renderingRef = useRef(true);
  const engineRunningRef = useRef(true);
  const pointerInsideRef = useRef(false);
  const pointerDownRef = useRef(false);
  const pauseTimerRef = useRef<number | null>(null);
  // Forces are applied once per data generation, never per tick (see below).
  const forcesConfiguredRef = useRef(false);

  const renderControls = (): RenderControls | undefined =>
    graphRef.current as RenderControls | undefined;

  const resumeRender = useCallback((): void => {
    if (pauseTimerRef.current !== null) {
      window.clearTimeout(pauseTimerRef.current);
      pauseTimerRef.current = null;
    }
    if (renderingRef.current) return;
    renderingRef.current = true;
    renderControls()?.resumeAnimation?.();
  }, []);

  /**
   * Park the render loop — but only when nothing on screen still needs
   * frames. Pausing mid-simulation would freeze a half-settled layout, and
   * pausing during a camera tween would strand it mid-flight, so both defer
   * instead of cancelling.
   */
  const pauseRenderIfIdle = useCallback(function pauseIfIdle(): void {
    if (pauseTimerRef.current !== null) {
      window.clearTimeout(pauseTimerRef.current);
      pauseTimerRef.current = null;
    }
    if (!renderingRef.current) return;
    if (engineRunningRef.current) return;
    if (pointerInsideRef.current || pointerDownRef.current) return;
    const pending = programmaticUntilRef.current - Date.now();
    if (pending > 0) {
      pauseTimerRef.current = window.setTimeout(pauseIfIdle, pending + 30);
      return;
    }
    renderingRef.current = false;
    renderControls()?.pauseAnimation?.();
  }, []);

  /** Fit the graph into view, flagged as OUR camera move, not the user's. */
  const fitToView = useCallback(
    (duration: number, padding: number): void => {
      const ref = graphRef.current;
      if (!ref) return;
      programmaticUntilRef.current = Date.now() + duration + 120;
      // A tween needs frames; make sure the loop is not parked.
      resumeRender();
      ref.zoomToFit(duration, padding);
    },
    [resumeRender],
  );

  useEffect(() => {
    if (graphData.nodes.length === 0) return;
    const timer = window.setTimeout(() => {
      if (!panInteractedRef.current) {
        fitToView(400, 120);
      }
    }, 2500);
    return () => window.clearTimeout(timer);
  }, [graphData.nodes.length, fitToView]);

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
      fitToView(400, 80);
    }, 220);
    return () => window.clearTimeout(timer);
  }, [winSize.w, winSize.h, graphData.nodes.length, fitToView]);

  // New data reheats the simulation, so the loop must be running to show it —
  // and the forces have to be re-applied to the new node/link set.
  useEffect(() => {
    if (graphData.nodes.length === 0) return;
    forcesConfiguredRef.current = false;
    engineRunningRef.current = true;
    resumeRender();
  }, [graphData, resumeRender]);

  // A selection change only needs a repaint, not a reheat: run a few frames,
  // then go back to idle.
  useEffect(() => {
    if (graphData.nodes.length === 0) return;
    resumeRender();
    const timer = window.setTimeout(() => pauseRenderIfIdle(), 400);
    return () => window.clearTimeout(timer);
  }, [highlightSlug, graphData.nodes.length, resumeRender, pauseRenderIfIdle]);

  // Pointer presence drives the idle parking: while the cursor is over the
  // canvas the user can hover, drag and wheel-zoom, all of which need frames.
  //
  // `hasGraph` is a real dependency, not decoration: the first render of this
  // component is always the loading placeholder, so the wrapper does not exist
  // yet and an attach-on-mount effect would bind to nothing and never retry.
  const hasGraph = graphData.nodes.length > 0;
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || !hasGraph) return;
    const onEnter = () => {
      pointerInsideRef.current = true;
      resumeRender();
    };
    const onLeave = () => {
      pointerInsideRef.current = false;
      pauseRenderIfIdle();
    };
    const onDown = () => {
      pointerDownRef.current = true;
      resumeRender();
    };
    const onUp = () => {
      pointerDownRef.current = false;
    };
    el.addEventListener("pointerenter", onEnter);
    el.addEventListener("pointerleave", onLeave);
    el.addEventListener("pointerdown", onDown);
    window.addEventListener("pointerup", onUp);
    el.addEventListener("wheel", resumeRender, { passive: true });
    return () => {
      el.removeEventListener("pointerenter", onEnter);
      el.removeEventListener("pointerleave", onLeave);
      el.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointerup", onUp);
      el.removeEventListener("wheel", resumeRender);
    };
  }, [hasGraph, resumeRender, pauseRenderIfIdle]);

  useEffect(
    () => () => {
      if (pauseTimerRef.current !== null) window.clearTimeout(pauseTimerRef.current);
    },
    [],
  );

  const handleResetView = useCallback((): void => {
    // Bumped FIRST, because everything below is flat-canvas work that bails
    // out the moment the 3D scene is the one on screen. The scene re-frames
    // its own camera off this counter.
    setResetTick((tick) => tick + 1);
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
    // The reheat needs frames, and it will run the engine again.
    engineRunningRef.current = true;
    resumeRender();
    const sim = anyRef.d3Force?.("simulation");
    if (sim?.alpha && sim?.restart) {
      sim.alpha(1).restart();
    }
    // Give the sim ~1.5 s to spread, then fit.
    window.setTimeout(() => fitToView(600, 100), 1500);
  }, [graphData.nodes, fitToView, resumeRender]);

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
  const clampViewToBounds = useCallback((): void => {
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
  }, [winSize.w, winSize.h]);

  const handleZoom = useCallback((): void => {
    // Our own fit animation emits zoom events too — those are not a user pan,
    // and the fit is in-bounds by construction, so leave both flags alone.
    if (Date.now() < programmaticUntilRef.current) return;
    // Any user-driven zoom/pan cancels future auto-fits. Without
    // this, the 2.5 s pending fit would yank the canvas back to
    // centre right after the user finished dragging.
    panInteractedRef.current = true;
    // Live wall — keep the graph reachable WHILE the user drags, so it
    // sticks at the edge instead of sliding off and vanishing.
    clampViewToBounds();
  }, [clampViewToBounds]);

  const handleZoomEnd = useCallback((): void => {
    // Safety net for any pan/zoom that slipped past the live clamp
    // (e.g. a wheel-zoom that shifts the centre).
    if (Date.now() < programmaticUntilRef.current) return;
    clampViewToBounds();
  }, [clampViewToBounds]);

  const handleNodeDrag = useCallback((): void => {
    panInteractedRef.current = true;
  }, []);

  const handleBackgroundClick = useCallback(
    (event: MouseEvent): void => {
      // Double-click on empty canvas re-centres the view.
      // Cheaper than reaching for the Zentrieren button.
      if (event.detail >= 2) {
        panInteractedRef.current = false;
        handleResetView();
      }
    },
    [handleResetView],
  );

  // Force-sim tuning for dense graphs (~10+ edges per node), applied ONCE per
  // data generation.
  //
  // This used to run on EVERY engine tick. d3's setters are not free: each
  // `charge.strength()` re-initialises the force over all nodes and each
  // `link.distance()` recomputes the distance of every link — so a 60 fps
  // simulation paid an extra O(nodes + links) pass per frame purely to
  // re-assign constants that never changed. On a dense vault that is exactly
  // the stutter users felt while the graph settled and while dragging.
  const configureForces = useCallback((): void => {
    engineRunningRef.current = true;
    if (forcesConfiguredRef.current) return;
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
    // Soft centering: every node feels a gentle pull towards (0,0) — this
    // replaces the hard bounding box. Strength 0.08 keeps the cluster from
    // drifting infinitely, but is gentle enough that the user can drag a node
    // far away and it stays where they dropped it. Higher values (0.3-0.5)
    // caused dragged nodes to spring back to the centre, which felt broken
    // when the user wanted to lay the graph out manually.
    const centerForce = anyRef.d3Force?.("center");
    if (centerForce && typeof centerForce.strength === "function") {
      centerForce.strength(0.08);
    }
    forcesConfiguredRef.current = true;
  }, []);

  const handleEngineStop = useCallback((): void => {
    engineRunningRef.current = false;
    // First chance the simulation finished spreading — fit the bounding box
    // of the settled nodes into the viewport. 80px padding leaves room for
    // labels at all four edges. Skipped once the user has taken over the
    // camera: a reheat (node drag, reset) ends here too, and yanking the view
    // back every time a drag settles was its own reported bug.
    if (!panInteractedRef.current) {
      fitToView(500, 80);
    }
    // Nothing left to animate — park the loop until the user comes back.
    pauseRenderIfIdle();
  }, [fitToView, pauseRenderIfIdle]);

  const nodeLabel = useCallback(
    (node: NodeObject<RenderNode>) =>
      escapeTooltipText(nodeDetails(node as RenderNode, tStable)),
    [tStable],
  );

  const linkLabel = useCallback(
    (link: RenderEdge) => escapeTooltipText(edgeDetails(link as RenderEdge, nodeTitles)),
    [nodeTitles],
  );

  const nodeVal = useCallback(
    (node: NodeObject<RenderNode>) =>
      sizeOf(node as RenderNode, node.id === highlightRef.current),
    [],
  );

  const nodeColor = useCallback(
    (node: NodeObject<RenderNode>) => node.colour ?? NODE_COLOUR.entity ?? "#8b95a7",
    [],
  );

  const nodeCanvasObjectMode = useCallback(() => "after" as const, []);

  const nodeCanvasObject = useCallback(
    (
      node: NodeObject<RenderNode>,
      ctx: CanvasRenderingContext2D,
      globalScale: number,
    ): void => {
      const x = node.x ?? 0;
      const y = node.y ?? 0;
      const isActive = node.id === highlightRef.current;
      // Match the nodeVal calculation so label distance stays consistent with
      // the dot edge across all node sizes.
      const radius = sizeOf(node as RenderNode, isActive) * 2;
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
    },
    [],
  );

  const linkColor = useCallback(
    (link: RenderEdge) =>
      (link as RenderEdge).broken ? BROKEN_EDGE_COLOUR : "rgba(106, 169, 255, 0.45)",
    [],
  );

  const linkLineDash = useCallback(
    (link: RenderEdge) => ((link as RenderEdge).broken ? [4, 4] : null),
    [],
  );

  const linkArrowColor = useCallback(
    (link: RenderEdge) =>
      (link as RenderEdge).broken ? BROKEN_EDGE_COLOUR : "rgba(106, 169, 255, 0.75)",
    [],
  );

  const handleNodeClick = useCallback(
    (node: NodeObject<RenderNode>): void => {
      selectNode((node.id as string | undefined) ?? "");
    },
    [selectNode],
  );

  // Hidden DOM mirror — keeps the canvas-based graph testable without a full
  // canvas mock. The visible canvas remains the source of truth for users;
  // this list is purely a behaviour anchor for RTL + a11y readers.
  //
  // Memoised because it is the single largest render cost in this component:
  // one <li> plus a formatted label per node AND per edge. Rebuilding all of
  // that on every canvas measurement (the ResizeObserver fires a burst per
  // window drag) is what made resizing the window feel like the graph froze.
  const nodeListItems = useMemo(
    () =>
      graphData.nodes.map((node) => {
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
              onClick={() => selectNode(node.id)}
              aria-label={nodeDetails(node, tStable)}
            >
              {node.title}
            </button>
          </li>
        );
      }),
    [graphData.nodes, highlightSlug, selectNode, tStable],
  );

  const edgeListItems = useMemo(
    () =>
      graphData.links.map((edge, index) => {
        const source = endpointId(edge.source);
        const target = endpointId(edge.target);
        return (
          <li
            key={`${source}:${target}:${index}`}
            data-testid="wiki-graph-edge"
            data-edge-source={source}
            data-edge-target={target}
            data-edge-broken={edge.broken ? "true" : "false"}
          >
            {edgeDetails(edge, nodeTitles)}
            {edge.broken ? ` · ${tStable("wiki_graph.unresolved")}` : ""}
          </li>
        );
      }),
    [graphData.links, nodeTitles, tStable],
  );

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

  return (
    <div
      ref={wrapRef}
      data-testid="wiki-graph-wrap"
      className="relative h-full w-full overflow-hidden"
      role="group"
      aria-label={t("wiki_graph.relationship_graph")}
    >
      <div className="absolute top-3 right-3 z-10 flex items-center gap-2">
        <GraphDimensionToggle />
        <button
          type="button"
          onClick={handleResetView}
          data-testid="wiki-graph-reset-view"
          className="rounded-md border border-border bg-card/80 px-3 py-1.5 text-xs text-muted-foreground backdrop-blur transition hover:text-foreground hover:bg-card"
          title={t("wiki_graph.reset_view_title")}
        >
          {t("wiki_graph.center")}
        </button>
      </div>
      <ul data-testid="wiki-graph-node-list" className="sr-only">
        {nodeListItems}
      </ul>
      <ul data-testid="wiki-graph-edge-list" className="sr-only">
        {edgeListItems}
      </ul>

      {isSpatial ? (
        <Suspense
          fallback={
            <div
              data-testid="wiki-graph-3d-loading"
              className="flex h-full items-center justify-center text-sm text-muted-foreground"
            >
              {t("wiki_graph.loading_3d")}
            </div>
          }
        >
          <WikiGraph3D
            graphData={graphData}
            width={winSize.w}
            height={winSize.h}
            highlightSlug={highlightSlug}
            onNodeClick={selectNode}
            resetSignal={resetTick}
            nodeLabel={nodeLabel}
            linkLabel={linkLabel}
          />
        </Suspense>
      ) : (
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
        //
        // Every accessor below is a STABLE callback. Passing fresh closures
        // made the library re-ingest its whole accessor set on each of this
        // component's renders, for values that never changed.
        ref={graphRef}
        graphData={graphData}
        width={winSize.w}
        height={winSize.h}
        backgroundColor="rgba(0,0,0,0)"
        nodeId="id"
        nodeLabel={nodeLabel}
        // Pan + zoom + drag — all interactions enabled so the user
        // can move freely. NO bounding box — the previous bounding
        // box implementation created an "invisible wall" the user
        // could hit and we removed it deliberately.
        enablePanInteraction={true}
        enableZoomInteraction={true}
        enableNodeDrag={true}
        minZoom={0.1}
        maxZoom={8}
        onZoom={handleZoom}
        onZoomEnd={handleZoomEnd}
        onNodeDrag={handleNodeDrag}
        onBackgroundClick={handleBackgroundClick}
        // Compact, Obsidian-like node size.
        nodeRelSize={2}
        nodeVal={nodeVal}
        nodeColor={nodeColor}
        nodeCanvasObjectMode={nodeCanvasObjectMode}
        nodeCanvasObject={nodeCanvasObject}
        linkLabel={linkLabel}
        linkColor={linkColor}
        linkWidth={1.0}
        linkLineDash={linkLineDash}
        linkDirectionalArrowLength={4}
        linkDirectionalArrowRelPos={0.82}
        linkDirectionalArrowColor={linkArrowColor}
        //  - velocityDecay 0.6: damp oscillation in dense graphs
        //  - alphaDecay 0.04: settle time ~4s
        //  - cooldownTicks 200: matches alphaDecay
        //  - warmupTicks 40: enough pre-paint settling that the first
        //    visible frame is already mostly in shape
        cooldownTicks={200}
        d3VelocityDecay={0.6}
        d3AlphaDecay={0.04}
        warmupTicks={40}
        onEngineTick={configureForces}
        onEngineStop={handleEngineStop}
        onNodeClick={handleNodeClick}
      />
      )}
    </div>
  );
}

export default WikiGraph;
