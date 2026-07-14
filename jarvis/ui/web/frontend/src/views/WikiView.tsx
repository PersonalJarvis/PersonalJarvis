/**
 * Desktop view of the on-disk Obsidian vault.
 *
 * Read-only — writes happen via the WikiCurator (B1) or the user editing
 * Markdown files in Obsidian. This component is a pure projection of
 * `wiki/obsidian-vault/` exposed through Agent A's `/api/wiki/*` endpoints.
 *
 * Layout (matches docs/plans/b3/00-OVERVIEW.md §4.1):
 *   ┌──────────┬──────────────────┬────────────┐
 *   │   tree   │  graph | page    │ backlinks  │
 *   │ (260 px) │  (centre tabs)   │  (380 px)  │
 *   └──────────┴──────────────────┴────────────┘
 *
 * Replaces the legacy `MemoryView` (`data/core_memory.json` flat memory).
 */
import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { FileText, Network, Notebook, RefreshCw } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { ViewHeader } from "@/views/ChatsView";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import {
  fetchWikiHealth,
  fetchWikiTree,
  rebuildWikiIndex,
  type WikiHealthSnapshot,
} from "@/lib/wikiApi";

import { TreeSidebar } from "@/components/wiki/TreeSidebar";
import { PageRenderer } from "@/components/wiki/PageRenderer";
import { BacklinksPanel } from "@/components/wiki/BacklinksPanel";
import { ObsidianStatus } from "@/components/wiki/ObsidianStatus";
import { ObsidianSetupDialog } from "@/components/wiki/ObsidianSetupDialog";
import type { ObsidianStatus as ObsidianStatusType } from "@/types/setup";

// Agent C owns WikiGraph. Lazy import so the graph bundle (~120 KB minified)
// only loads when the Wiki tab is mounted. A placeholder file ships in this
// branch — Agent C's real implementation will replace it during Wave 2.
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

