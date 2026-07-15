import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Command } from "cmdk";
import { FileText, FileWarning, RefreshCw, Search } from "lucide-react";

import { useDocSearch } from "@/hooks/useDocs";
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

  // Reset the query on close so a re-open starts fresh.
  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content className="fixed left-1/2 top-[15%] z-50 w-[min(640px,calc(100vw-2rem))] -translate-x-1/2 overflow-hidden rounded-lg border border-border bg-card shadow-2xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none">
          <Dialog.Title className="sr-only">{t("docs.search_modal_title")}</Dialog.Title>
          <Dialog.Description className="sr-only">
            {t("docs.search_hint")}
          </Dialog.Description>
          <Command shouldFilter={false} loop>
            {/* Header / Input */}
            <div className="flex items-center gap-2 border-b border-border px-3 py-2">
              <Search className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <Command.Input
                value={query}
                onValueChange={setQuery}
                placeholder={t("docs.search_modal_placeholder")}
                aria-label={t("docs.search_modal_title")}
                name="docs-search"
                autoComplete="off"
                autoFocus
                className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              />
              <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                Esc
              </kbd>
            </div>

            {/* Results */}
            <Command.List className="max-h-80 overflow-y-auto p-1">
              {!debouncedQuery.trim() ? (
                <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                  {t("docs.search_hint")}
                </div>
              ) : isFetching ? (
                <div className="px-3 py-6 text-center text-xs text-muted-foreground">
                  {t("docs.search_loading")}
                </div>
              ) : error ? (
                <div
                  className="flex flex-col items-center px-3 py-6 text-center text-xs text-muted-foreground"
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
                    className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-1.5 font-medium text-foreground transition-colors hover:bg-muted"
                  >
                    <RefreshCw className="h-3 w-3" aria-hidden="true" />
                    {t("docs.search_retry")}
                  </button>
                </div>
              ) : results.length === 0 ? (
                <Command.Empty className="px-3 py-6 text-center text-xs text-muted-foreground">
                  {t("docs.no_results").replace("{0}", debouncedQuery)}
                </Command.Empty>
              ) : (
                results.map((r) => (
                  <Command.Item
                    key={r.slug}
                    value={r.slug}
                    onSelect={() => {
                      onSelect(r.slug);
                      onOpenChange(false);
                    }}
                    className={cn(
                      "flex cursor-pointer flex-col gap-1 rounded-md px-3 py-2 text-sm",
                      "data-[selected=true]:bg-muted",
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <FileText className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden="true" />
                      <span className="flex-1 truncate font-medium">
                        {r.title}
                      </span>
                      <span className="shrink-0 text-[10px] uppercase tracking-wider text-muted-foreground">
                        {r.section}
                      </span>
                    </div>
                    <div className="ml-5 line-clamp-2 text-xs text-muted-foreground [&>mark]:bg-yellow-500/30 [&>mark]:text-foreground">
                      {renderSearchSnippet(r.snippet)}
                    </div>
                  </Command.Item>
                ))
              )}
            </Command.List>

            {/* Footer */}
            <div className="flex items-center justify-between border-t border-border bg-muted/20 px-3 py-1.5 text-[10px] text-muted-foreground">
              <span>
                <kbd className="rounded border border-border px-1 font-medium">↑↓</kbd>{" "}
                {t("docs_search_modal.navigate")}
              </span>
              <span>
                <kbd className="rounded border border-border px-1 font-medium">↵</kbd>{" "}
                {t("docs_search_modal.open")}
              </span>
            </div>
          </Command>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function renderSearchSnippet(value: string): React.ReactNode {
  const parts = value.split(/(<mark>|<\/mark>)/gi);
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
