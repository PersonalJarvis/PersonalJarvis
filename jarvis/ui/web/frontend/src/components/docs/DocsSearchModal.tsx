import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Command } from "cmdk";
import {
  Clock,
  FileText,
  FileWarning,
  Loader2,
  RefreshCw,
  Search,
} from "lucide-react";

import { buildDocSections, useDocSearch, useDocsGrouped } from "@/hooks/useDocs";
import { useRecentDocs } from "@/hooks/useRecentDocs";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect: (slug: string) => void;
}

/**
 * Full-text search modal. Ctrl+K opens it, typing shows live results, and
 * Enter opens the selected guide.
 *
 * FTS5 surrounds matches with ``<mark>`` tags. ``renderSearchSnippet`` turns
 * only those markers into React elements and keeps every other character as
 * text, so documentation content can never inject HTML into the dialog.
 */
export function DocsSearchModal({ open, onOpenChange, onSelect }: Props) {
  const t = useT();
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebounced(query, 150);
  const {
    data: results = [],
    isFetching,
    error,
    refetch,
  } = useDocSearch(
    debouncedQuery,
    undefined,
    open,
  );
  const grouped = useDocsGrouped();
  const { recent } = useRecentDocs();

  // With nothing typed yet the dialog offers a starting point: the guides
  // opened most recently, then the first topic's reading path.
  const suggestions = useMemo(() => {
    const sections = buildDocSections(grouped.data);
    const known = new Map<string, { title: string; section: string }>();
    for (const section of sections) {
      for (const doc of section.docs) {
        known.set(doc.slug, { title: doc.title, section: section.name });
      }
    }
    const recentRows: SuggestionRow[] = [];
    for (const doc of recent) {
      const entry = known.get(doc.slug);
      if (entry) recentRows.push({ slug: doc.slug, ...entry });
    }
    const start = sections[0];
    const startRows: SuggestionRow[] = (start?.docs ?? [])
      .filter((doc) => !recentRows.some((row) => row.slug === doc.slug))
      .map((doc) => ({ slug: doc.slug, title: doc.title, section: start.name }));
    return { recentRows, startName: start?.name ?? "", startRows };
  }, [grouped.data, recent]);

  // Reset the query on close so a re-open starts fresh.
  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  const choose = (slug: string) => {
    onSelect(slug);
    onOpenChange(false);
  };

  const hasSuggestions =
    suggestions.recentRows.length + suggestions.startRows.length > 0;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-scrim/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content className="fixed left-1/2 top-[12%] z-50 w-[min(680px,calc(100vw-2rem))] -translate-x-1/2 overflow-hidden rounded-lg bg-popover shadow-float data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none">
          <Dialog.Title className="sr-only">{t("docs.search_modal_title")}</Dialog.Title>
          <Dialog.Description className="sr-only">
            {t("docs.search_hint")}
          </Dialog.Description>
          <Command shouldFilter={false} loop>
            <div className="flex h-14 items-center gap-3 border-b border-border px-4">
              <Search className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <Command.Input
                value={query}
                onValueChange={setQuery}
                placeholder={t("docs.search_modal_placeholder")}
                aria-label={t("docs.search_modal_title")}
                name="docs-search"
                autoComplete="off"
                autoFocus
                className="h-full flex-1 bg-transparent text-lg text-foreground outline-none placeholder:text-foreground-faint"
              />
              {isFetching && debouncedQuery.trim() && (
                <Loader2
                  className="h-4 w-4 shrink-0 animate-spin text-muted-foreground motion-reduce:animate-none"
                  aria-hidden="true"
                />
              )}
              <kbd className="shrink-0 rounded-sm border border-border px-1.5 py-0.5 font-sans text-xs font-medium text-muted-foreground">
                Esc
              </kbd>
            </div>

            <Command.List className="max-h-96 overflow-y-auto p-2">
              {!debouncedQuery.trim() ? (
                !hasSuggestions ? (
                  <div className="px-3 py-10 text-center text-base text-muted-foreground">
                    {t("docs.search_hint")}
                  </div>
                ) : (
                  <>
                    {suggestions.recentRows.length > 0 && (
                      <Command.Group heading={t("docs.recent")} className={GROUP_CLASS}>
                        {suggestions.recentRows.map((row) => (
                          <SuggestionItem
                            key={`recent-${row.slug}`}
                            value={`recent-${row.slug}`}
                            icon={Clock}
                            row={row}
                            onSelect={() => choose(row.slug)}
                          />
                        ))}
                      </Command.Group>
                    )}
                    {suggestions.startRows.length > 0 && (
                      <Command.Group heading={suggestions.startName} className={GROUP_CLASS}>
                        {suggestions.startRows.map((row) => (
                          <SuggestionItem
                            key={`start-${row.slug}`}
                            value={`start-${row.slug}`}
                            icon={FileText}
                            row={row}
                            onSelect={() => choose(row.slug)}
                          />
                        ))}
                      </Command.Group>
                    )}
                  </>
                )
              ) : isFetching && results.length === 0 ? (
                <div className="px-3 py-10 text-center text-base text-muted-foreground">
                  {t("docs.search_loading")}
                </div>
              ) : error ? (
                <div
                  className="flex flex-col items-center px-3 py-10 text-center text-base text-muted-foreground"
                  role="alert"
                >
                  <FileWarning
                    className="mb-2 h-4 w-4 text-destructive"
                    aria-hidden="true"
                  />
                  <span>{t("docs.search_failed")}</span>
                  <button
                    type="button"
                    onClick={() => void refetch()}
                    className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-md border border-border-strong px-3 text-sm font-medium text-foreground transition-colors hover:bg-secondary"
                  >
                    <RefreshCw className="h-3 w-3" aria-hidden="true" />
                    {t("docs.search_retry")}
                  </button>
                </div>
              ) : results.length === 0 ? (
                <Command.Empty className="px-3 py-10 text-center text-base text-muted-foreground">
                  {t("docs.no_results").replace("{0}", debouncedQuery)}
                </Command.Empty>
              ) : (
                results.map((r) => (
                  <Command.Item
                    key={r.slug}
                    value={r.slug}
                    onSelect={() => choose(r.slug)}
                    className={cn(
                      "group flex cursor-pointer gap-3 rounded-md px-3 py-2.5",
                      "data-[selected=true]:bg-secondary",
                    )}
                  >
                    <FileText
                      className="mt-0.5 h-4 w-4 shrink-0 text-foreground-faint group-data-[selected=true]:text-accent"
                      aria-hidden="true"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline justify-between gap-3">
                        <span className="truncate text-base font-medium text-foreground-strong">
                          {r.title}
                        </span>
                        <span className="shrink-0 text-xs text-muted-foreground">
                          {r.section}
                        </span>
                      </div>
                      <div className="mt-0.5 line-clamp-2 text-sm text-muted-foreground [&>mark]:rounded-sm [&>mark]:bg-accent-soft [&>mark]:px-0.5 [&>mark]:text-foreground-strong">
                        {renderSearchSnippet(r.snippet)}
                      </div>
                    </div>
                  </Command.Item>
                ))
              )}
            </Command.List>

            <div className="flex items-center gap-4 border-t border-border px-4 py-2 text-xs text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <kbd className={KBD_CLASS}>↑</kbd>
                <kbd className={KBD_CLASS}>↓</kbd>
                {t("docs_search_modal.navigate")}
              </span>
              <span className="flex items-center gap-1.5">
                <kbd className={KBD_CLASS}>↵</kbd>
                {t("docs_search_modal.open")}
              </span>
            </div>
          </Command>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

interface SuggestionRow {
  slug: string;
  title: string;
  section: string;
}

const GROUP_CLASS =
  "[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pb-1.5 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-muted-foreground";

const KBD_CLASS =
  "inline-flex h-5 min-w-5 items-center justify-center rounded-sm border border-border px-1 font-sans text-xs font-medium";

function SuggestionItem({
  value,
  icon: Icon,
  row,
  onSelect,
}: {
  value: string;
  icon: typeof FileText;
  row: SuggestionRow;
  onSelect: () => void;
}) {
  return (
    <Command.Item
      value={value}
      onSelect={onSelect}
      className="group flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 data-[selected=true]:bg-secondary"
    >
      <Icon
        className="h-4 w-4 shrink-0 text-foreground-faint group-data-[selected=true]:text-accent"
        aria-hidden="true"
      />
      <span className="min-w-0 flex-1 truncate text-base text-foreground">{row.title}</span>
      <span className="shrink-0 text-xs text-muted-foreground">{row.section}</span>
    </Command.Item>
  );
}

/**
 * The index stores raw Markdown, so a snippet arrives with ``**``, link
 * targets and table pipes in it. Strip that syntax (keeping the ``<mark>``
 * highlights) so a result reads as prose.
 */
export function cleanSnippetMarkdown(value: string): string {
  return value
    .replace(/\]\([^)]*\)/g, "]")
    .replace(/[[\]]/g, "")
    .replace(/(\*\*|__|`)/g, "")
    .replace(/(^|\s)#{1,6}\s/g, "$1")
    .replace(/\s*\|\s*/g, " · ")
    .replace(/\s{2,}/g, " ")
    .trim();
}

export function renderSearchSnippet(value: string): React.ReactNode {
  const parts = cleanSnippetMarkdown(value).split(/(<mark>|<\/mark>)/gi);
  let marked = false;
  return parts.map((part, index) => {
    if (part.toLowerCase() === "<mark>") {
      marked = true;
      return null;
    }
    if (part.toLowerCase() === "</mark>") {
      marked = false;
      return null;
    }
    return marked ? <mark key={index}>{part}</mark> : part;
  });
}

/** A very simple debounce hook — avoids pulling in an extra lib. */
function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}
