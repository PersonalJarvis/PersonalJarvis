/**
 * The Wiki section: the assistant's long-term memory, as the Obsidian vault
 * on disk holds it.
 *
 * Read-only — writes happen via the wiki curator or the user editing Markdown
 * files in Obsidian. This view is a projection of the vault exposed through
 * the `/api/wiki/*` endpoints.
 *
 * One viewport, three regions, nothing below the fold:
 *
 *   ┌───────────┬──────────────────────────────┬────────────┐
 *   │  library  │  stage: memory map | page    │ inspector  │
 *   │  272 px   │  (fills the rest)            │  320 px    │
 *   └───────────┴──────────────────────────────┴────────────┘
 *
 * The library finds a page, the stage shows it (or the whole vault as a
 * map), and the inspector tells you how it connects — or, with nothing open,
 * whether the memory is healthy and what it took in today.
 */
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BookOpen, Maximize2, Minimize2, Network, Search, X } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { ViewHeader } from "@/views/ChatsView";
import { cn } from "@/lib/utils";
import { useT, useUiLanguage } from "@/i18n";
import { useEventStore } from "@/store/events";
import {
  fetchWikiHealth,
  fetchWikiTree,
  rebuildWikiIndex,
} from "@/lib/wikiApi";
import { cleanTitle, libraryItems, relativeAge, type LibraryItem } from "@/lib/wikiModel";
import { useWikiLive } from "@/hooks/useWikiLive";

import { EmptyState } from "@/components/ui/empty-state";
import { WikiLibrary } from "@/components/wiki/WikiLibrary";
import { PageRenderer } from "@/components/wiki/PageRenderer";
import {
  HEALTH_DOT_STYLE,
  HEALTH_LABEL_KEY,
  WikiInspector,
  classifyWikiHealth,
} from "@/components/wiki/WikiInspector";
import { WikiSearch, type WikiSearchHandle } from "@/components/wiki/WikiSearch";
import { ObsidianStatus } from "@/components/wiki/ObsidianStatus";
import { ObsidianSetupDialog } from "@/components/wiki/ObsidianSetupDialog";
import type { ObsidianStatus as ObsidianStatusType } from "@/types/setup";

// The graph bundle (~120 KB minified) only loads when the Wiki section mounts.
const WikiGraph = lazy(() =>
  import("@/components/wiki/WikiGraph").then((mod) => ({
    default: mod.WikiGraph,
  })),
);

type CentreTab = "graph" | "page";

interface WikiToast {
  message: string;
  id: number;
}

const IS_MAC =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/i.test(navigator.platform || navigator.userAgent);

