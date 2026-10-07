/**
 * The Wiki section's left rail: every page in the vault, findable in one
 * glance.
 *
 * A filter field on top narrows every group at once as you type. With no
 * filter, the five most recently changed pages lead — what the assistant
 * learned last is usually what you came to look at. Below them the vault's
 * folders are regrouped by what a page IS (people and things, projects,
 * agents' notebooks, system pages), each with the shape the memory map draws
 * it as, so the list and the map share one visual vocabulary.
 */
import { useMemo, useState, type ReactNode } from "react";
import { ChevronRight, Search, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT, useUiLanguage } from "@/i18n";
import type { WikiTreeFolder } from "@/lib/wikiApi";
import {
  GROUP_LABEL_KEY,
  compactAge,
  libraryGroups,
  libraryItems,
  matchesFilter,
  recentItems,
  type LibraryItem,
  type WikiGroupId,
} from "@/lib/wikiModel";
import { KindGlyph } from "@/components/wiki/KindGlyph";

const DEFAULT_OPEN: ReadonlySet<WikiGroupId> = new Set(["entity", "project", "concept"]);

export interface WikiLibraryProps {
  folders: readonly WikiTreeFolder[];
  isLoading: boolean;
  isError: boolean;
  selectedSlug: string | null;
  onSelect: (slug: string) => void;
  /** Drawn above the filter: the section's title, stats and search. */
  header?: ReactNode;
}

