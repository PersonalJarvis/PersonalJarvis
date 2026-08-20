import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Crosshair, Maximize2, Minimize2, Notebook, X } from "lucide-react";
import type { NodeObject } from "react-force-graph-3d";
import { useEventStore } from "@/store/events";
import { useDeckStore } from "@/store/deck";
import { fetchWikiTree } from "@/lib/wikiApi";
import {
  edgeDetails,
  escapeTooltipText,
  nodeDetails,
  toGraphData,
  type RenderEdge,
  type RenderNode,
  type WikiGraphPayload,
} from "@/lib/wikiGraph";
import { detectWebgl } from "@/lib/graphDimension";
import { useDeckSlotPowered } from "@/components/deck/DeckReveal";
import { DeckCard, DeckIconButton } from "@/components/deck/DeckCard";
import { HudLamp, useElementSize } from "@/components/deck/HudFrame";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * The wiki on the deck — the vault's memory map, in space.
 *
 * This is the SAME scene the Wiki section draws (`components/wiki/WikiGraph3D`):
 * the same request, the same nodes, colours and size rule, the same ambient
 * orbit. The deck adds three things a front page needs: it fits into a card
 * and keeps working there, it can expand to the whole window on one click,
 * and pages that changed this session glow so the newest memory is the one
 * the eye lands on.
 *
 * The WebGL scene is the largest dependency in the app; it is loaded lazily
 * so the deck paints first and the network fades in a moment later. A machine
 * without WebGL gets the flat map instead — degraded honestly, never blank.
 *
 * Only ONE scene is ever mounted: while the expanded overlay is open, the card
 * shows a placeholder, so there is one WebGL context and one orbit.
 */
const loadWikiGraph3D = () => import("@/components/wiki/WikiGraph3D");
const WikiGraph3D = lazy(() => loadWikiGraph3D().then((m) => ({ default: m.WikiGraph3D })));

/**
 * Get the scene's heavy parts ready while nothing is happening — the deck
 * calls this from the boot's idle time, so that when the board takes
 * over, the WebGL probe is a cache hit and the 3D chunk is already parsed.
 * Measured 2026-08-19: done on the board's mount instead, the probe alone
 * held the main thread for half a second, in the middle of the launch.
 */
export function warmWikiScene(): void {
  try {
    detectWebgl();
  } catch {
    /* the probe answers false on its own when WebGL is missing */
  }
  void loadWikiGraph3D().catch(() => {
    /* a chunk that fails to preload simply loads later, as it always did */
  });
}
const WikiGraph2D = lazy(() =>
  import("@/components/wiki/WikiGraph").then((m) => ({ default: m.WikiGraph })),
);

const GRAPH_QUERY_KEY = ["wiki", "graph"] as const; // shared with the Wiki section's map

