/**
 * The Wiki section's right rail.
 *
 * With no page open it answers "is my memory working, and what is in it":
 * the subsystem's health in one sentence, the last day's capture as a flow
 * from conversations reviewed to facts written, and the pages everything
 * else hangs off. With a page open it answers "where does this page sit":
 * what links to it, what it links to, and its vital facts.
 *
 * "Honest, not silent" still holds — a failed bootstrap, a failed write, a
 * backlog or a stale index is stated here in words, with the fix beside it.
 */
import { useMemo, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowUpRight, FileText, RefreshCw } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { useRunLocale } from "@/components/runs/format";
import {
  fetchWikiBacklinks,
  fetchWikiGraph,
  fetchWikiPage,
  type WikiCaptureFunnel,
  type WikiHealthSnapshot,
} from "@/lib/wikiApi";
import {
  GROUP_LABEL_KEY,
  cleanTitle,
  degreeOf,
  groupOfKind,
  readableSnippet,
  relativeAge,
  uniqueEdges,
  type LibraryItem,
} from "@/lib/wikiModel";
import { KindGlyph } from "@/components/wiki/KindGlyph";
import { WIKI_INSPECTOR_ID } from "@/store/wikiPanel";

export type WikiHealthVisual = "green" | "amber" | "red" | "unknown";

// The three status hues and nothing else. "unknown" is the ONLY state allowed
// to be neutral — an "ok" that renders dimmer than an "unknown" inverts the ramp.
export const HEALTH_DOT_STYLE: Record<WikiHealthVisual, string> = {
  green: "bg-success",
  amber: "bg-warning",
  red: "bg-destructive",
  unknown: "bg-faint-foreground",
};

export const HEALTH_LABEL_KEY: Record<WikiHealthVisual, string> = {
  green: "wiki_ui.health_green",
  amber: "wiki_ui.health_amber",
  red: "wiki_ui.health_red",
  unknown: "wiki_ui.health_unknown",
};

export function classifyWikiHealth(health: WikiHealthSnapshot): WikiHealthVisual {
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
  // `last_write?.ok === false` and `last_chain_failure` are both ruled out
  // above, so the remaining green condition collapses to `bootstrap_ok`.
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
    return t("wiki_health.chain_failure").replace("{0}", health.last_chain_failure.detail);
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
    return t("wiki_health.pending_writes").replace("{0}", String(health.journal_backlog));
  }
  return t("wiki_health.no_writes_yet");
}

export interface WikiInspectorProps {
  selectedSlug: string | null;
  itemsBySlug: ReadonlyMap<string, LibraryItem>;
  health: WikiHealthSnapshot | null | undefined;
  healthLoading: boolean;
  isReindexing: boolean;
  reindexError: string | null;
  onReindex: () => void;
  onSelect: (slug: string) => void;
}

export function WikiInspector(props: WikiInspectorProps) {
  const t = useT();
  const onPage = Boolean(props.selectedSlug);
  const HeadIcon = onPage ? FileText : Activity;
  // The Agentic IDE's side panel, in the Wiki: one step lighter than the
  // stage, a rule on its left edge, and a 44 px head that lines up with the
  // stage's own tab bar so the two read as one row.
  return (
    <aside
      id={WIKI_INSPECTOR_ID}
      className="flex h-full min-h-0 w-[300px] shrink-0 flex-col border-l border-border bg-card/40"
      data-testid={onPage ? "wiki-inspector-page" : "wiki-backlinks-placeholder"}
      aria-label={t("wiki_ui.inspector_label")}
    >
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border px-4 text-sm font-medium text-foreground">
        <HeadIcon className="h-4 w-4 text-muted-foreground" aria-hidden />
        {t(onPage ? "wiki_ui.inspector_page" : "wiki_ui.inspector_overview")}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {props.selectedSlug ? (
          <PageContext
            slug={props.selectedSlug}
            itemsBySlug={props.itemsBySlug}
            onSelect={props.onSelect}
          />
        ) : (
          <Overview {...props} />
        )}
      </div>
    </aside>
  );
}

/* ------------------------------------------------------------------------ */
/* Overview                                                                  */
/* ------------------------------------------------------------------------ */

function Overview({
  health,
  healthLoading,
  isReindexing,
  reindexError,
  onReindex,
  onSelect,
  itemsBySlug,
}: WikiInspectorProps) {
  return (
    <div className="flex flex-col">
      <HealthBlock
        health={health}
        isLoading={healthLoading}
        isReindexing={isReindexing}
        reindexError={reindexError}
        onReindex={onReindex}
      />
      {health?.capture_funnel && (
        <CaptureFlow funnel={health.capture_funnel} error={health.capture_error} />
      )}
      <HubsBlock onSelect={onSelect} itemsBySlug={itemsBySlug} />
    </div>
  );
}

