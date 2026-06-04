import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Command } from "cmdk";
import { Search, FileText } from "lucide-react";

import { useDocSearch } from "@/hooks/useDocs";
import { DocTypeBadge } from "./DocTypeBadge";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect: (slug: string) => void;
}

/**
 * Volltext-Search-Modal — cmdk + Radix-Dialog. Pattern Anthropic/Tailwind-
 * Style: Strg+K oeffnet, Tippen zeigt Live-Results, Enter springt zum Doc.
 *
 * Backend-FTS5 liefert ``snippet``-HTML mit ``<mark>``-Tags fuer Highlights;
 * wir rendern das mit ``dangerouslySetInnerHTML`` weil es nur unsere eigenen
 * Doc-Bodies sind (kein User-Input, kein XSS-Risiko).
 */
export function DocsSearchModal({ open, onOpenChange, onSelect }: Props) {
  const t = useT();
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebounced(query, 150);
  const { data: results = [], isFetching } = useDocSearch(
    debouncedQuery,
    undefined,
    open,
  );

  // Reset query beim Schliessen, damit Re-open frisch startet.
  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content className="fixed left-1/2 top-[15%] z-50 w-[min(640px,calc(100vw-2rem))] -translate-x-1/2 overflow-hidden rounded-lg border border-border bg-card shadow-2xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95">
          <Dialog.Title className="sr-only">{t("docs.search_modal_title")}</Dialog.Title>
          <Command shouldFilter={false} loop>
            {/* Header / Input */}
            <div className="flex items-center gap-2 border-b border-border px-3 py-2">
              <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
              <Command.Input
                value={query}
                onValueChange={setQuery}
                placeholder={t("docs.search_modal_placeholder")}
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
                      <FileText className="h-3 w-3 shrink-0 text-muted-foreground" />
                      <span className="flex-1 truncate font-medium">
                        {r.title}
                      </span>
                      <DocTypeBadge diataxis={r.diataxis} className="shrink-0" />
                    </div>
                    <div
                      className="ml-5 line-clamp-2 text-xs text-muted-foreground [&>mark]:bg-yellow-500/30 [&>mark]:text-foreground"
                      dangerouslySetInnerHTML={{ __html: r.snippet }}
                    />
                  </Command.Item>
                ))
              )}
            </Command.List>

            {/* Footer */}
            <div className="flex items-center justify-between border-t border-border bg-muted/20 px-3 py-1.5 text-[10px] text-muted-foreground">
              <span>
                <kbd className="rounded border border-border px-1 font-medium">↑↓</kbd>{" "}
                navigieren
              </span>
              <span>
                <kbd className="rounded border border-border px-1 font-medium">↵</kbd>{" "}
                oeffnen
              </span>
            </div>
          </Command>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** Sehr einfacher Debounce-Hook — vermeidet eine extra Lib. */
function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}
