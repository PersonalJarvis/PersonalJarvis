import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Download,
  ExternalLink,
  FolderOpen,
  Frame,
  Loader2,
  Maximize2,
  RefreshCw,
  ShieldAlert,
  Workflow,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ViewHeader } from "@/views/ChatsView";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { openExternalUrl } from "@/lib/openExternal";
import {
  artifactDownloadUrl,
  revealArtifact,
  useArtifactsForOutput,
  useOutputsCapabilities,
  useOutputsList,
  usePlanForOutput,
  type ArtifactSummary,
  type OutputStatus,
  type OutputSummary,
} from "@/hooks/useOutputs";
import {
  classifyVisual,
  missionMapUrl,
  MISSION_MAP_PATH,
  useVisualArtifacts,
  visualUrl,
  type VisualKind,
} from "@/hooks/useVisualArtifacts";
import {
  buildRunGraph,
  edgePath,
  NODE_H,
  NODE_W,
  type RunGraph,
  type RunGraphNode,
} from "@/lib/runGraph";
import {
  categoryColor,
  categoryTint,
  NODE_CATEGORIES,
  nodeCategory,
  nodeGlyph,
} from "@/lib/nodeSpec";

/**
 * The Visualization section — a run drawn as a workflow, not listed as files.
 *
 * Every archived run is presented the way a workflow editor would draw it:
 * the request as the start node, one node per tool call the worker actually
 * made (with its real success/failure, reconstructed from the archived
 * transcript by `/api/outputs/{slug}/plan`), the final answer, and the
 * deliverables on a second track connected to the steps that wrote them.
 * One glance answers "what did this run do" — the question a flat
 * newest-first file gallery could never answer.
 *
 * It still owns no data: runs come from `/api/outputs`, steps from the plan
 * endpoint, deliverables from the artifact listing. A run archived before
 * this feature existed — or whose stream never survived — degrades to
 * start → deliverables, so the archive needs no migration.
 *
 * The server-rendered mission map (`/api/outputs/{slug}/graph`, see
 * `mission_graph.py`) is this graph's no-JS sibling: same story, baked into
 * a static page under the strict no-script CSP. It stays one click away
 * ("open as page") because a page can be shared, printed and opened in a
 * real browser tab — things a canvas inside the app cannot do.
 *
 * Detachable (`DETACHABLE_VIEWS` in jarvis/ui/desktop_app.py): a run graph is
 * the thing people put on a second monitor while the mission is still going.
 */

/*
 * Zoom: the buttons snap to these steps, the wheel (Ctrl/⌘ + scroll, and the
 * trackpad pinch that browsers report the same way) moves continuously, and
 * "fit" produces whatever ratio the graph needs — so the step logic finds the
 * NEAREST step in the pressed direction rather than requiring an exact match.
 */
const ZOOM_STEPS = [0.25, 0.35, 0.5, 0.65, 0.8, 1, 1.25, 1.5] as const;
const MIN_ZOOM = ZOOM_STEPS[0];
const MAX_ZOOM = ZOOM_STEPS[ZOOM_STEPS.length - 1];

const clampZoom = (value: number): number =>
  Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));

/** Same status colour language as the Outputs cards — one vocabulary. */
const RUN_DOT: Record<OutputStatus, string> = {
  success: "bg-emerald-400",
  error: "bg-destructive",
  running: "bg-primary animate-pulse",
  cancelled: "bg-amber-400",
  unknown: "bg-muted-foreground/50",
};

const NODE_DOT: Record<string, string> = {
  done: "bg-emerald-400",
  failed: "bg-destructive",
  running: "bg-primary animate-pulse",
  skipped: "bg-muted-foreground/40",
};