function Block({
  title,
  aside,
  children,
  testId,
  ariaLabel,
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
  testId?: string;
  ariaLabel?: string;
}) {
  return (
    <section
      className="border-b border-border px-5 py-5 last:border-b-0"
      data-testid={testId}
      aria-label={ariaLabel}
    >
      <header className="mb-3 flex items-baseline justify-between gap-3">
        <h2 className="text-base font-semibold text-foreground-strong">{title}</h2>
        {aside && <div className="shrink-0 whitespace-nowrap text-sm text-foreground-faint">{aside}</div>}
      </header>
      {children}
    </section>
  );
}

function HealthBlock({
  health,
  isLoading,
  isReindexing,
  reindexError,
  onReindex,
}: {
  health: WikiHealthSnapshot | null | undefined;
  isLoading: boolean;
  isReindexing: boolean;
  reindexError: string | null;
  onReindex: () => void;
}) {
  const t = useT();

  if (isLoading) {
    return (
      <Block title={t("wiki_ui.health_title")} testId="wiki-health-strip">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span
            className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-faint-foreground"
            data-testid="wiki-health-dot"
            data-visual="loading"
            aria-hidden
          />
          <span data-testid="wiki-health-checking">{t("wiki_health.checking")}</span>
        </div>
      </Block>
    );
  }

  if (!health) {
    return (
      <Block title={t("wiki_ui.health_title")} testId="wiki-health-strip">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span
            className="h-2 w-2 shrink-0 rounded-full bg-faint-foreground"
            data-testid="wiki-health-dot"
            data-visual="unknown"
            aria-hidden
          />
          <span data-testid="wiki-health-unavailable">{t("wiki_health.unavailable")}</span>
        </div>
      </Block>
    );
  }

  const visual = classifyWikiHealth(health);
  const vaultText = health.vault_root
    ? t("wiki_health.vault_prefix").replace("{0}", health.vault_root)
    : t("wiki_health.vault_unknown");

  return (
    <Block title={t("wiki_ui.health_title")} testId="wiki-health-strip">
      <div className="flex items-center gap-2">
        <span className="relative flex h-2 w-2 shrink-0" aria-hidden>
          {visual === "green" && (
            <span className="absolute inset-0 animate-ping rounded-full bg-success opacity-40 motion-reduce:hidden" />
          )}
          <span
            className={cn("relative h-2 w-2 rounded-full", HEALTH_DOT_STYLE[visual])}
            data-testid="wiki-health-dot"
            data-visual={visual}
          />
        </span>
        <span className="text-base font-medium text-foreground">{t(HEALTH_LABEL_KEY[visual])}</span>
      </div>
      <p
        data-testid="wiki-health-write"
        className={cn(
          "mt-2 break-words text-sm",
          visual === "red" ? "text-destructive" : "text-muted-foreground",
        )}
      >
        {describeWikiWriteStatus(health, t)}
      </p>

      {(health.journal_backlog > 0 ||
        health.index_state === "stale" ||
        health.vault_legacy_conflict) && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          {health.journal_backlog > 0 && (
            <Badge testId="wiki-health-backlog">
              {t("wiki_health.backlog_count").replace("{0}", String(health.journal_backlog))}
            </Badge>
          )}
          {health.index_state === "stale" && (
            <>
              <Badge testId="wiki-health-index-stale">
                {t("wiki_health.index_stale")
                  .replace("{0}", String(health.indexed_pages))
                  .replace("{1}", String(health.vault_pages))}
              </Badge>
              <button
                type="button"
                onClick={onReindex}
                disabled={isReindexing}
                data-testid="wiki-health-reindex"
                className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border-strong px-2.5 text-sm font-medium text-foreground transition-colors hover:bg-secondary disabled:opacity-50"
              >
                <RefreshCw className={cn("h-3 w-3", isReindexing && "animate-spin")} aria-hidden />
                {t(isReindexing ? "wiki_health.reindexing" : "wiki_health.reindex")}
              </button>
            </>
          )}
          {health.vault_legacy_conflict && (
            <Badge testId="wiki-health-legacy-conflict">{t("wiki_health.legacy_conflict")}</Badge>
          )}
        </div>
      )}
      {reindexError && (
        <p role="alert" data-testid="wiki-health-reindex-error" className="mt-2 text-sm text-destructive">
          {t("wiki_health.reindex_failed_detail").replace("{0}", reindexError)}
        </p>
      )}

      <p
        data-testid="wiki-health-vault"
        className="mt-3 truncate font-mono text-xs text-foreground-faint"
        title={vaultText}
      >
        {vaultText}
      </p>
    </Block>
  );
}