async function fetchGraph(): Promise<WikiGraphPayload> {
  const res = await fetch("/api/wiki/graph");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/**
 * The scene itself, sized to its container. Shared by the card and the
 * expanded overlay so both are literally the same map.
 */
function DeckWikiScene({
  graphData,
  highlightSlug,
  pivotSlug,
  resetSignal,
  onNodeClick,
  className,
}: {
  graphData: { nodes: RenderNode[]; links: RenderEdge[] };
  highlightSlug?: string;
  pivotSlug?: string | null;
  resetSignal: number;
  onNodeClick: (slug: string) => void;
  className?: string;
}) {
  const t = useT();
  const [ref, size] = useElementSize<HTMLDivElement>();
  const webgl = useMemo(() => detectWebgl(), []);

  const titles = useMemo(
    () => new Map(graphData.nodes.map((n) => [n.id, n.title])),
    [graphData.nodes],
  );
  const tRef = useRef(t);
  tRef.current = t;
  const nodeLabel = useCallback(
    (node: NodeObject<RenderNode>) => escapeTooltipText(nodeDetails(node as RenderNode, tRef.current)),
    [],
  );
  const linkLabel = useCallback(
    (link: RenderEdge) => escapeTooltipText(edgeDetails(link, titles)),
    [titles],
  );

  // The scene mounts once the card's slot has powered on — during the
  // launch the WebGL init would stall the very beats it sits under.
  const powered = useDeckSlotPowered();
  const ready = powered && size.w > 20 && size.h > 20;

  return (
    <div ref={ref} className={cn("relative h-full w-full overflow-hidden", className)}>
      {ready && (
        <Suspense
          fallback={
            <div className="flex h-full items-center justify-center font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
              {t("wiki_graph.loading_3d")}
            </div>
          }
        >
          {webgl ? (
            <WikiGraph3D
              graphData={graphData}
              width={size.w}
              height={size.h}
              highlightSlug={highlightSlug}
              pivotSlug={pivotSlug}
              onNodeClick={onNodeClick}
              resetSignal={resetSignal}
              nodeLabel={nodeLabel}
              linkLabel={linkLabel}
            />
          ) : (
            <WikiGraph2D onNodeClick={onNodeClick} highlightSlug={highlightSlug} />
          )}
        </Suspense>
      )}
    </div>
  );
}

export function WikiCard({ className }: { className?: string }) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const requestWikiPage = useEventStore((s) => s.requestWikiPage);
  const changes = useDeckStore((s) => s.wikiChanges);
  const [expanded, setExpanded] = useState(false);
  const [resetTick, setResetTick] = useState(0);

  const graph = useQuery({ queryKey: GRAPH_QUERY_KEY, queryFn: fetchGraph, staleTime: 30_000, retry: false });
  const tree = useQuery({ queryKey: ["deck", "wiki-tree"], queryFn: fetchWikiTree, refetchInterval: 60_000, retry: false });

  // A page the assistant just wrote should appear on the map without a
  // reload: every WikiPageChanged the deck store folds in re-fetches the graph
  // (and the counts). Debounced by react-query's own dedup, so a burst of
  // writes is one request.
  const queryClient = useQueryClient();
  const changeCount = changes.length;
  const newestTs = changes[0]?.ts ?? 0;
  useEffect(() => {
    if (changeCount === 0) return;
    void queryClient.invalidateQueries({ queryKey: GRAPH_QUERY_KEY });
    void queryClient.invalidateQueries({ queryKey: ["deck", "wiki-tree"] });
  }, [changeCount, newestTs, queryClient]);

  const graphData = useMemo(() => {
    if (!graph.data?.ok) return { nodes: [] as RenderNode[], links: [] as RenderEdge[] };
    return toGraphData(graph.data);
  }, [graph.data]);

  const pages = tree.data?.stats?.total_pages ?? graphData.nodes.length;
  const links = tree.data?.stats?.total_links ?? graphData.links.length;
  // The pivot: the user's own page, as the route names it. Until something
  // changes this session that page is also the one that glows, so the centre
  // of the turn is recognisable as a page and not just a point in space.
  const hub = graph.data?.hub ?? null;
  const highlightSlug = changes[0]?.slug ?? hub ?? undefined;

  const openPage = useCallback(
    (slug: string) => {
      requestWikiPage(slug);
      setActiveSection("memory");
    },
    [requestWikiPage, setActiveSection],
  );

  // Esc closes the expanded map — the one keyboard promise an overlay makes.
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  const empty = graph.isError || (graph.data && graph.data.ok === false) || graphData.nodes.length === 0;

  return (
    <>
      <DeckCard
        icon={Notebook}
        title={t("deck.card_wiki")}
        meta={`${pages} · ${links}`}
        live={changes.length > 0}
        variant="bracket"
        onOpen={() => setActiveSection("memory")}
        openLabel={t("deck.open_section")}
        actions={
          <>
            <DeckIconButton icon={Crosshair} label={t("wiki_graph.center")} onClick={() => setResetTick((n) => n + 1)} />
            <DeckIconButton icon={Maximize2} label={t("deck.wiki_expand")} onClick={() => setExpanded(true)} />
          </>
        }
        className={className}
        bodyClassName="p-0"
      >
        {empty ? (
          <p className="px-2.5 py-2 text-[11px] text-muted-foreground">
            {graph.isLoading ? t("wiki_graph.loading_3d") : t("deck.unavailable")}
          </p>
        ) : expanded ? (
          <div className="flex h-full items-center justify-center font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
            {t("deck.wiki_expanded_note")}
          </div>
        ) : (
          <DeckWikiScene
            graphData={graphData}
            highlightSlug={highlightSlug}
            pivotSlug={hub}
            resetSignal={resetTick}
            onNodeClick={openPage}
          />
        )}
        {changes.length > 0 && !expanded && (
          <div className="pointer-events-none absolute bottom-1.5 left-2.5 right-2.5 flex items-center gap-1.5 truncate font-mono text-[9px] text-primary">
            <HudLamp on />
            <span className="truncate">{changes.slice(0, 3).map((c) => c.slug).join(" · ")}</span>
          </div>
        )}
      </DeckCard>

      {expanded &&
        createPortal(
          <WikiExpanded
            graphData={graphData}
            highlightSlug={highlightSlug}
            pivotSlug={hub}
            pages={pages}
            links={links}
            recent={changes.map((c) => c.slug)}
            onClose={() => setExpanded(false)}
            onOpenSection={() => {
              setExpanded(false);
              setActiveSection("memory");
            }}
            onNodeClick={(slug) => {
              setExpanded(false);
              openPage(slug);
            }}
          />,
          document.body,
        )}
    </>
  );
}

