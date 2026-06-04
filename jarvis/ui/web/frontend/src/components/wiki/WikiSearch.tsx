// Ctrl-K command palette for the Wiki tab.
//
// Owned by Agent C of Phase B3. Wraps the `cmdk` command palette inside a
// modal Dialog. Behaviour spec lives in docs/plans/b3/AGENT-C-graph-and-search.md
// §5.
//
//   * Ctrl-K (or Cmd-K on Mac) opens the palette globally while the Wiki tab
//     is mounted.
//   * Forward-ref exposes `open()` so the header search box (Agent B) can
//     trigger the palette on click.
//   * Empty query → "Recent" section listing the 5 most-recently-modified
//     pages from the existing `tree` query cache.
//   * Typing fires `/api/wiki/search?q=…&k=8` debounced 200 ms.
//   * Enter or click → `onResultClick(slug)` and closes the dialog.
import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useState } from "react";
import { Command } from "cmdk";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { colourForKind } from "@/lib/wikiGraph";

const SEARCH_DEBOUNCE_MS = 200;
const SEARCH_LIMIT = 8;
const RECENT_LIMIT = 5;

interface SearchHit {
  slug: string;
  title: string;
  path: string;
  snippet: string;
  score: number;
}

interface SearchResponse {
  ok: boolean;
  query: string;
  hits: SearchHit[];
  error?: string;
}

interface TreeFile {
  slug: string;
  title: string;
  mtime: number;
  size: number;
}

interface TreeFolder {
  name: string;
  kind: string;
  count: number;
  files: TreeFile[];
}

interface TreeResponse {
  ok: boolean;
  vault_root: string;
  folders: TreeFolder[];
}

interface RecentEntry {
  slug: string;
  title: string;
  path: string;
  kind: string;
  mtime: number;
}