/** The rail row's timestamp — what tells two same-ask runs apart. */
function formatRunDate(entry: OutputSummary): string {
  const ts = entry.completed_at ?? entry.started_at;
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Human file size, rounded the way a caption wants it. */
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Human duration for a step — sub-second precision only where it matters. */
function formatDuration(seconds: number): string {
  if (seconds < 10) return `${seconds.toFixed(1)} s`;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes} min ${rest} s`;
}

export function VisualizationView() {
  const t = useT();
  const outputs = useOutputsList();
  const runs = useMemo(() => outputs.data ?? [], [outputs.data]);

  /* A `?run=<slug>` in the URL pre-selects that run — what makes a detached
   * window or a pasted link open on the run it talks about, instead of
   * whatever happens to be newest. Read once at mount; clicks own it after. */
  const [selectedSlug, setSelectedSlug] = useState<string | null>(
    () => new URLSearchParams(window.location.search).get("run"),
  );
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  /*
   * The newest run is the default subject — the graph the user most likely
   * came for. It is a fallback, not state: a refetch that reorders the list
   * must never fight an explicit click.
   */
  const run: OutputSummary | null = useMemo(() => {
    if (runs.length === 0) return null;
    return runs.find((r) => r.slug === selectedSlug) ?? runs[0];
  }, [runs, selectedSlug]);

  const plan = usePlanForOutput(run?.slug ?? null);
  const artifacts = useArtifactsForOutput(run?.slug ?? null);

  /*
   * Another surface asked for something to be staged.
   *
   * The request arrives through the store rather than as a prop, because the
   * asker is never this section's parent — it is the agent strip in a
   * different view, unmounted by the time this one renders. A target names a
   * `visualId` (slug::path) or "latest"; both resolve to a run plus, for a
   * real file, its artifact node. The mission-map pseudo-artifact resolves to
   * the run alone — this canvas IS the map. See VisualStageRequest.
   */
  const visualStage = useEventStore((s) => s.visualStage);
  const { visuals } = useVisualArtifacts();
  useEffect(() => {
    if (visualStage === null) return;
    if (visualStage.target === "latest") {
      const newest = visuals[0];
      if (newest) {
        setSelectedSlug(newest.slug);
        setSelectedNodeId(
          newest.path === MISSION_MAP_PATH ? null : `artifact:${newest.path}`,
        );
      }
      return;
    }
    const separator = visualStage.target.indexOf("::");
    if (separator > 0) {
      const path = visualStage.target.slice(separator + 2);
      setSelectedSlug(visualStage.target.slice(0, separator));
      setSelectedNodeId(path === MISSION_MAP_PATH ? null : `artifact:${path}`);
    }
  }, [visualStage, visuals]);

  const graph: RunGraph | null = useMemo(() => {
    if (run === null) return null;
    return buildRunGraph({
      slug: run.slug,
      utterance: run.utterance,
      runStatus: run.status,
      plan: plan.data,
      files: artifacts.data?.files ?? [],
    });
  }, [run, plan.data, artifacts.data]);

  /* A node pinned from another run's graph must not linger as a selection
   * that matches nothing — clear it once the new graph is known. */
  useEffect(() => {
    setSelectedNodeId((current) =>
      current === null || graph?.nodes.some((n) => n.id === current)
        ? current
        : null,
    );
  }, [graph]);

  const selectedNode = useMemo(() => {
    if (graph === null) return null;
    return graph.nodes.find((n) => n.id === selectedNodeId) ?? null;
  }, [graph, selectedNodeId]);

  const [zoom, setZoom] = useState<number>(1);
  /* Once the user has zoomed or panned by hand, the automatic fit keeps its
   * hands off until another run is selected — an explicit choice must never
   * be fought. */
  const userZoomed = useRef(false);
  const [canvasEl, setCanvasEl] = useState<HTMLDivElement | null>(null);

  /*
   * Anchored zoom: every zoom keeps ONE canvas point where it is on screen —
   * the cursor for the wheel, the viewport centre for the buttons. Without an
   * anchor the scale pivots on the canvas origin and the node being looked at
   * slides out of view. The anchor is recorded in content coordinates and the
   * scroll correction lands right after the rescaled canvas hits the DOM,
   * before paint — no one-frame jump.
   */
  const zoomRef = useRef(1);
  const zoomAnchor = useRef<{
    px: number;
    py: number;
    cx: number;
    cy: number;
  } | null>(null);

  useLayoutEffect(() => {
    zoomRef.current = zoom;
    const pending = zoomAnchor.current;
    if (pending === null || canvasEl === null) return;
    zoomAnchor.current = null;
    canvasEl.scrollLeft = pending.px * zoom - pending.cx;
    canvasEl.scrollTop = pending.py * zoom - pending.cy;
  }, [zoom, canvasEl]);

  /** Zoom to `next`, keeping the given viewport point (default: centre) put. */
  const zoomTo = useCallback(
    (next: number, at?: { cx: number; cy: number }) => {
      const clamped = clampZoom(next);
      const current = zoomRef.current;
      if (canvasEl !== null && clamped !== current) {
        const cx = at?.cx ?? canvasEl.clientWidth / 2;
        const cy = at?.cy ?? canvasEl.clientHeight / 2;
        zoomAnchor.current = {
          px: (canvasEl.scrollLeft + cx) / current,
          py: (canvasEl.scrollTop + cy) / current,
          cx,
          cy,
        };
      }
      setZoom(clamped);
    },
    [canvasEl],
  );

  const stepZoom = useCallback(
    (direction: 1 | -1) => {
      userZoomed.current = true;
      const current = zoomRef.current;
      const next =
        direction > 0
          ? (ZOOM_STEPS.find((s) => s > current + 0.001) ?? MAX_ZOOM)
          : ([...ZOOM_STEPS].reverse().find((s) => s < current - 0.001) ??
            Math.min(current, MIN_ZOOM));
      zoomTo(next);
    },
    [zoomTo],
  );

  /* Scale the whole story into the visible canvas — never above 100%, so a
   * two-node run stays a pair of cards instead of a pair of billboards. The
   * guard skips hidden/unmeasured hosts (detached warm-up, jsdom). */
  const fitZoom = useCallback(() => {
    if (canvasEl === null || graph === null) return;
    const { clientWidth, clientHeight } = canvasEl;
    if (clientWidth < 80 || clientHeight < 80) return;
    const scale = Math.min(
      (clientWidth - 16) / graph.width,
      (clientHeight - 16) / graph.height,
      1,
    );
    // Fit means "show the whole story": the viewport returns to the origin,
    // by plain assignment (no pending anchor — fit IS the anchor decision).
    zoomAnchor.current = null;
    canvasEl.scrollLeft = 0;
    canvasEl.scrollTop = 0;
    setZoom(clampZoom(scale));
  }, [canvasEl, graph]);

  useEffect(() => {
    userZoomed.current = false;
  }, [run?.slug]);

  /* Auto-fit whenever the graph's extents change — the first paint, the plan
   * arriving a beat after the run list, and a live run growing new steps. */
  useEffect(() => {
    if (!userZoomed.current) fitZoom();
  }, [fitZoom, graph?.width, graph?.height]);

  /* Ctrl/⌘ + wheel zooms (that's also how browsers deliver a trackpad pinch).
   * Attached natively because React registers wheel listeners passively, and
   * a passive listener cannot preventDefault the browser's own page zoom. */
  useEffect(() => {
    if (canvasEl === null) return;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      userZoomed.current = true;
      const rect = canvasEl.getBoundingClientRect();
      zoomTo(zoomRef.current * Math.exp(-event.deltaY * 0.002), {
        cx: event.clientX - rect.left,
        cy: event.clientY - rect.top,
      });
    };
    canvasEl.addEventListener("wheel", onWheel, { passive: false });
    return () => canvasEl.removeEventListener("wheel", onWheel);
  }, [canvasEl, zoomTo]);

  /*
   * Drag-to-pan. The scroll container follows a held primary/middle button on
   * canvas background; a press on a node stays a click. Pointer capture keeps
   * a fast drag alive outside the canvas (guarded: jsdom has no capture API).
   */
  const [panning, setPanning] = useState(false);
  const panState = useRef<{
    pointerId: number;
    originX: number;
    originY: number;
    scrollLeft: number;
    scrollTop: number;
  } | null>(null);

  const onPanStart = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (canvasEl === null) return;
      if (event.button !== 0 && event.button !== 1) return;
      if ((event.target as Element).closest("button") !== null) return;
      // Middle button would otherwise start the browser's own autoscroll.
      if (event.button === 1) event.preventDefault();
      panState.current = {
        pointerId: event.pointerId,
        originX: event.clientX,
        originY: event.clientY,
        scrollLeft: canvasEl.scrollLeft,
        scrollTop: canvasEl.scrollTop,
      };
      setPanning(true);
      // Capture keeps a fast drag alive outside the canvas. Guarded twice:
      // jsdom neither constructs PointerEvents (pointerId undefined) nor
      // accepts capture for a pointer it never saw.
      if (typeof event.pointerId === "number")
        canvasEl.setPointerCapture?.(event.pointerId);
    },
    [canvasEl],
  );

  const onPanMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const pan = panState.current;
      if (pan === null || canvasEl === null) return;
      const dx = event.clientX - pan.originX;
      const dy = event.clientY - pan.originY;
      // A real drag (not a sloppy click) is a viewport choice like a manual
      // zoom: the auto-fit stops following a live run's growth.
      if (Math.abs(dx) + Math.abs(dy) > 4) userZoomed.current = true;
      canvasEl.scrollLeft = pan.scrollLeft - dx;
      canvasEl.scrollTop = pan.scrollTop - dy;
    },
    [canvasEl],
  );

  const onPanEnd = useCallback(() => {
    const pan = panState.current;
    if (pan === null) return;
    panState.current = null;
    setPanning(false);
    if (typeof pan.pointerId === "number")
      canvasEl?.releasePointerCapture?.(pan.pointerId);
  }, [canvasEl]);

  const refetch = useCallback(() => {
    void outputs.refetch();
    void plan.refetch();
    void artifacts.refetch();
  }, [outputs, plan, artifacts]);

  const loading = outputs.isLoading || plan.isLoading || artifacts.isLoading;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <ViewHeader
        icon={<Frame className="h-4 w-4 text-primary" aria-hidden />}
        title={t("visualization.title")}
        subtitle={t("visualization.subtitle")}
        right={
          <div className="flex items-center gap-1.5">
            {run !== null && (
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  void openExternalUrl(
                    `${window.location.origin}${missionMapUrl(run.slug)}`,
                  )
                }
                title={t("visualization.open_map_page_hint")}
                data-testid="visualization-open-map"
              >
                <Workflow className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                {t("visualization.open_map_page")}
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={refetch}
              disabled={loading}
              data-testid="visualization-refresh"
            >
              {loading ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden />
              )}
              {t("visualization.refresh")}
            </Button>
          </div>
        }
      />

      <div className="flex min-h-0 flex-1">
        {/* Run rail — every archived run, newest first. */}
        <aside className="flex w-64 shrink-0 flex-col border-r border-border">
          <p className="border-b border-border px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {t("visualization.runs")}
          </p>
          <ScrollArea className="min-h-0 flex-1">
            <ul className="space-y-1 p-2" data-testid="visualization-runs">
              {runs.map((entry) => (
                <li key={entry.slug}>
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedSlug(entry.slug);
                      setSelectedNodeId(null);
                    }}
                    aria-current={entry.slug === run?.slug}
                    className={cn(
                      "flex w-full items-start gap-2 rounded-md px-2 py-2 text-left text-xs transition-colors",
                      entry.slug === run?.slug
                        ? "bg-primary/15 text-foreground"
                        : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
                    )}
                  >
                    <span
                      className={cn(
                        "mt-1 h-1.5 w-1.5 shrink-0 rounded-full",
                        RUN_DOT[entry.status ?? "unknown"],
                      )}
                      aria-hidden
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium text-foreground">
                        {entry.utterance?.trim() || entry.slug}
                      </span>
                      <span className="block truncate">
                        {[
                          formatRunDate(entry),
                          t("visualization.run_meta").replace(
                            "{0}",
                            String(entry.artifact_count ?? 0),
                          ),
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </ScrollArea>
        </aside>

        {/* Canvas — the selected run as connected nodes along tracks. */}
        <section className="flex min-h-0 min-w-0 flex-1 flex-col">
          {graph === null ? (
            <EmptyCanvas
              loading={outputs.isLoading}
              error={outputs.isError}
              title={t(
                outputs.isError
                  ? "visualization.error_title"
                  : "visualization.empty_title",
              )}
              body={t(
                outputs.isError
                  ? "visualization.error_body"
                  : "visualization.empty_body",
              )}
            />
          ) : (
            <>
              <div
                ref={setCanvasEl}
                className={cn(
                  "min-h-0 flex-1 overflow-auto",
                  panning ? "cursor-grabbing" : "cursor-grab",
                )}
                data-testid="visualization-canvas"
                onPointerDown={onPanStart}
                onPointerMove={onPanMove}
                onPointerUp={onPanEnd}
                onPointerCancel={onPanEnd}
              >
                <GraphCanvas
                  graph={graph}
                  zoom={zoom}
                  selectedId={selectedNodeId}
                  onSelect={setSelectedNodeId}
                />
              </div>

              <footer className="flex shrink-0 items-center gap-3 border-t border-border px-4 py-2">
                {/* The honesty line: what this graph could and could not read. */}
                <p className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground">
                  {plan.data && plan.data.plan === null
                    ? t("visualization.no_steps")
                    : graph.droppedSteps > 0
                      ? t("visualization.steps_dropped").replace(
                          "{0}",
                          String(graph.droppedSteps),
                        )
                      : t("visualization.legend")}
                </p>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => stepZoom(-1)}
                    title={t("visualization.zoom_out")}
                    aria-label={t("visualization.zoom_out")}
                  >
                    <ZoomOut className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                  <button
                    type="button"
                    onClick={() => {
                      userZoomed.current = true;
                      zoomTo(1);
                    }}
                    title={t("visualization.zoom_reset")}
                    aria-label={t("visualization.zoom_reset")}
                    className="w-12 text-center text-[11px] tabular-nums text-muted-foreground transition-colors hover:text-foreground"
                  >
                    {`${Math.round(zoom * 100)}%`}
                  </button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => stepZoom(1)}
                    title={t("visualization.zoom_in")}
                    aria-label={t("visualization.zoom_in")}
                  >
                    <ZoomIn className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      // An explicit fit re-arms the auto-fit: a live run that
                      // grows new steps keeps fitting until zoomed by hand.
                      userZoomed.current = false;
                      fitZoom();
                    }}
                    title={t("visualization.zoom_fit")}
                    aria-label={t("visualization.zoom_fit")}
                    data-testid="visualization-zoom-fit"
                  >
                    <Maximize2 className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                </div>
              </footer>
            </>
          )}
        </section>

        {/* Inspector — what one node did, or what one deliverable looks like. */}
        {selectedNode !== null && run !== null && (
          <NodeInspector
            node={selectedNode}
            slug={run.slug}
            onClose={() => setSelectedNodeId(null)}
          />
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------- */

function GraphCanvas({
  graph,
  zoom,
  selectedId,
  onSelect,
}: {
  graph: RunGraph;
  zoom: number;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const t = useT();
  const byId = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph]);

  return (
    <div
      style={{ width: graph.width * zoom, height: graph.height * zoom }}
      className="relative min-h-full min-w-full"
    >
      <div
        style={{
          width: graph.width,
          height: graph.height,
          transform: `scale(${zoom})`,
          transformOrigin: "top left",
        }}
        className="relative"
      >
        {/* Edge layer under the nodes. Colours come from the theme tokens, so
            the tracks are signal-yellow on matte black in the dark theme and
            legible gold on paper in the light one — never a hardcoded hex. */}
        <svg
          width={graph.width}
          height={graph.height}
          className="absolute inset-0"
          aria-hidden
        >
          {/* Arrowheads. Since the track wraps, an edge can run back to the
              left — only a pointed end keeps the flow readable. markerUnits
              defaults to strokeWidth, so a highlighted edge's arrow grows
              with its stroke for free. refX=8 puts the tip exactly on the
              path end — the target card's port, drawn on the layer above. */}
          <defs>
            <marker
              id="viz-arrow"
              viewBox="0 0 8 8"
              refX="8"
              refY="4"
              markerWidth="5.5"
              markerHeight="5.5"
              orient="auto"
            >
              <path d="M 0 0 L 8 4 L 0 8 Z" className="fill-primary/70" />
            </marker>
            <marker
              id="viz-arrow-failed"
              viewBox="0 0 8 8"
              refX="8"
              refY="4"
              markerWidth="5.5"
              markerHeight="5.5"
              orient="auto"
            >
              <path d="M 0 0 L 8 4 L 0 8 Z" className="fill-destructive/80" />
            </marker>
          </defs>
          {graph.edges.map((edge) => {
            const from = byId.get(edge.from);
            const to = byId.get(edge.to);
            if (!from || !to) return null;
            /* The selected node's own edges brighten, so "what fed this and
             * what came of it" is answered by the picture, not by tracing. */
            const active =
              selectedId !== null &&
              (edge.from === selectedId || edge.to === selectedId);
            return (
              <path
                key={edge.id}
                data-testid="graph-edge"
                d={edgePath(from, to)}
                fill="none"
                strokeWidth={active ? 2.5 : 1.5}
                markerEnd={`url(#${edge.failed ? "viz-arrow-failed" : "viz-arrow"})`}
                className={
                  edge.failed
                    ? active
                      ? "stroke-destructive"
                      : "stroke-destructive/70"
                    : active
                      ? "stroke-primary"
                      : "stroke-primary/50"
                }
              />
            );
          })}
        </svg>

        {graph.nodes.map((node) => {
          const category = nodeCategory(node);
          const Icon = nodeGlyph(node);
          const hue = categoryColor(category);
          const selected = node.id === selectedId;
          const reasoning = category === "reasoning";
          /* Two calm lines per card. A reasoning card is titled as what it
           * IS, with the thought as its detail line — a raw sentence as a
           * bold headline reads like a rendering accident. */
          const title =
            node.kind === "start"
              ? t("visualization.node_start")
              : node.kind === "result"
                ? t("visualization.node_result")
                : reasoning
                  ? t("visualization.cat_reasoning")
                  : node.title;
          const subtitle =
            node.kind === "start"
              ? node.title
              : reasoning
                ? node.title
                : node.subtitle;
          /* Port dots come from the graph model (edge-derived), so a dot is
           * always a real attachment point — a start node has no input, a
           * deliverable no output, exactly like a workflow editor's nodes. */
          const port =
            "absolute h-1.5 w-1.5 rounded-full border border-background bg-primary/70";
          return (
            <button
              key={node.id}
              type="button"
              onClick={() => onSelect(node.id)}
              aria-current={selected}
              data-testid={`graph-node-${node.kind}`}
              data-category={category}
              style={{ left: node.x, top: node.y, width: NODE_W, height: NODE_H }}
              className={cn(
                "absolute flex items-center gap-2.5 rounded-xl border bg-card px-3 text-left shadow-sm transition-colors",
                /* A thought is not an action: reasoning cards wear a dashed
                 * frame, the visual grammar of "internal, produced nothing". */
                reasoning && "border-dashed",
                /* A failed or running step announces itself from the card
                 * frame — a 6px dot alone is not a glanceable alarm. */
                selected
                  ? "border-primary ring-1 ring-primary/40"
                  : node.status === "failed"
                    ? "border-destructive/70 hover:border-destructive"
                    : node.status === "running"
                      ? "border-primary/60 hover:border-primary"
                      : "border-border hover:border-primary/50",
              )}
            >
              {node.ports.left && (
                <span
                  className={cn(port, "-left-[3px] top-1/2 -translate-y-1/2")}
                  aria-hidden
                />
              )}
              {node.ports.right &&
                (category === "agent" ? (
                  /* A spawn node branches — its output port doubles, the way
                   * n8n's if node wears one connector per outcome. */
                  <>
                    <span
                      className={cn(port, "-right-[3px] top-1/2 -translate-y-[7px]")}
                      aria-hidden
                    />
                    <span
                      className={cn(port, "-right-[3px] top-1/2 translate-y-[1px]")}
                      aria-hidden
                    />
                  </>
                ) : (
                  <span
                    className={cn(port, "-right-[3px] top-1/2 -translate-y-1/2")}
                    aria-hidden
                  />
                ))}
              {node.ports.top && (
                <span
                  className={cn(port, "-top-[3px] left-1/2 -translate-x-1/2")}
                  aria-hidden
                />
              )}
              {node.ports.bottom && (
                <span
                  className={cn(port, "-bottom-[3px] left-1/2 -translate-x-1/2")}
                  aria-hidden
                />
              )}
              {/* The category speaks through the icon alone — its hue and
                  glyph — never through an extra text line. The label survives
                  as the tile's tooltip for whoever hovers to ask. */}
              <span
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg"
                style={{ color: hue, backgroundColor: categoryTint(category, 0.12) }}
                title={t(NODE_CATEGORIES[category].labelKey)}
              >
                <Icon className="h-4 w-4" aria-hidden />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs font-medium text-foreground">
                  {title}
                </span>
                {subtitle && (
                  <span className="block truncate text-[11px] text-muted-foreground">
                    {subtitle}
                  </span>
                )}
              </span>
              {node.status !== "none" && !reasoning && (
                <span
                  className={cn(
                    "h-1.5 w-1.5 shrink-0 rounded-full",
                    NODE_DOT[node.status],
                  )}
                  title={t(`visualization.status_${node.status}`)}
                />
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------- */

function NodeInspector({
  node,
  slug,
  onClose,
}: {
  node: RunGraphNode;
  slug: string;
  onClose: () => void;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const capabilities = useOutputsCapabilities();

  const artifact = node.kind === "artifact" ? (node.artifact ?? null) : null;
  const visualKind = artifact ? classifyVisual(artifact.path) : null;

  const onReveal = useCallback(async () => {
    if (!artifact) return;
    try {
      await revealArtifact(slug, artifact.path);
    } catch {
      pushToast("error", t("visualization.reveal_failed"));
    }
  }, [artifact, slug, pushToast, t]);

  return (
    <aside
      className="flex w-96 shrink-0 flex-col border-l border-border"
      data-testid="visualization-inspector"
    >
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <p className="min-w-0 flex-1 truncate text-xs font-medium">
          {node.kind === "start"
            ? t("visualization.node_start")
            : node.kind === "result"
              ? t("visualization.node_result")
              : node.title}
        </p>
        <Button
          variant="ghost"
          size="sm"
          onClick={onClose}
          title={t("visualization.close")}
          aria-label={t("visualization.close")}
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </Button>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-3 p-3">
          {/* A reasoning step is a thought, not a call: it gets the thought
              text and nothing pretending to be tool telemetry. */}
          {node.kind === "step" && node.step?.kind === "reasoning" && (
            <InspectorField label={t("visualization.field_thought")}>
              <p className="whitespace-pre-wrap break-words text-xs">
                {node.step.output || node.step.name}
              </p>
            </InspectorField>
          )}

          {node.kind === "step" && node.step && node.step.kind !== "reasoning" && (
            <>
              <InspectorField label={t("visualization.field_tool")}>
                {node.step.tool_name ?? "—"}
              </InspectorField>
              <InspectorField label={t("visualization.field_detail")}>
                <code className="block whitespace-pre-wrap break-words text-[11px]">
                  {node.step.name}
                </code>
              </InspectorField>
              {node.step.output && (
                <InspectorField label={t("visualization.field_output")}>
                  <code className="block whitespace-pre-wrap break-words text-[11px] text-muted-foreground">
                    {node.step.output}
                  </code>
                </InspectorField>
              )}
              {node.step.error && (
                <InspectorField label={t("visualization.field_error")}>
                  <code className="block whitespace-pre-wrap break-words text-[11px] text-destructive">
                    {node.step.error}
                  </code>
                </InspectorField>
              )}
              <InspectorField label={t("visualization.field_status")}>
                {t(`visualization.status_${node.status}`)}
              </InspectorField>
              {typeof node.step.duration_s === "number" &&
                node.step.duration_s > 0 && (
                  <InspectorField label={t("visualization.field_duration")}>
                    {formatDuration(node.step.duration_s)}
                  </InspectorField>
                )}
              {/* One attempt is the norm — only a retry is worth a line. */}
              {(node.step.attempts ?? 1) > 1 && (
                <InspectorField label={t("visualization.field_attempts")}>
                  {String(node.step.attempts)}
                </InspectorField>
              )}
            </>
          )}

          {(node.kind === "start" || node.kind === "result") && (
            <InspectorField
              label={
                node.kind === "start"
                  ? t("visualization.field_request")
                  : t("visualization.field_answer")
              }
            >
              <p className="whitespace-pre-wrap break-words text-xs">
                {(node.kind === "start" ? node.title : node.subtitle) || "—"}
              </p>
            </InspectorField>
          )}

          {artifact && (
            <>
              {visualKind !== null && (
                <ArtifactPreview slug={slug} artifact={artifact} kind={visualKind} />
              )}
              <InspectorField label={t("visualization.field_file")}>
                <code className="block break-words text-[11px]">
                  {artifact.path}
                </code>
              </InspectorField>
              <InspectorField label={t("visualization.field_size")}>
                {formatSize(artifact.size)}
              </InspectorField>
              <div className="flex flex-wrap items-center gap-1 pt-1">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    void openExternalUrl(
                      `${window.location.origin}${visualUrl(slug, artifact.path)}`,
                    )
                  }
                  title={t("visualization.open_external")}
                >
                  <ExternalLink className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                  {t("visualization.open_external")}
                </Button>
                <Button variant="ghost" size="sm" asChild>
                  <a
                    href={artifactDownloadUrl(slug, artifact.path)}
                    download
                    title={t("visualization.download")}
                  >
                    <Download className="h-3.5 w-3.5" aria-hidden />
                  </a>
                </Button>
                {/* Desktop only: a headless host has no file manager to open,
                    so the button is absent rather than dead. */}
                {capabilities.data?.native_file_actions && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => void onReveal()}
                    title={t("visualization.reveal")}
                  >
                    <FolderOpen className="h-3.5 w-3.5" aria-hidden />
                  </Button>
                )}
              </div>
            </>
          )}
        </div>
      </ScrollArea>
    </aside>
  );
}

function InspectorField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <div className="text-xs">{children}</div>
    </div>
  );
}

/**
 * The deliverable preview inside the inspector.
 *
 * Raster and vector go through `<img>`, which cannot execute anything even
 * when the SVG carries a script. Pages and PDFs need a document frame, and
 * those are the two cases where the file's own content is being interpreted —
 * the backend already serves both under the no-script CSP (`VIEW_CSP`,
 * outputs_routes.py), and the frame is sandboxed on top so the preview stays
 * inert even if that header is ever loosened.
 */
function ArtifactPreview({
  slug,
  artifact,
  kind,
}: {
  slug: string;
  artifact: ArtifactSummary;
  kind: VisualKind;
}) {
  const t = useT();
  const [failed, setFailed] = useState(false);
  const url = visualUrl(slug, artifact.path);

  // A new file must not inherit the previous file's failure verdict.
  useEffect(() => setFailed(false), [url]);

  if (failed) {
    return (
      <p className="text-xs text-muted-foreground">
        {t("visualization.render_failed")}
      </p>
    );
  }

  if (kind === "image" || kind === "vector") {
    return (
      <img
        src={url}
        alt={artifact.path}
        data-testid="visualization-image"
        onError={() => setFailed(true)}
        className="max-h-72 w-full rounded-md border border-border bg-background/40 object-contain"
      />
    );
  }

  return (
    <div className="space-y-1.5">
      <iframe
        src={url}
        title={artifact.path}
        data-testid="visualization-frame"
        onError={() => setFailed(true)}
        // Empty sandbox = opaque origin, no scripts, no forms, no navigation.
        // A PDF still renders: the browser's own viewer is not the framed
        // document's script context.
        sandbox=""
        className="h-72 w-full rounded-md border border-border bg-white"
      />
      <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <ShieldAlert className="h-3 w-3 shrink-0" aria-hidden />
        {t("visualization.sandbox_note")}
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------------- */

function EmptyCanvas({
  loading,
  error,
  title,
  body,
}: {
  loading: boolean;
  error: boolean;
  title: string;
  body: string;
}) {
  return (
    <div
      className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 p-8 text-center"
      data-testid="visualization-empty"
    >
      {loading && !error ? (
        <Loader2
          className="h-5 w-5 animate-spin text-muted-foreground"
          aria-hidden
        />
      ) : (
        <Frame className="h-6 w-6 text-muted-foreground" aria-hidden />
      )}
      <p className="text-sm font-medium">{title}</p>
      <p className="max-w-sm text-xs text-muted-foreground">{body}</p>
    </div>
  );
}
