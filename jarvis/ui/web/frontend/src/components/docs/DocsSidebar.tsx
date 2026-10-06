import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ExternalLink, RefreshCw } from "lucide-react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { buildDocSections, useDocsGrouped } from "@/hooks/useDocs";
import { useT } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";
import { ONLINE_DOCS_URL, SearchTrigger } from "./docsShared";

interface Props {
  selectedSlug: string | null;
  onSelect: (slug: string) => void;
  onShowOverview: () => void;
  onOpenSearch: () => void;
}

/**
 * The docs navigation: one search field, an Overview entry, then every topic
 * as a collapsible group of plain text links. No per-row icons or counts —
 * the titles carry the navigation, the active row carries the accent.
 */
export function DocsSidebar({
  selectedSlug,
  onSelect,
  onShowOverview,
  onOpenSearch,
}: Props) {
  const t = useT();
  const { data, isLoading, isFetching, error, refetch } = useDocsGrouped();
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const sections = useMemo(() => buildDocSections(data), [data]);

  // Opening a guide from search or a cross-link re-opens its group, so the
  // active row is never hidden inside a collapsed topic.
  useEffect(() => {
    if (!selectedSlug) return;
    const owner = sections.find((section) =>
      section.docs.some((doc) => doc.slug === selectedSlug),
    );
    if (!owner) return;
    setCollapsed((prev) => {
      if (!prev.has(owner.name)) return prev;
      const next = new Set(prev);
      next.delete(owner.name);
      return next;
    });
  }, [selectedSlug, sections]);

  const toggle = (name: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  return (
    <aside
      aria-label={t("docs_sidebar.title")}
      className="flex h-full w-64 shrink-0 flex-col"
    >
      <div className="px-4 pb-3 pt-5">
        <SearchTrigger onClick={onOpenSearch} />
      </div>

      <ScrollArea className="flex-1">
        <nav className="px-3 pb-6">
          <NavRow
            label={t("docs_sidebar.overview")}
            active={selectedSlug === null}
            onClick={onShowOverview}
          />

          {isLoading && <SidebarSkeleton />}

          {error && !isLoading && (
            <button
              type="button"
              onClick={() => void refetch()}
              className="mt-4 inline-flex items-center gap-1.5 rounded-sm px-3 text-sm text-destructive transition-colors hover:text-destructive/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
          )}

          {sections.map((section) => {
            const isCollapsed = collapsed.has(section.name);
            const groupId = `docs-nav-${section.order}-${section.docs[0]?.slug ?? "group"}`;
            return (
              <div key={section.name} className="mt-5">
                <button
                  type="button"
                  onClick={() => toggle(section.name)}
                  aria-expanded={!isCollapsed}
                  aria-controls={groupId}
                  className="group flex w-full items-center justify-between gap-2 rounded-sm px-3 py-1 text-left text-sm font-semibold text-foreground-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span>{section.name}</span>
                  <ChevronDown
                    className={cn(
                      "h-3.5 w-3.5 shrink-0 text-foreground-faint opacity-0 transition group-hover:opacity-100 group-focus-visible:opacity-100",
                      isCollapsed && "-rotate-90 opacity-100",
                    )}
                    aria-hidden="true"
                  />
                </button>
                {!isCollapsed && (
                  <ul id={groupId} className="mt-1 space-y-px">
                    {section.docs.map((doc) => (
                      <li key={doc.slug}>
                        <NavRow
                          label={doc.title}
                          active={doc.slug === selectedSlug}
                          onClick={() => onSelect(doc.slug)}
                        />
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </nav>
      </ScrollArea>

      <div className="px-4 py-3">
        <a
          href={ONLINE_DOCS_URL}
          onClick={(event) => {
            event.preventDefault();
            void openExternalUrl(ONLINE_DOCS_URL);
          }}
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 rounded-sm text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {t("docs_sidebar.github")}
          <ExternalLink className="h-3 w-3" aria-hidden="true" />
        </a>
      </div>
    </aside>
  );
}

function NavRow({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-active={active || undefined}
      aria-current={active ? "page" : undefined}
      title={label}
      className={cn(
        "block w-full rounded-md px-3 py-1.5 text-left text-base transition-colors",
        "hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        // The ink is set per state: a muted-ink utility would outrank the
        // strong ink `.jarvis-nav-active` gives the selected row.
        active
          ? "jarvis-nav-active font-medium text-foreground-strong"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      <span className="line-clamp-2 break-words">{label}</span>
    </button>
  );
}

function SidebarSkeleton() {
  return (
    <div
      className="mt-5 animate-pulse space-y-6 px-3 motion-reduce:animate-none"
      aria-hidden="true"
    >
      {[5, 4, 6].map((rows, group) => (
        <div key={group} className="space-y-3">
          <div className="h-2.5 w-24 rounded-full bg-muted" />
          {Array.from({ length: rows }, (_, row) => (
            <div
              key={row}
              className="h-2.5 rounded-full bg-muted/70"
              style={{ width: `${55 + ((row + group) % 3) * 14}%` }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