export function WikiView(): JSX.Element {
  const t = useT();
  const language = useUiLanguage();
  useWikiLive();
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [centreTab, setCentreTab] = useState<CentreTab>("graph");
  const [isGraphExpanded, setIsGraphExpanded] = useState(false);
  const [toast, setToast] = useState<WikiToast | null>(null);
  // The setup walkthrough opens with the status payload the pill last saw.
  // The hint object also reseeds whenever the user reopens the dialog so
  // step-2-vs-step-3 starts from the most recent reality.
  const [setupHint, setSetupHint] = useState<ObsidianStatusType | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [isReindexing, setIsReindexing] = useState(false);
  const [reindexError, setReindexError] = useState<string | null>(null);
  const searchRef = useRef<WikiSearchHandle>(null);

  // A staged "open this page" request from another section (e.g. the Contacts
  // detail's wiki link). `seq` bumps on every request, so re-opening the same
  // slug still fires.
  const wikiPageRequest = useEventStore((s) => s.wikiPageRequest);
  useEffect(() => {
    if (wikiPageRequest) setSelectedSlug(wikiPageRequest.slug);
  }, [wikiPageRequest]);

  const treeQuery = useQuery({
    queryKey: ["wiki", "tree"],
    queryFn: fetchWikiTree,
    staleTime: 5_000,
  });

  const stats = treeQuery.data?.stats;
  const totalPages = stats?.total_pages ?? 0;
  const totalLinks = stats?.total_links ?? 0;
  const folders = useMemo(() => treeQuery.data?.folders ?? [], [treeQuery.data?.folders]);
  const items = useMemo(() => libraryItems(folders), [folders]);
  const itemsBySlug = useMemo(() => {
    const map = new Map<string, LibraryItem>();
    for (const item of items) if (!map.has(item.slug)) map.set(item.slug, item);
    return map;
  }, [items]);

  // Wiki subsystem health: polled on mount + every 30 s so the "honest, not
  // silent" status stays live without a manual refresh.
  const healthQuery = useQuery({
    queryKey: ["wiki", "health"],
    queryFn: fetchWikiHealth,
    refetchInterval: 30_000,
    staleTime: 5_000,
  });

  // Selecting a page (tree, graph, wikilink, search) swaps to the page tab.
  useEffect(() => {
    if (selectedSlug) {
      setCentreTab("page");
      setIsGraphExpanded(false);
    }
  }, [selectedSlug]);

  // Escape leaves the full-window map. With the nav rail covered it is the
  // reflex people reach for first.
  useEffect(() => {
    if (!isGraphExpanded) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsGraphExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isGraphExpanded]);

  // On the first visit, auto-open the Obsidian setup walkthrough — but only
  // if the user never marked it completed AND the status says action is
  // required. AbortController cancels both requests on unmount.
  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    (async () => {
      try {
        const [statusResp, stateResp] = await Promise.all([
          fetch("/api/setup/obsidian/status", { signal: controller.signal }),
          fetch("/api/setup/state", { signal: controller.signal }),
        ]);
        if (cancelled || !statusResp.ok || !stateResp.ok) return;

        const status = (await statusResp.json()) as ObsidianStatusType;
        const state = (await stateResp.json()) as { obsidian_setup_seen: boolean };

        if (cancelled) return;
        if (state.obsidian_setup_seen === false && status.recommended_action !== "ok") {
          setSetupHint(status);
          setDialogOpen(true);
        }
      } catch (err) {
        // AbortError is expected on unmount; anything else is only logged —
        // the Obsidian pill in the header still gives the user a manual entry.
        if ((err as { name?: string })?.name !== "AbortError") {
          console.debug("[WikiView] first-run setup probe failed:", err);
        }
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, []);

  const showToast = useCallback((message: string) => {
    const id = Date.now();
    setToast({ message, id });
    window.setTimeout(() => {
      setToast((prev) => (prev?.id === id ? null : prev));
    }, 3000);
  }, []);

  const handleReindex = useCallback(async () => {
    setIsReindexing(true);
    setReindexError(null);
    try {
      const result = await rebuildWikiIndex();
      if (!result.ok) {
        throw new Error(result.error ?? t("wiki_health.reindex_failed"));
      }
      await Promise.all([healthQuery.refetch(), treeQuery.refetch()]);
    } catch (error) {
      setReindexError(error instanceof Error ? error.message : t("wiki_health.reindex_failed"));
      showToast(t("wiki_health.reindex_failed"));
    } finally {
      setIsReindexing(false);
    }
  }, [healthQuery, showToast, t, treeQuery]);

  const handleSelect = useCallback(
    (slug: string) => {
      if (itemsBySlug.size > 0 && !itemsBySlug.has(slug)) {
        showToast(t("wiki_view.page_not_found"));
        return;
      }
      setIsGraphExpanded(false);
      // Selecting the already-open page from the graph does not change the
      // slug, so the selectedSlug effect cannot switch tabs in that case.
      setCentreTab("page");
      setSelectedSlug(slug);
    },
    [itemsBySlug, showToast, t],
  );

  const closePage = useCallback(() => {
    setSelectedSlug(null);
    setCentreTab("graph");
  }, []);

  const health = healthQuery.data;
  const healthVisual = health ? classifyWikiHealth(health) : "unknown";
  const curated = stats?.last_curator_run
    ? relativeAge(Date.parse(stats.last_curator_run) / 1000, language)
    : "";

  const subtitle = treeQuery.isLoading
    ? t("wiki_view.loading_vault")
    : totalPages === 0
      ? t("wiki_view.vault_empty")
      : [
          t("wiki_ui.subtitle_pages").replace("{0}", formatCount(totalPages, language)),
          t("wiki_ui.subtitle_links").replace("{0}", formatCount(totalLinks, language)),
          curated ? t("wiki_ui.subtitle_curated").replace("{0}", curated) : "",
        ]
          .filter(Boolean)
          .join(" · ");

  const selectedTitle = selectedSlug
    ? itemsBySlug.get(selectedSlug)?.title ?? cleanTitle(selectedSlug)
    : "";

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="wiki-view">
      <ViewHeader
        icon={<BookOpen className="h-4 w-4" />}
        title={t("wiki_ui.title")}
        subtitle={subtitle}
        right={
          <>
            <button
              type="button"
              onClick={() => searchRef.current?.open()}
              data-testid="wiki-search-trigger"
              className="flex h-8 w-60 items-center gap-2 rounded-md border border-border bg-background px-2.5 text-sm text-foreground-faint transition-colors hover:border-border-strong hover:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Search className="h-3.5 w-3.5 shrink-0" aria-hidden />
              <span className="flex-1 truncate text-left">{t("wiki_ui.search_placeholder")}</span>
              <kbd className="rounded-sm border border-border px-1.5 font-mono text-xs leading-5">
                {IS_MAC ? "⌘K" : "Ctrl K"}
              </kbd>
            </button>
            <button
              type="button"
              onClick={closePage}
              data-testid="wiki-health-chip"
              data-visual={healthQuery.isLoading ? "loading" : healthVisual}
              title={t("wiki_ui.health_chip_title")}
              className="inline-flex h-8 items-center gap-2 rounded-md px-2.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            >
              <span
                className={cn(
                  "h-2 w-2 rounded-full",
                  healthQuery.isLoading ? "animate-pulse bg-faint-foreground" : HEALTH_DOT_STYLE[healthVisual],
                )}
                aria-hidden
              />
              {healthQuery.isLoading ? t("wiki_health.checking") : t(HEALTH_LABEL_KEY[healthVisual])}
            </button>
            <ObsidianStatus
              onOpenSetup={(s) => {
                setSetupHint(s);
                setDialogOpen(true);
              }}
            />
          </>
        }
      />

      {dialogOpen && setupHint && (
        <ObsidianSetupDialog
          open={dialogOpen}
          onClose={() => setDialogOpen(false)}
          initialStatus={setupHint}
          onComplete={async () => {
            // Only when the user explicitly confirms that setup worked;
            // never on Escape or an outside click. A failed mark only means
            // the wizard reopens on the next visit, so it is logged, not shown.
            try {
              await fetch("/api/setup/state/obsidian-seen", { method: "POST" });
            } catch (err) {
              console.debug("[WikiView] mark-obsidian-seen failed:", err);
            }
          }}
        />
      )}

      <WikiSearch ref={searchRef} onResultClick={handleSelect} />

      {treeQuery.isError ? (
        <div className="flex flex-1 items-center justify-center border-t border-border p-6">
          <p role="alert" className="max-w-reading text-base text-destructive" data-testid="wiki-tree-error">
            {t("wiki_view.load_error")}
          </p>
        </div>
      ) : !treeQuery.isLoading && totalPages === 0 ? (
        // An empty vault can itself be a symptom (a failed bootstrap writes
        // nothing), so the health rail stays beside the empty state.
        <div className="flex min-h-0 flex-1 overflow-hidden border-t border-border">
          <div className="flex min-w-0 flex-1 items-center justify-center p-6">
            <WikiEmptyState />
          </div>
          <WikiInspector
            selectedSlug={null}
            itemsBySlug={itemsBySlug}
            health={health}
            healthLoading={healthQuery.isLoading}
            isReindexing={isReindexing}
            reindexError={reindexError}
            onReindex={handleReindex}
            onSelect={handleSelect}
          />
        </div>
      ) : (
        <div
          id="wiki-workspace"
          className={cn(
            "flex min-h-0 flex-1 overflow-hidden border-t border-border",
            // Expanded means the whole window: the map is the one thing in
            // this app that gets better the more room it has.
            isGraphExpanded && "fixed inset-0 z-[100] border-t-0 bg-background",
          )}
          data-testid="wiki-workspace"
          data-graph-expanded={isGraphExpanded ? "true" : "false"}
        >
          {!isGraphExpanded && (
            <WikiLibrary
              folders={folders}
              isLoading={treeQuery.isLoading}
              isError={treeQuery.isError}
              selectedSlug={selectedSlug}
              onSelect={handleSelect}
            />
          )}

          <section className="flex min-w-0 flex-1 flex-col" aria-label={t("wiki_ui.stage_label")}>
            <div className="flex h-11 shrink-0 items-stretch gap-1 border-b border-border px-3">
              <StageTab
                active={centreTab === "graph"}
                onClick={() => setCentreTab("graph")}
                icon={<Network className="h-3.5 w-3.5" aria-hidden />}
                label={t("wiki_ui.tab_map")}
                testId="wiki-tab-graph"
              />
              {selectedSlug && (
                <StageTab
                  active={centreTab === "page"}
                  onClick={() => {
                    setCentreTab("page");
                    setIsGraphExpanded(false);
                  }}
                  label={selectedTitle}
                  testId="wiki-tab-page"
                  onClose={closePage}
                  closeLabel={t("wiki_ui.close_page")}
                />
              )}
              {centreTab === "graph" && (
                <button
                  type="button"
                  className="ml-auto inline-flex h-8 items-center gap-1.5 self-center rounded-md px-2.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => setIsGraphExpanded((expanded) => !expanded)}
                  aria-controls="wiki-workspace"
                  aria-expanded={isGraphExpanded}
                  aria-label={t(
                    isGraphExpanded ? "wiki_graph.restore_view_title" : "wiki_graph.expand_view_title",
                  )}
                  title={t(
                    isGraphExpanded ? "wiki_graph.restore_view_title" : "wiki_graph.expand_view_title",
                  )}
                  data-testid="wiki-graph-expand-toggle"
                >
                  {isGraphExpanded ? (
                    <Minimize2 className="h-3.5 w-3.5" aria-hidden />
                  ) : (
                    <Maximize2 className="h-3.5 w-3.5" aria-hidden />
                  )}
                  <span>{t(isGraphExpanded ? "wiki_graph.restore" : "wiki_graph.expand")}</span>
                </button>
              )}
            </div>

            <div
              className={cn(
                "relative min-h-0 flex-1",
                centreTab === "page" ? "overflow-y-auto" : "overflow-hidden",
              )}
            >
              {centreTab === "graph" && (
                <Suspense fallback={<GraphSkeleton />}>
                  <WikiGraph onNodeClick={handleSelect} highlightSlug={selectedSlug ?? undefined} />
                </Suspense>
              )}
              {centreTab === "page" && selectedSlug && (
                <PageRenderer key={selectedSlug} slug={selectedSlug} onWikilinkClick={handleSelect} />
              )}
            </div>
          </section>

          {!isGraphExpanded && (
            <WikiInspector
              selectedSlug={selectedSlug}
              itemsBySlug={itemsBySlug}
              health={health}
              healthLoading={healthQuery.isLoading}
              isReindexing={isReindexing}
              reindexError={reindexError}
              onReindex={handleReindex}
              onSelect={handleSelect}
            />
          )}
        </div>
      )}

      {toast && (
        <div
          className="pointer-events-none fixed bottom-12 right-6 z-[110] max-w-sm rounded-lg bg-popover px-4 py-3 text-base text-foreground shadow-float"
          data-testid="wiki-toast"
          role="status"
        >
          {toast.message}
        </div>
      )}
    </div>
  );
}

function formatCount(value: number, language: string): string {
  try {
    return new Intl.NumberFormat(language).format(value);
  } catch {
    return String(value);
  }
}

function StageTab({
  active,
  onClick,
  icon,
  label,
  testId,
  onClose,
  closeLabel,
}: {
  active: boolean;
  onClick: () => void;
  icon?: React.ReactNode;
  label: string;
  testId: string;
  onClose?: () => void;
  closeLabel?: string;
}) {
  return (
    <div
      className={cn(
        "relative -mb-px flex min-w-0 items-center border-b-2 transition-colors",
        active ? "border-accent" : "border-transparent",
      )}
    >
      <button
        type="button"
        onClick={onClick}
        aria-current={active ? "page" : undefined}
        data-active={active ? "true" : "false"}
        data-testid={testId}
        className={cn(
          "flex h-full min-w-0 max-w-[280px] items-center gap-2 px-2 text-base font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
          active ? "text-foreground-strong" : "text-muted-foreground hover:text-foreground",
        )}
      >
        {icon}
        <span className="truncate">{label}</span>
      </button>
      {onClose && (
        <button
          type="button"
          onClick={onClose}
          aria-label={closeLabel}
          title={closeLabel}
          data-testid={`${testId}-close`}
          className="mr-1 flex h-5 w-5 shrink-0 items-center justify-center rounded-sm text-foreground-faint transition-colors hover:bg-secondary hover:text-foreground"
        >
          <X className="h-3 w-3" aria-hidden />
        </button>
      )}
    </div>
  );
}

function WikiEmptyState() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  return (
    <div data-testid="wiki-empty-state">
      <EmptyState
        icon={<BookOpen />}
        title={t("wiki_view.empty_title")}
        description={`${t("wiki_view.empty_body_a")} ${assistantName} ${t("wiki_view.empty_body_b")}`}
      />
      <p className="mx-auto -mt-6 max-w-[420px] text-center text-sm text-foreground-faint">
        {t("wiki_view.manual_a")}{" "}
        <code className="rounded-sm bg-secondary px-1 py-0.5 font-mono text-xs text-foreground">.md</code>
        {t("wiki_view.manual_b")}{" "}
        <code className="rounded-sm bg-secondary px-1 py-0.5 font-mono text-xs text-foreground">
          wiki/obsidian-vault/entities/
        </code>{" "}
        {t("wiki_view.manual_c")}
      </p>
    </div>
  );
}

function GraphSkeleton() {
  return (
    <div className="flex h-full items-center justify-center p-6" data-testid="wiki-graph-skeleton">
      <div className="h-24 w-24 animate-pulse rounded-full bg-foreground/10" />
    </div>
  );
}