/**
 * The map at the whole window. A HUD overlay, not a modal dialog: a thin
 * bracket frame, a title strip with the counts, and the scene — the wallpaper
 * still shows through the WebGL background the way it does everywhere else.
 */
function WikiExpanded({
  graphData,
  highlightSlug,
  pivotSlug,
  pages,
  links,
  recent,
  onClose,
  onOpenSection,
  onNodeClick,
}: {
  graphData: { nodes: RenderNode[]; links: RenderEdge[] };
  highlightSlug?: string;
  pivotSlug?: string | null;
  pages: number;
  links: number;
  recent: string[];
  onClose: () => void;
  onOpenSection: () => void;
  onNodeClick: (slug: string) => void;
}) {
  const t = useT();
  const [resetTick, setResetTick] = useState(0);
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("deck.card_wiki")}
      className="fixed inset-0 z-[60] flex flex-col bg-background/80 backdrop-blur-sm"
    >
      <div className="flex items-center gap-3 border-b border-primary/40 px-4 py-2">
        <HudLamp on />
        <Notebook className="h-3.5 w-3.5 text-primary" />
        <span className="font-mono text-[11px] uppercase tracking-[0.22em] text-primary">
          {t("deck.card_wiki")}
        </span>
        <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
          {pages} {t("deck.wiki_pages")} · {links} {t("deck.wiki_links")}
        </span>
        {recent.length > 0 && (
          <span className="ml-2 truncate font-mono text-[10px] text-primary">
            {t("deck.wiki_recent")}: {recent.slice(0, 4).join(" · ")}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          <DeckIconButton icon={Crosshair} label={t("wiki_graph.center")} onClick={() => setResetTick((n) => n + 1)} />
          <button
            type="button"
            onClick={onOpenSection}
            className="border border-border/70 px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground transition-colors hover:border-primary/60 hover:text-primary"
          >
            {t("deck.open_section")}
          </button>
          <DeckIconButton icon={Minimize2} label={t("deck.wiki_collapse")} onClick={onClose} />
          <DeckIconButton icon={X} label={t("deck.close")} onClick={onClose} />
        </div>
      </div>
      <div className="relative min-h-0 flex-1">
        <DeckWikiScene
          graphData={graphData}
          highlightSlug={highlightSlug}
          pivotSlug={pivotSlug}
          resetSignal={resetTick}
          onNodeClick={onNodeClick}
        />
      </div>
    </div>
  );
}