export function WikiLibrary({
  folders,
  isLoading,
  isError,
  selectedSlug,
  onSelect,
  header,
}: WikiLibraryProps) {
  const t = useT();
  const language = useUiLanguage();
  const [query, setQuery] = useState("");
  const [openGroups, setOpenGroups] = useState<Set<WikiGroupId>>(() => new Set(DEFAULT_OPEN));

  const items = useMemo(() => libraryItems(folders), [folders]);
  const filtered = useMemo(
    () => (query.trim() ? items.filter((item) => matchesFilter(item, query)) : items),
    [items, query],
  );
  const groups = useMemo(() => libraryGroups(filtered), [filtered]);
  const recent = useMemo(() => recentItems(items, 5), [items]);
  const filtering = query.trim().length > 0;
  // One clock per render of the list, so every row ages against the same now.
  const now = Date.now();

  const toggle = (id: WikiGroupId) =>
    setOpenGroups((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <aside
      className="flex h-full min-h-0 w-[264px] shrink-0 flex-col"
      data-testid="wiki-tree-sidebar"
      aria-label={t("wiki_ui.library_label")}
    >
      {header}
      <div className="shrink-0 px-3 pb-2">
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-foreground-faint"
            aria-hidden
          />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") setQuery("");
            }}
            placeholder={t("wiki_ui.filter_placeholder")}
            aria-label={t("wiki_ui.filter_placeholder")}
            data-testid="wiki-library-filter"
            className={cn(
              "h-8 w-full rounded-lg border border-transparent bg-transparent pl-8 pr-7 text-sm text-foreground hover:bg-secondary/60",
              "placeholder:text-foreground-faint [&::-webkit-search-cancel-button]:hidden",
              "transition-colors focus-visible:border-border focus-visible:bg-input focus-visible:outline-none",
            )}
          />
          {filtering && (
            <button
              type="button"
              onClick={() => setQuery("")}
              aria-label={t("wiki_ui.filter_clear")}
              className="absolute right-1.5 top-1/2 flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded-sm text-foreground-faint hover:bg-secondary hover:text-foreground"
            >
              <X className="h-3 w-3" aria-hidden />
            </button>
          )}
        </div>
      </div>

      <nav className="min-h-0 flex-1 overflow-y-auto px-2 pb-3" data-testid="wiki-library-list">
        {isLoading && <LibrarySkeleton />}
        {isError && (
          <p role="alert" className="px-2 py-2 text-sm text-destructive">
            {t("tree_sidebar.load_error")}
          </p>
        )}

        {!isLoading && !isError && (
          <>
            {!filtering && recent.length > 0 && (
              <Section label={t("wiki_ui.recent")} testId="wiki-library-recent">
                {recent.map((item) => (
                  <Row
                    key={`recent:${item.folder}/${item.slug}`}
                    item={item}
                    age={compactAge(item.mtime, language, now)}
                    active={item.slug === selectedSlug}
                    onSelect={onSelect}
                    testId="wiki-library-recent-item"
                  />
                ))}
              </Section>
            )}

            {groups.map((group) => {
              const open = filtering || openGroups.has(group.id);
              return (
                <div key={group.id} className="mt-3 first:mt-0" data-folder={group.id}>
                  <button
                    type="button"
                    onClick={() => toggle(group.id)}
                    disabled={filtering}
                    aria-expanded={open}
                    data-open={open ? "true" : "false"}
                    className={cn(
                      "group flex h-7 w-full items-center gap-1.5 rounded-md px-2 text-sm font-medium text-foreground-faint transition-colors",
                      !filtering && "hover:text-foreground",
                    )}
                  >
                    <ChevronRight
                      className={cn("h-3 w-3 shrink-0 transition-transform", open && "rotate-90")}
                      aria-hidden
                    />
                    <span className="flex-1 truncate text-left">{t(GROUP_LABEL_KEY[group.id])}</span>
                    <span className="tabular-nums">{group.items.length}</span>
                  </button>
                  {open && (
                    <ul className="mt-0.5 space-y-px" data-testid={`wiki-folder-${group.id}`}>
                      {group.items.map((item) => (
                        <li key={`${item.folder}/${item.slug}`}>
                          <Row
                            item={item}
                            age={compactAge(item.mtime, language, now)}
                            active={item.slug === selectedSlug}
                            onSelect={onSelect}
                          />
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}

            {filtering && groups.length === 0 && (
              <p className="px-2 py-6 text-center text-sm text-muted-foreground" data-testid="wiki-library-no-match">
                {t("wiki_ui.filter_no_match")}
              </p>
            )}
          </>
        )}
      </nav>
    </aside>
  );
}

function Section({
  label,
  testId,
  children,
}: {
  label: string;
  testId: string;
  children: ReactNode;
}) {
  return (
    <div className="mb-1" data-testid={testId}>
      <div className="flex h-7 items-center px-2 text-sm font-medium text-foreground-faint">
        {label}
      </div>
      <div className="space-y-px">{children}</div>
    </div>
  );
}

function Row({
  item,
  age,
  active,
  onSelect,
  testId,
}: {
  item: LibraryItem;
  age: string;
  active: boolean;
  onSelect: (slug: string) => void;
  testId?: string;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(item.slug)}
      data-slug={item.slug}
      data-active={active ? "true" : "false"}
      data-testid={testId}
      title={item.title}
      className={cn(
        "flex h-8 w-full items-center gap-2.5 rounded-lg px-2 text-left text-base transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "bg-secondary text-foreground-strong"
          : "text-foreground-secondary hover:bg-secondary hover:text-foreground",
      )}
    >
      <KindGlyph
        group={item.group}
        className={cn("h-2.5 w-2.5", active ? "text-accent" : "text-foreground-faint")}
      />
      <span className="min-w-0 flex-1 truncate">{item.title}</span>
      {age && (
        <span className="shrink-0 text-xs tabular-nums text-foreground-faint">{age}</span>
      )}
    </button>
  );
}

function LibrarySkeleton() {
  return (
    <div className="space-y-2 px-2 py-2" data-testid="wiki-tree-skeleton" aria-busy="true">
      {[72, 56, 64, 48, 60, 52].map((width, i) => (
        <div
          key={i}
          className="h-4 animate-pulse rounded-sm bg-foreground/10"
          style={{ width: `${width}%` }}
        />
      ))}
    </div>
  );
}
