import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Clock,
  FileText,
  Loader2,
  RefreshCw,
  Search,
} from "lucide-react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import {
  buildDocSections,
  useDocsGrouped,
  type DocNavSummary,
} from "@/hooks/useDocs";
import { useRecentDocs } from "@/hooks/useRecentDocs";
import { useT } from "@/i18n";

interface Props {
  selectedSlug: string | null;
  onSelect: (slug: string) => void;
  onShowOverview: () => void;
  onOpenSearch: () => void;
}

export function DocsSidebar({
  selectedSlug,
  onSelect,
  onShowOverview,
  onOpenSearch,
}: Props) {
  const t = useT();
  const { data, isLoading, isFetching, error, refetch } = useDocsGrouped();
  const { recent } = useRecentDocs();
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const sections = useMemo(() => buildDocSections(data), [data]);

  const filteredSections = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return sections;
    return sections
      .map((section) => ({
        ...section,
        docs: section.docs.filter(
          (d) =>
          d.title.toLowerCase().includes(q) ||
          d.summary.toLowerCase().includes(q) ||
          d.slug.toLowerCase().includes(q) ||
          d.tags.some((t) => t.toLowerCase().includes(q)),
        ),
      }))
      .filter((section) => section.docs.length > 0);
  }, [query, sections]);

  const totalCount = useMemo(() => {
    return filteredSections.reduce((acc, section) => acc + section.docs.length, 0);
  }, [filteredSections]);

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-border bg-card/40">
      {/* Header */}
      <div className="border-b border-border px-3 py-3">
        <div className="mb-2 flex items-center justify-between">
          <button
            type="button"
            onClick={onShowOverview}
            className="rounded-sm text-sm font-semibold tracking-tight transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {t("docs_sidebar.title")}
          </button>
          <button
            type="button"
            onClick={onOpenSearch}
            title={t("docs.fulltext_search")}
            aria-label={t("docs.fulltext_search")}
            className="rounded-md p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
          >
            <Search className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
        <label htmlFor="docs-sidebar-filter" className="sr-only">
          {t("docs.search_placeholder")}
        </label>
        <input
          id="docs-sidebar-filter"
          name="docs-filter"
          type="text"
          placeholder={t("docs.search_placeholder")}
          autoComplete="off"
          value={query}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
            setQuery(e.target.value)
          }
          className="flex h-8 w-full rounded-md border border-input bg-background px-2 text-xs ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        />
        {isLoading ? (
          <div
            className="mt-2 flex items-center gap-2 text-[11px] text-muted-foreground"
            role="status"
          >
            <Loader2 className="h-3 w-3 animate-spin text-primary motion-reduce:animate-none" aria-hidden="true" />
            <span>{t("docs_sidebar.indexing")}</span>
          </div>
        ) : error ? (
          <button
            type="button"
            onClick={() => void refetch()}
            className="mt-2 inline-flex items-center gap-1.5 text-[11px] text-destructive transition hover:text-destructive/80"
          >
            <RefreshCw
              className={cn(
                "h-3 w-3",
                isFetching && "animate-spin motion-reduce:animate-none",
              )}
              aria-hidden="true"
            />
            {t("docs_sidebar.retry")}
          </button>
        ) : (
          <p className="mt-1 text-[11px] text-muted-foreground">
            {totalCount} {t("docs_sidebar.documents")}
          </p>
        )}
      </div>

      {/* Tree */}
      <ScrollArea className="flex-1">
        <div className="px-2 py-2">
          {isLoading && <SidebarSkeleton />}

          {/* Recent docs — only when not filtered + at least 1 entry */}
          {!isLoading && !query && recent.length > 0 && (
            <div className="mb-2">
              <div className="flex w-full items-center gap-1 rounded-md px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                <Clock className="h-3 w-3" aria-hidden="true" />
                <span>{t("docs.recent")}</span>
                <span className="ml-auto text-[10px] font-normal text-muted-foreground/70">
                  {recent.length}
                </span>
              </div>
              {recent.map((doc) => (
                <button
                  key={doc.slug}
                  type="button"
                  onClick={() => onSelect(doc.slug)}
                  data-active={doc.slug === selectedSlug || undefined}
                  className={cn(
                    "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-xs transition",
                    "hover:bg-muted/60",
                    doc.slug === selectedSlug &&
                      "bg-muted font-medium border-l-2 border-primary -ml-px pl-[7px]",
                  )}
                  title={doc.title}
                >
                  <FileText className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <span className="flex-1 break-words leading-snug line-clamp-2">
                    {doc.title}
                  </span>
                </button>
              ))}
              <div className="my-2 border-b border-border/40" />
            </div>
          )}

          {filteredSections.map((section) => {
              const isCollapsed = collapsed.has(section.name);
              return (
                <div key={section.name} className="mb-2">
                  <button
                    type="button"
                    onClick={() => {
                      const next = new Set(collapsed);
                      if (isCollapsed) next.delete(section.name);
                      else next.add(section.name);
                      setCollapsed(next);
                    }}
                    aria-expanded={!isCollapsed}
                    className="flex w-full items-center gap-1 rounded-md px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground hover:bg-muted/50"
                  >
                    {isCollapsed ? (
                      <ChevronRight className="h-3 w-3" aria-hidden="true" />
                    ) : (
                      <ChevronDown className="h-3 w-3" aria-hidden="true" />
                    )}
                    <span>{section.name}</span>
                    <span className="ml-auto text-[10px] font-normal text-muted-foreground/70">
                      {section.docs.length}
                    </span>
                  </button>
                  {!isCollapsed &&
                    section.docs.map((doc) => (
                      <SidebarItem
                        key={doc.slug}
                        doc={doc}
                        active={doc.slug === selectedSlug}
                        onClick={() => onSelect(doc.slug)}
                      />
                    ))}
                </div>
              );
            })}

          {!isLoading && !error && totalCount === 0 && (
            <div className="px-3 py-8 text-center text-xs text-muted-foreground">
              {t("docs_sidebar.no_results")}
            </div>
          )}
        </div>
      </ScrollArea>
    </aside>
  );
}