export function WikiView(): JSX.Element {
  const t = useT();
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [centreTab, setCentreTab] = useState<CentreTab>("graph");
  const [toast, setToast] = useState<WikiToast | null>(null);
  // Sub-Agent 5: the setup walkthrough opens with the status payload the
  // pill last saw. The hint object also reseeds whenever the user reopens
  // the dialog so step-2-vs-step-3 starts from the most recent reality.
  const [setupHint, setSetupHint] = useState<ObsidianStatusType | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [isReindexing, setIsReindexing] = useState(false);

  // Tree query lives both here (for header stats + empty-state detection)
  // and inside TreeSidebar (for the list). React Query dedupes them.
  const treeQuery = useQuery({
    queryKey: ["wiki", "tree"],
    queryFn: fetchWikiTree,
    staleTime: 5_000,
  });

  const stats = treeQuery.data?.stats;
  const totalPages = stats?.total_pages ?? 0;
  const totalLinks = stats?.total_links ?? 0;

  // Wiki subsystem health (spec A5): polled on mount + every 30 s so the
  // "honest, not silent" status strip stays live without a manual refresh.
  const healthQuery = useQuery({
    queryKey: ["wiki", "health"],
    queryFn: fetchWikiHealth,
    refetchInterval: 30_000,
    staleTime: 5_000,
  });

  // When a slug is selected (via tree click, graph click, or wikilink),
  // automatically swap to the page tab.
  useEffect(() => {
    if (selectedSlug) setCentreTab("page");
  }, [selectedSlug]);

  // Sub-Agent 6: on first ever visit to the Wiki tab, auto-open the
  // Obsidian setup walkthrough — but only if the user has never marked
  // it as completed AND the current status says action is required.
  // Both requests run in parallel; AbortController cancels them if the
  // component unmounts before the network round trip finishes.
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
        if (
          state.obsidian_setup_seen === false &&
          status.recommended_action !== "ok"
        ) {
          setSetupHint(status);
          setDialogOpen(true);
        }
      } catch (err) {
        // AbortError is expected on unmount; everything else we silently
        // swallow — the status pill still gives the user a manual entry.
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
    try {
      const result = await rebuildWikiIndex();
      if (!result.ok) throw new Error(result.error ?? "index rebuild failed");
      await Promise.all([healthQuery.refetch(), treeQuery.refetch()]);
    } catch {
      showToast(t("wiki_health.reindex_failed"));
    } finally {
      setIsReindexing(false);
    }
  }, [healthQuery, showToast, t, treeQuery]);

  // Build the known-slug set lazily here too, so we can validate a wikilink
  // click before changing the URL. Single source of truth: the tree response.
  const knownSlugs = collectSlugs(treeQuery.data?.folders ?? []);

  const handleSelect = useCallback(
    (slug: string) => {
      if (knownSlugs.size > 0 && !knownSlugs.has(slug)) {
        showToast(t("wiki_view.page_not_found"));
        return;
      }
      setSelectedSlug(slug);
    },
    // knownSlugs is recomputed each render; intentional, the Set is small.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [showToast, knownSlugs.size],
  );

  const subtitle = treeQuery.isLoading
    ? t("wiki_view.loading_vault")
    : totalPages === 0
      ? t("wiki_view.vault_empty")
      : `${totalPages} ${t("wiki_view.pages")} · ${totalLinks} ${t("wiki_view.wikilinks")}`;

  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="wiki-view">
      <div className="flex items-start justify-between gap-3 pr-6">
        <div className="min-w-0 flex-1">
          <ViewHeader
            icon={<Notebook className="h-4 w-4" />}
            title="Wiki · Memory Map"
            subtitle={subtitle}
          />
        </div>
        <div className="flex shrink-0 items-center pt-4">
          <ObsidianStatus
            onOpenSetup={(s) => {
              setSetupHint(s);
              setDialogOpen(true);
            }}
          />
        </div>
      </div>

      <WikiHealthStrip
        health={healthQuery.data}
        isLoading={healthQuery.isLoading}
        isReindexing={isReindexing}
        onReindex={handleReindex}
      />

      {dialogOpen && setupHint && (
        <ObsidianSetupDialog
          open={dialogOpen}
          onClose={() => setDialogOpen(false)}
          initialStatus={setupHint}
          onComplete={async () => {
            // Sub-Agent 6: only when the user EXPLICITLY confirms the
            // setup worked ("Hat geklappt"); never on Escape / outside
            // click. Fire-and-forget — the route never 5xx's and a
            // failed mark just means the wizard re-opens next visit.
            try {
              await fetch("/api/setup/state/obsidian-seen", {
                method: "POST",
              });
            } catch (err) {
              console.debug("[WikiView] mark-obsidian-seen failed:", err);
            }
          }}
        />
      )}

      {treeQuery.isError ? (
        <div className="flex flex-1 items-center justify-center p-6">
          <div
            role="alert"
            className="max-w-md rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive"
            data-testid="wiki-tree-error"
          >
            {t("wiki_view.load_error")}
          </div>
        </div>
      ) : !treeQuery.isLoading && totalPages === 0 ? (
        <EmptyState />
      ) : (
        <div className="flex flex-1 min-h-0 overflow-hidden">
          <TreeSidebar
            selectedSlug={selectedSlug}
            onSelect={handleSelect}
          />

          <section className="flex flex-1 min-w-0 flex-col bg-background">
            <div className="flex border-b border-border bg-card/40">
              <TabButton
                active={centreTab === "graph"}
                onClick={() => setCentreTab("graph")}
                icon={<Network className="h-3.5 w-3.5" />}
                label="Memory Map"
              />
              <TabButton
                active={centreTab === "page"}
                onClick={() => setCentreTab("page")}
                icon={<FileText className="h-3.5 w-3.5" />}
                label={
                  selectedSlug
                    ? `Page · ${selectedSlug}.md`
                    : "Page"
                }
                disabled={!selectedSlug}
              />
            </div>

            <div className="flex-1 min-h-0 overflow-y-auto">
              {centreTab === "graph" && (
                <Suspense fallback={<GraphSkeleton />}>
                  <WikiGraph
                    onNodeClick={handleSelect}
                    highlightSlug={selectedSlug ?? undefined}
                  />
                </Suspense>
              )}

              {centreTab === "page" && (
                <>
                  {selectedSlug ? (
                    <PageRenderer
                      slug={selectedSlug}
                      onWikilinkClick={handleSelect}
                    />
                  ) : (
                    <div
                      className="px-7 py-10 text-center text-sm text-muted-foreground"
                      data-testid="wiki-page-no-selection"
                    >
                      {t("wiki_view.no_selection_hint")}
                    </div>
                  )}
                </>
              )}
            </div>
          </section>

          {selectedSlug ? (
            <BacklinksPanel slug={selectedSlug} onSelect={handleSelect} />
          ) : (
            <aside className="flex h-full w-[380px] shrink-0 flex-col border-l border-border bg-card/40 p-4">
              <div className="rounded-lg border border-border bg-secondary/30 p-4 text-xs text-muted-foreground">
                {t("wiki_view.backlinks_hint")}
              </div>
            </aside>
          )}
        </div>
      )}

      {toast && (
        <div
          className="pointer-events-none fixed bottom-12 right-6 z-50 max-w-sm rounded-lg border border-border bg-card px-4 py-3 text-sm text-foreground shadow-xl"
          data-testid="wiki-toast"
          role="status"
        >
          {toast.message}
        </div>
      )}
    </div>
  );
}