function Badge({ children, testId }: { children: ReactNode; testId: string }) {
  return (
    <span
      data-testid={testId}
      className="inline-flex h-6 items-center rounded-sm bg-warning/10 px-2 text-xs font-medium text-warning"
    >
      {children}
    </span>
  );
}

/**
 * The last day's capture as a flow: each stage a bar measured against the
 * conversations reviewed, so the drop-off between "looked at" and "written"
 * is visible at a glance. The quieter outcomes follow as a ledger.
 */
function CaptureFlow({
  funnel,
  error,
}: {
  funnel: WikiCaptureFunnel;
  error: string | null | undefined;
}) {
  const t = useT();
  const windowHours = Math.max(1, Math.round(funnel.window_hours));
  const stages = [
    ["reviewed", t("wiki_health.capture_reviewed"), funnel.total],
    ["candidate-reviews", t("wiki_health.capture_candidate_reviews"), funnel.candidates],
    ["candidate-facts", t("wiki_health.capture_candidate_facts"), funnel.facts],
    ["writes", t("wiki_health.capture_writes"), funnel.writes],
  ] as const;
  const ledger = [
    ["noop", t("wiki_health.capture_noop"), funnel.stage2_noop],
    ["rejected", t("wiki_health.capture_rejected"), funnel.stage2_rejected],
    ["skipped", t("wiki_health.capture_skipped"), funnel.stage2_skipped],
    ["pending", t("wiki_health.capture_pending"), funnel.stage2_pending],
    ["filtered", t("wiki_health.capture_filtered"), funnel.filtered],
    ["empty", t("wiki_health.capture_empty"), funnel.empty],
    ["failed", t("wiki_health.capture_failed"), funnel.failed],
    ["in-progress", t("wiki_health.capture_in_progress"), funnel.started],
    ["session-sweeps", t("wiki_health.capture_session_sweeps"), funnel.sessions_swept],
  ] as const;
  const scale = Math.max(1, ...stages.map(([, , value]) => value));
  const errorText =
    error === "capture_store_unavailable"
      ? t("wiki_health.capture_store_unavailable")
      : error
        ? t("wiki_health.capture_store_error").replace("{0}", error)
        : null;
  const quiet = !error && funnel.total === 0 && funnel.started === 0;

  return (
    <Block
      title={t("wiki_ui.capture_title")}
      aside={t("wiki_ui.capture_window").replace("{0}", String(windowHours))}
      testId="wiki-capture-funnel"
      ariaLabel={t("wiki_health.capture_aria").replace("{0}", String(windowHours))}
    >
      {errorText && (
        <p className="mb-3 text-sm text-destructive" data-testid="wiki-capture-error" role="alert">
          {errorText}
        </p>
      )}
      {!error && funnel.failed > 0 && (
        <p className="mb-3 text-sm text-destructive" data-testid="wiki-capture-failed-detail" role="status">
          {t("wiki_health.capture_failed_detail").replace("{0}", String(funnel.failed))}
        </p>
      )}
      {quiet && (
        <p className="mb-3 text-sm text-muted-foreground" data-testid="wiki-capture-quiet">
          {t("wiki_ui.capture_quiet")}
        </p>
      )}

      <ol className="space-y-2.5">
        {stages.map(([key, label, value], index) => {
          const last = index === stages.length - 1;
          return (
            <li key={key} data-testid={`wiki-capture-${key}`}>
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="text-muted-foreground">{label}</span>
                <span className="tabular-nums text-foreground">{value}</span>
              </div>
              <div className="mt-1 h-1 overflow-hidden rounded-full bg-foreground/10">
                <div
                  className={cn(
                    "h-full rounded-full transition-[width] duration-500",
                    last ? "bg-accent" : "bg-foreground/45",
                  )}
                  style={{ width: `${value > 0 ? Math.max(2, (value / scale) * 100) : 0}%` }}
                />
              </div>
            </li>
          );
        })}
      </ol>

      <dl className="mt-4 space-y-1 border-t border-border pt-3 text-sm">
        {ledger.map(([key, label, value]) => {
          const alarm = key === "failed" && value > 0;
          return (
            <div
              key={key}
              className={cn("flex items-baseline gap-2", alarm && "text-destructive")}
              data-testid={`wiki-capture-${key}`}
            >
              <dt className={alarm ? undefined : "text-muted-foreground"}>{label}</dt>
              <span aria-hidden className="mb-1 min-w-4 flex-1 border-b border-dotted border-border-strong" />
              <dd className={cn("tabular-nums", alarm ? "text-destructive" : value > 0 ? "text-foreground" : "text-foreground-faint")}>
                {value}
              </dd>
            </div>
          );
        })}
      </dl>
    </Block>
  );
}