interface ItemProps {
  doc: DocNavSummary;
  active: boolean;
  onClick: () => void;
}

function SidebarSkeleton() {
  return (
    <div
      className="animate-pulse space-y-5 px-2 py-2 motion-reduce:animate-none"
      aria-hidden="true"
    >
      {[4, 3, 5].map((rows, group) => (
        <div key={group} className="space-y-2">
          <div className="h-2 w-24 rounded-full bg-muted" />
          {Array.from({ length: rows }, (_, row) => (
            <div key={row} className="flex items-center gap-2 py-1">
              <div className="h-3 w-3 rounded-sm bg-muted/80" />
              <div
                className="h-2.5 rounded-full bg-muted/80"
                style={{ width: `${58 + ((row + group) % 3) * 12}%` }}
              />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

function SidebarItem({ doc, active, onClick }: ItemProps) {
  // Doc titles should be fully readable (user mandate 2026-04-29) —
  // ``break-words`` allows wrapping inside German compound words,
  // ``line-clamp-2`` caps it at 2 lines max so the list doesn't sprawl, and
  // the ``title`` attribute stays as a hover tooltip for the full title.
  // NO Diataxis pill — the section header above already carries that info
  // (user mandate: no duplicate information, no truncated ``LEGAC...`` pill).
  return (
    <button
      type="button"
      onClick={onClick}
      data-active={active || undefined}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-xs transition",
        "hover:bg-muted/60",
        active &&
          "bg-muted font-medium border-l-2 border-primary -ml-px pl-[7px]",
      )}
      title={doc.title}
    >
      <FileText className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span className="flex-1 break-words leading-snug line-clamp-2">
        {doc.title}
      </span>
    </button>
  );
}