async function fetchSearch(query: string): Promise<SearchResponse> {
  const url = `/api/wiki/search?q=${encodeURIComponent(query)}&k=${SEARCH_LIMIT}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/**
 * Pull recent pages out of the existing tree cache so the empty-query state
 * shows something useful without firing another network request.
 */
function selectRecentPages(tree: TreeResponse | undefined): RecentEntry[] {
  if (!tree?.ok) return [];
  const flat: RecentEntry[] = [];
  for (const folder of tree.folders) {
    for (const file of folder.files) {
      flat.push({
        slug: file.slug,
        title: file.title,
        path: `${folder.name}/${file.slug}.md`,
        kind: folder.kind,
        mtime: file.mtime,
      });
    }
  }
  flat.sort((a, b) => b.mtime - a.mtime);
  return flat.slice(0, RECENT_LIMIT);
}

/**
 * Render a snippet with `<mark>` highlight wrapping for the matched terms.
 * Best-effort: case-insensitive word match, escapes HTML.
 */
function highlightSnippet(snippet: string, query: string): JSX.Element {
  const trimmed = query.trim();
  if (!trimmed || !snippet) return <>{snippet}</>;
  const terms = trimmed
    .split(/\s+/)
    .filter(Boolean)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (terms.length === 0) return <>{snippet}</>;
  const pattern = new RegExp(`(${terms.join("|")})`, "ig");
  const parts = snippet.split(pattern);
  return (
    <>
      {parts.map((part, idx) =>
        pattern.test(part) ? (
          <mark key={idx} className="bg-amber-400/30 text-foreground">
            {part}
          </mark>
        ) : (
          <span key={idx}>{part}</span>
        ),
      )}
    </>
  );
}

export interface WikiSearchProps {
  onResultClick: (slug: string) => void;
}

export interface WikiSearchHandle {
  open: () => void;
}

export const WikiSearch = forwardRef<WikiSearchHandle, WikiSearchProps>(function WikiSearch(
  { onResultClick },
  ref,
): JSX.Element {
  const [open, setOpen] = useState(false);
  const [rawQuery, setRawQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");

  useImperativeHandle(ref, () => ({ open: () => setOpen(true) }), []);

  // Ctrl-K / Cmd-K global trigger.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const isOpenCombo =
        event.key.toLowerCase() === "k" && (event.ctrlKey || event.metaKey);
      if (isOpenCombo) {
        event.preventDefault();
        setOpen((prev) => !prev);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // Debounce input → query that actually fires the fetch.
  useEffect(() => {
    const handle = window.setTimeout(() => setDebouncedQuery(rawQuery.trim()), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(handle);
  }, [rawQuery]);

  // Reset on close so the next open starts fresh.
  useEffect(() => {
    if (!open) {
      setRawQuery("");
      setDebouncedQuery("");
    }
  }, [open]);

  const enabled = open && debouncedQuery.length > 0;
  const { data, isFetching, isError, refetch } = useQuery({
    queryKey: ["wiki", "search", debouncedQuery],
    queryFn: () => fetchSearch(debouncedQuery),
    enabled,
    staleTime: 10_000,
  });

  const queryClient = useQueryClient();
  const recent = useMemo(() => {
    const tree = queryClient.getQueryData<TreeResponse>(["wiki", "tree"]);
    return selectRecentPages(tree);
  }, [queryClient, open]);

  const handlePick = useCallback(
    (slug: string) => {
      onResultClick(slug);
      setOpen(false);
    },
    [onResultClick],
  );

  // Render the modal even when closed so the imperative `open()` and Ctrl-K
  // shortcut produce a single Dialog mount across the lifetime of the tab.
  return (
    <Command.Dialog
      open={open}
      onOpenChange={setOpen}
      label="Wiki-Suche"
      data-testid="wiki-search-dialog"
      contentClassName="fixed left-1/2 top-[20vh] z-50 w-[min(640px,90vw)] -translate-x-1/2 rounded-xl border border-border bg-background shadow-2xl"
      overlayClassName="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm"
    >
      {/* Screen-reader-only title and description — required by Radix Dialog's
          a11y contract; visually hidden so the palette UI stays clean. */}
      <h2 className="sr-only">Wiki-Suche</h2>
      <p className="sr-only" id="wiki-search-description">
        Volltext-Suche im Vault. Tippe einen Begriff oder navigiere mit den Pfeiltasten.
      </p>
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <span aria-hidden="true" className="text-muted-foreground">⌕</span>
        <Command.Input
          value={rawQuery}
          onValueChange={setRawQuery}
          placeholder="Volltext-Suche im Vault…"
          data-testid="wiki-search-input"
          className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          autoFocus
        />
        <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
          Esc
        </kbd>
      </div>

      <Command.List
        data-testid="wiki-search-list"
        className="max-h-[60vh] overflow-y-auto p-2 text-sm"
      >
        {debouncedQuery.length === 0 ? (
          <Command.Group heading="Zuletzt geändert" data-testid="wiki-search-recent">
            {recent.length === 0 ? (
              <div className="px-2 py-3 text-muted-foreground" data-testid="wiki-search-recent-empty">
                Noch keine Seiten im Vault.
              </div>
            ) : (
              recent.map((entry) => (
                <Command.Item
                  key={entry.slug}
                  value={`recent:${entry.slug}`}
                  onSelect={() => handlePick(entry.slug)}
                  data-testid="wiki-search-recent-item"
                  data-slug={entry.slug}
                  className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 aria-selected:bg-muted"
                >
                  <span
                    aria-hidden="true"
                    className="inline-block h-2 w-2 rounded-full"
                    style={{ background: colourForKind(entry.kind) }}
                  />
                  <span className="font-medium">{entry.title}</span>
                  <span className="text-muted-foreground">— {entry.path}</span>
                </Command.Item>
              ))
            )}
          </Command.Group>
        ) : isFetching ? (
          <div className="px-2 py-3 text-muted-foreground" data-testid="wiki-search-loading">
            Suche läuft…
          </div>
        ) : isError || !data?.ok ? (
          <div
            className="flex flex-col items-start gap-2 px-2 py-3 text-muted-foreground"
            data-testid="wiki-search-error"
          >
            <span>Suche gerade nicht verfügbar.</span>
            <button
              type="button"
              onClick={() => refetch()}
              className="text-xs underline hover:text-foreground"
            >
              Erneut versuchen
            </button>
          </div>
        ) : data.hits.length === 0 ? (
          <Command.Empty data-testid="wiki-search-empty">
            Keine Treffer für &quot;{debouncedQuery}&quot;. Versuche andere Stichworte oder schau in den Graph.
          </Command.Empty>
        ) : (
          <Command.Group heading="Treffer" data-testid="wiki-search-hits">
            {data.hits.map((hit) => (
              <Command.Item
                key={hit.slug}
                value={`hit:${hit.slug}`}
                onSelect={() => handlePick(hit.slug)}
                data-testid="wiki-search-hit"
                data-slug={hit.slug}
                className="flex cursor-pointer flex-col gap-1 rounded px-2 py-1.5 aria-selected:bg-muted"
              >
                <span className="flex items-center gap-2">
                  <span className="font-medium">{hit.title}</span>
                  <span className="text-muted-foreground">— {hit.path}</span>
                </span>
                <span className="line-clamp-2 text-xs text-muted-foreground">
                  {highlightSnippet(hit.snippet, debouncedQuery)}
                </span>
              </Command.Item>
            ))}
          </Command.Group>
        )}
      </Command.List>
    </Command.Dialog>
  );
});

export default WikiSearch;