/** The pages most others hang off — the vault's load-bearing pages. */
function HubsBlock({
  onSelect,
  itemsBySlug,
}: {
  onSelect: (slug: string) => void;
  itemsBySlug: ReadonlyMap<string, LibraryItem>;
}) {
  const t = useT();
  const { data, isLoading } = useQuery({
    queryKey: ["wiki", "graph"],
    queryFn: fetchWikiGraph,
    staleTime: 30_000,
  });

  const hubs = useMemo(() => {
    if (!data?.ok || !data.nodes) return [];
    const degree = degreeOf(uniqueEdges(data.edges ?? []));
    return data.nodes
      .map((node) => ({
        slug: node.id,
        title: cleanTitle(node.title, node.id),
        group: groupOfKind(node.kind),
        degree: degree.get(node.id) ?? 0,
      }))
      .filter((node) => node.degree > 0)
      .sort((a, b) => b.degree - a.degree)
      .slice(0, 6);
  }, [data]);

  if (!isLoading && hubs.length === 0) return null;
  const top = hubs[0]?.degree ?? 1;

  return (
    <Block title={t("wiki_ui.hubs_title")} testId="wiki-inspector-hubs">
      {isLoading ? (
        <div className="space-y-2" aria-busy="true">
          {[80, 64, 52].map((w) => (
            <div key={w} className="h-4 animate-pulse rounded-sm bg-foreground/10" style={{ width: `${w}%` }} />
          ))}
        </div>
      ) : (
        <ul className="-mx-2 space-y-px">
          {hubs.map((hub) => (
            <li key={hub.slug}>
              <button
                type="button"
                onClick={() => onSelect(hub.slug)}
                className="group flex w-full flex-col gap-1 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-secondary"
                data-testid="wiki-hub-item"
              >
                <span className="flex w-full items-center gap-2 text-sm">
                  <KindGlyph group={itemsBySlug.get(hub.slug)?.group ?? hub.group} className="h-2.5 w-2.5 text-foreground-faint" />
                  <span className="min-w-0 flex-1 truncate text-foreground">{hub.title}</span>
                  <span className="tabular-nums text-foreground-faint">{hub.degree}</span>
                </span>
                <span className="ml-[18px] block h-0.5 overflow-hidden rounded-full bg-foreground/[0.06]">
                  <span
                    className="block h-full rounded-full bg-foreground/30 transition-colors group-hover:bg-accent"
                    style={{ width: `${(hub.degree / top) * 100}%` }}
                  />
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Block>
  );
}

/* ------------------------------------------------------------------------ */
/* Page context                                                              */
/* ------------------------------------------------------------------------ */

function PageContext({
  slug,
  itemsBySlug,
  onSelect,
}: {
  slug: string;
  itemsBySlug: ReadonlyMap<string, LibraryItem>;
  onSelect: (slug: string) => void;
}) {
  const t = useT();
  const language = useRunLocale();
  const backlinksQuery = useQuery({
    queryKey: ["wiki", "backlinks", slug],
    queryFn: () => fetchWikiBacklinks(slug),
    staleTime: 5_000,
  });
  const pageQuery = useQuery({
    queryKey: ["wiki", "page", slug],
    queryFn: () => fetchWikiPage(slug),
    staleTime: 5_000,
  });

  const backlinks = (backlinksQuery.data?.backlinks ?? []).filter((bl) => bl.slug !== slug);
  const outgoing = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const link of pageQuery.data?.wikilinks ?? []) {
      if (!link || link === slug || seen.has(link)) continue;
      seen.add(link);
      out.push(link);
    }
    return out;
  }, [pageQuery.data?.wikilinks, slug]);

  const page = pageQuery.data?.ok ? pageQuery.data : null;
  const item = itemsBySlug.get(slug);
  const facts: Array<[string, string]> = [];
  if (page) {
    facts.push([t("wiki_ui.fact_kind"), t(GROUP_LABEL_KEY[item?.group ?? groupOfKind(page.kind)])]);
    if (item?.folder) facts.push([t("wiki_ui.fact_folder"), item.folder]);
    if (page.stats?.words !== undefined)
      facts.push([t("wiki_ui.fact_words"), new Intl.NumberFormat(language).format(page.stats.words)]);
    if (page.stats?.mtime) facts.push([t("wiki_ui.fact_changed"), relativeAge(page.stats.mtime, language)]);
  }

  return (
    <div className="flex flex-col">
      <Block
        title={t("wiki_ui.linked_from")}
        aside={backlinksQuery.isLoading ? undefined : String(backlinks.length)}
        testId="wiki-backlinks-panel"
      >
        {backlinksQuery.isLoading && <LineSkeleton />}
        {backlinksQuery.isError && (
          <p role="alert" className="text-sm text-destructive">
            {t("backlinks_panel.load_error")}
          </p>
        )}
        {!backlinksQuery.isLoading && !backlinksQuery.isError && backlinks.length === 0 && (
          <p className="text-sm text-muted-foreground" data-testid="wiki-backlinks-empty">
            {t("wiki_ui.linked_from_empty")}
          </p>
        )}
        {backlinks.length > 0 && (
          <ul className="-mx-2 space-y-px">
            {backlinks.map((bl) => {
              const snippet = bl.snippet ? readableSnippet(bl.snippet) : "";
              return (
                <li key={bl.slug}>
                  <button
                    type="button"
                    onClick={() => onSelect(bl.slug)}
                    className="flex w-full items-start gap-2.5 rounded-md px-2 py-2 text-left transition-colors hover:bg-secondary"
                    data-testid="wiki-backlink-item"
                    data-target-slug={bl.slug}
                  >
                    <KindGlyph
                      group={itemsBySlug.get(bl.slug)?.group ?? "other"}
                      className="mt-1 h-2.5 w-2.5 text-foreground-faint"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm text-foreground">
                        {cleanTitle(bl.title, bl.slug)}
                      </span>
                      {snippet && (
                        <span className="mt-0.5 line-clamp-2 block text-xs leading-4 text-muted-foreground">
                          {snippet}
                        </span>
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </Block>

      <Block
        title={t("wiki_ui.links_to")}
        aside={pageQuery.isLoading ? undefined : String(outgoing.length)}
        testId="wiki-outgoing-panel"
      >
        {pageQuery.isLoading && <LineSkeleton />}
        {!pageQuery.isLoading && outgoing.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("wiki_ui.links_to_empty")}</p>
        )}
        {outgoing.length > 0 && (
          <ul className="-mx-2 space-y-px">
            {outgoing.map((target) => {
              const known = itemsBySlug.get(target);
              return (
                <li key={target}>
                  {known ? (
                    <button
                      type="button"
                      onClick={() => onSelect(target)}
                      className="group flex h-8 w-full items-center gap-2.5 rounded-md px-2 text-left text-sm text-foreground transition-colors hover:bg-secondary"
                      data-testid="wiki-outgoing-item"
                    >
                      <KindGlyph group={known.group} className="h-2.5 w-2.5 text-foreground-faint" />
                      <span className="min-w-0 flex-1 truncate">{known.title}</span>
                      <ArrowUpRight className="h-3 w-3 shrink-0 text-foreground-faint opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />
                    </button>
                  ) : (
                    <span
                      className="flex h-8 items-center gap-2.5 px-2 text-sm text-foreground-faint"
                      title={t("wiki_ui.link_missing")}
                      data-testid="wiki-outgoing-missing"
                    >
                      <span className="h-2.5 w-2.5 shrink-0 rounded-full border border-dashed border-current" aria-hidden />
                      <span className="min-w-0 flex-1 truncate line-through decoration-foreground-faint/60">{target}</span>
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Block>

      {facts.length > 0 && (
        <Block title={t("wiki_ui.facts_title")} testId="wiki-page-facts">
          <dl className="space-y-1.5 text-sm">
            {facts.map(([label, value]) => (
              <div key={label} className="flex items-baseline gap-2">
                <dt className="text-muted-foreground">{label}</dt>
                <span aria-hidden className="mb-1 min-w-4 flex-1 border-b border-dotted border-border-strong" />
                <dd className="max-w-[60%] truncate text-foreground" title={value}>
                  {value}
                </dd>
              </div>
            ))}
          </dl>
        </Block>
      )}
    </div>
  );
}

function LineSkeleton() {
  return (
    <div className="space-y-2" aria-busy="true">
      <div className="h-4 w-3/4 animate-pulse rounded-sm bg-foreground/10" />
      <div className="h-4 w-1/2 animate-pulse rounded-sm bg-foreground/10" />
    </div>
  );
}