type WikiHealthVisual = "green" | "amber" | "red" | "unknown";

const HEALTH_DOT_STYLE: Record<WikiHealthVisual, string> = {
  green: "bg-[#5bd4a4]",
  amber: "bg-[#ffb84d]",
  red: "bg-destructive",
  unknown: "bg-muted-foreground/40",
};

function classifyWikiHealth(health: WikiHealthSnapshot): WikiHealthVisual {
  if (
    health.bootstrap_ok === false ||
    health.last_write?.ok === false ||
    health.last_chain_failure
  ) {
    return "red";
  }
  if (
    health.journal_backlog > 0 ||
    health.vault_legacy_conflict ||
    health.index_state === "stale"
  ) {
    return "amber";
  }
  // At this point `last_write?.ok === false` and `last_chain_failure` are
  // both already ruled out by the guard above, so the remaining green
  // condition collapses to `bootstrap_ok` alone.
  if (health.bootstrap_ok) {
    return "green";
  }
  // bootstrap_ok is null (never run yet) and nothing else flagged a problem —
  // neither a clean pass nor a known failure, so stay neutral rather than
  // claim "green" for a state we haven't actually verified.
  return "unknown";
}

function describeWikiWriteStatus(
  health: WikiHealthSnapshot,
  t: (key: string) => string,
): string {
  if (health.bootstrap_ok === false) {
    return health.bootstrap_error
      ? t("wiki_health.bootstrap_failed").replace("{0}", health.bootstrap_error)
      : t("wiki_health.bootstrap_failed_unknown");
  }
  if (health.last_chain_failure) {
    return t("wiki_health.chain_failure").replace(
      "{0}",
      health.last_chain_failure.detail,
    );
  }
  if (health.last_write?.ok === false) {
    return health.last_write.error
      ? t("wiki_health.last_write_failed").replace("{0}", health.last_write.error)
      : t("wiki_health.last_write_failed_unknown");
  }
  if (health.last_write?.ok) {
    const page = health.last_write.pages.join(", ") || health.last_write.source;
    return t("wiki_health.last_write_ok").replace("{0}", page);
  }
  if (health.journal_backlog > 0) {
    return t("wiki_health.pending_writes").replace(
      "{0}",
      String(health.journal_backlog),
    );
  }
  return t("wiki_health.no_writes_yet");
}

/**
 * Compact status strip at the top of the Wiki tab (spec A5). Polled by the
 * caller on a timer; this component only renders whatever snapshot it was
 * given. "Honest, not silent": a failed bootstrap, a failed write, or a
 * growing journal backlog shows up here instead of failing quietly.
 */
function WikiHealthStrip({
  health,
  isLoading,
  isReindexing,
  onReindex,
}: {
  health: WikiHealthSnapshot | null | undefined;
  isLoading: boolean;
  isReindexing: boolean;
  onReindex: () => void;
}): JSX.Element {
  const t = useT();

  if (isLoading) {
    return (
      <div
        className="flex items-center gap-2 border-b border-border bg-card/30 px-4 py-2 text-xs text-muted-foreground"
        data-testid="wiki-health-strip"
      >
        <span
          className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-muted-foreground/40"
          data-testid="wiki-health-dot"
          data-visual="loading"
          aria-hidden
        />
        <span data-testid="wiki-health-checking">{t("wiki_health.checking")}</span>
      </div>
    );
  }

  if (!health) {
    return (
      <div
        className="flex items-center gap-2 border-b border-border bg-card/30 px-4 py-2 text-xs text-muted-foreground"
        data-testid="wiki-health-strip"
      >
        <span
          className="h-2 w-2 shrink-0 rounded-full bg-muted-foreground/40"
          data-testid="wiki-health-dot"
          data-visual="unknown"
          aria-hidden
        />
        <span data-testid="wiki-health-unavailable">{t("wiki_health.unavailable")}</span>
      </div>
    );
  }

  const visual = classifyWikiHealth(health);
  const vaultText = health.vault_root
    ? t("wiki_health.vault_prefix").replace("{0}", health.vault_root)
    : t("wiki_health.vault_unknown");
  const writeText = describeWikiWriteStatus(health, t);

  return (
    <div
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border bg-card/30 px-4 py-2 text-xs text-muted-foreground"
      data-testid="wiki-health-strip"
    >
      <span
        className={cn("h-2 w-2 shrink-0 rounded-full", HEALTH_DOT_STYLE[visual])}
        data-testid="wiki-health-dot"
        data-visual={visual}
        aria-hidden
      />
      <span data-testid="wiki-health-vault" className="truncate">
        {vaultText}
      </span>
      <span aria-hidden>·</span>
      <span
        data-testid="wiki-health-write"
        className={visual === "red" ? "text-destructive" : undefined}
      >
        {writeText}
      </span>
      {health.journal_backlog > 0 && (
        <span
          data-testid="wiki-health-backlog"
          className="rounded-full border border-[#ffb84d]/40 bg-[#ffb84d]/10 px-1.5 py-0.5 text-[#ffb84d]"
        >
          {t("wiki_health.backlog_count").replace(
            "{0}",
            String(health.journal_backlog),
          )}
        </span>
      )}
      {health.index_state === "stale" && (
        <>
          <span
            data-testid="wiki-health-index-stale"
            className="rounded-full border border-[#ffb84d]/40 bg-[#ffb84d]/10 px-1.5 py-0.5 text-[#ffb84d]"
          >
            {t("wiki_health.index_stale")
              .replace("{0}", String(health.indexed_pages))
              .replace("{1}", String(health.vault_pages))}
          </span>
          <button
            type="button"
            onClick={onReindex}
            disabled={isReindexing}
            data-testid="wiki-health-reindex"
            className="inline-flex items-center gap-1 rounded-md border border-border px-1.5 py-0.5 text-foreground hover:bg-muted disabled:opacity-50"
          >
            <RefreshCw className={cn("h-3 w-3", isReindexing && "animate-spin")} />
            {t(isReindexing ? "wiki_health.reindexing" : "wiki_health.reindex")}
          </button>
        </>
      )}
      {health.vault_legacy_conflict && (
        <span
          data-testid="wiki-health-legacy-conflict"
          className="rounded-full border border-[#ffb84d]/40 bg-[#ffb84d]/10 px-1.5 py-0.5 text-[#ffb84d]"
        >
          {t("wiki_health.legacy_conflict")}
        </span>
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  label,
  disabled,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-xs transition-colors",
        active
          ? "border-primary text-foreground"
          : "border-transparent text-muted-foreground hover:text-foreground",
        disabled && "cursor-not-allowed opacity-50 hover:text-muted-foreground",
      )}
      data-active={active ? "true" : "false"}
      data-testid={`wiki-tab-${label.toLowerCase().replace(/\s+/g, "-")}`}
    >
      {icon}
      {label}
    </button>
  );
}

function EmptyState() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  return (
    <div className="flex flex-1 items-center justify-center p-6">
      <div
        className="max-w-lg rounded-xl border border-dashed border-border/70 bg-card/30 px-8 py-10 text-center"
        data-testid="wiki-empty-state"
      >
        <Notebook className="mx-auto mb-3 h-8 w-8 text-muted-foreground" />
        <h3 className="mb-2 text-base font-semibold text-foreground">
          {t("wiki_view.empty_title")}
        </h3>
        <p className="mb-2 text-sm text-muted-foreground">
          {t("wiki_view.empty_body_a")} {assistantName} {t("wiki_view.empty_body_b")}
        </p>
        <p className="text-sm text-muted-foreground">
          {t("wiki_view.manual_a")}{" "}
          <code className="rounded bg-background px-1 py-0.5 font-mono text-[12px]">
            .md
          </code>
          {t("wiki_view.manual_b")}{" "}
          <code className="rounded bg-background px-1 py-0.5 font-mono text-[12px]">
            wiki/obsidian-vault/entities/
          </code>{" "}
          {t("wiki_view.manual_c")}
        </p>
      </div>
    </div>
  );
}

function GraphSkeleton() {
  return (
    <div
      className="flex h-full min-h-[400px] items-center justify-center p-6"
      data-testid="wiki-graph-skeleton"
    >
      <div className="h-full w-full max-w-3xl animate-pulse rounded-xl bg-muted/30" />
    </div>
  );
}

function collectSlugs(
  folders: Array<{ files: Array<{ slug: string }> }>,
): Set<string> {
  const out = new Set<string>();
  for (const folder of folders) {
    for (const file of folder.files) {
      out.add(file.slug);
    }
  }
  return out;
}
