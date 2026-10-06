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
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { Search } from "lucide-react";

import { cleanTitle, groupOfKind, type WikiGroupId } from "@/lib/wikiModel";
import { KindGlyph } from "@/components/wiki/KindGlyph";
import { useT } from "@/i18n";

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

/** A search hit carries only its vault path; its top folder names the kind. */
function groupOfPath(path: string): WikiGroupId {
  const top = path.split(/[\\/]/)[0] ?? "";
  if (top === "entities") return "entity";
  if (top === "projects") return "project";
  if (top === "concepts") return "concept";
  if (top === "sessions") return "session";
  if (top === "society") return "agent";
  return path.includes("/") ? "other" : "system";
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
 * Build the highlight matcher for one query. Returns `null` when there is
 * nothing to highlight. Built once per query in the component rather than
 * once per snippet per render.
 */
function buildHighlightPattern(query: string): RegExp | null {
  const trimmed = query.trim();
  if (!trimmed) return null;
  const terms = trimmed
    .split(/\s+/)
    .filter(Boolean)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (terms.length === 0) return null;
  return new RegExp(`(${terms.join("|")})`, "ig");
}

/**
 * Render a snippet with `<mark>` highlight wrapping for the matched terms.
 * Best-effort: case-insensitive word match; React escapes the text for us.
 */
function highlightSnippet(snippet: string, pattern: RegExp | null): JSX.Element {
  if (!pattern || !snippet) return <>{snippet}</>;
  // `String.split` with exactly ONE capture group puts every captured
  // delimiter — i.e. every match — at an ODD index. Deriving the highlight
  // from the index is exact and free.
  //
  // It replaces a `pattern.test(part)` check that was quietly wrong: `test()`
  // on a /g regex advances `lastIndex` and resumes from there on the next
  // call, so consecutive calls alternated true/false. Real matches were left
  // unhighlighted and plain words got marked instead.
  const parts = snippet.split(pattern);
  return (
    <>
      {parts.map((part, idx) =>
        idx % 2 === 1 ? (
          <mark key={idx} className="rounded-sm bg-accent-soft text-foreground">
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
  const t = useT();
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
    // Keep the previous query's hits on screen while the next one is in
    // flight. Without this every keystroke tore the list down to a
    // "Searching…" placeholder and rebuilt it, which read as typing lag even
    // though the request itself was fast.
    placeholderData: keepPreviousData,
  });

  const highlightPattern = useMemo(
    () => buildHighlightPattern(debouncedQuery),
    [debouncedQuery],
  );

  // Read the tree through the query cache rather than `getQueryData` so the
  // palette re-renders when the tree lands. `enabled: false` means this
  // component never triggers the (expensive) vault walk itself — it only
  // shows the recents once some other view has fetched the tree.
  const { data: tree } = useQuery<TreeResponse>({
    queryKey: ["wiki", "tree"],
    queryFn: async () => {
      const res = await fetch("/api/wiki/tree");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
    enabled: false,
  });
  const recent = useMemo(() => selectRecentPages(tree), [tree]);

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
      label={t("wiki_search.dialog_label")}
      // The backend already ranked these hits (FTS5/BM25 over full page
      // bodies). cmdk's built-in filter would score them a SECOND time
      // against the item `value` — which is `hit:<slug>`, not the page text —
      // and hide every hit whose slug does not contain the query as a
      // subsequence. That is why a multi-word search looked broken: the API
      // returned results and the palette showed an empty list.
      shouldFilter={false}
      data-testid="wiki-search-dialog"
      contentClassName="fixed left-1/2 top-[18vh] z-50 w-[min(640px,90vw)] -translate-x-1/2 overflow-hidden rounded-lg bg-popover shadow-float"
      overlayClassName="fixed inset-0 z-40 bg-scrim/40 backdrop-blur-sm"
    >
      {/* Screen-reader-only title and description — required by Radix Dialog's
          a11y contract; visually hidden so the palette UI stays clean. */}
      <h2 className="sr-only">{t("wiki_search.dialog_label")}</h2>
      <p className="sr-only" id="wiki-search-description">
        {t("wiki_search.dialog_description")}
      </p>
      <div className="flex h-12 items-center gap-3 border-b border-border px-4">
        <Search className="h-4 w-4 shrink-0 text-foreground-faint" aria-hidden />
        <Command.Input
          value={rawQuery}
          onValueChange={setRawQuery}
          placeholder={t("wiki_search.input_placeholder")}
          data-testid="wiki-search-input"
          className="flex-1 bg-transparent text-base text-foreground outline-none placeholder:text-foreground-faint"
          autoFocus
        />
        <kbd className="rounded-sm border border-border px-1.5 py-0.5 font-mono text-xs text-foreground-faint">
          Esc
        </kbd>
      </div>

      <Command.List
        data-testid="wiki-search-list"
        className="max-h-[60vh] overflow-y-auto p-2 text-base [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-2 [&_[cmdk-group-heading]]:text-sm [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-foreground-faint"
      >
        {debouncedQuery.length === 0 ? (
          <Command.Group heading={t("wiki_search.recent_heading")} data-testid="wiki-search-recent">
            {recent.length === 0 ? (
              <div className="px-2 py-3 text-muted-foreground" data-testid="wiki-search-recent-empty">
                {t("wiki_search.no_pages")}
              </div>
            ) : (
              recent.map((entry) => (
                <Command.Item
                  key={entry.slug}
                  value={`recent:${entry.slug}`}
                  onSelect={() => handlePick(entry.slug)}
                  data-testid="wiki-search-recent-item"
                  data-slug={entry.slug}
                  className="flex h-9 cursor-pointer items-center gap-2.5 rounded-md px-2 text-foreground aria-selected:bg-secondary"
                >
                  <KindGlyph group={groupOfKind(entry.kind)} className="h-2.5 w-2.5 text-foreground-faint" />
                  <span className="truncate">{cleanTitle(entry.title, entry.slug)}</span>
                  <span className="ml-auto truncate text-sm text-foreground-faint">{entry.path}</span>
                </Command.Item>
              ))
            )}
          </Command.Group>
        ) : isFetching && !data ? (
          // Only the very first search shows a placeholder. Later keystrokes
          // keep the previous hits visible until the new ones arrive.
          <div className="px-2 py-3 text-muted-foreground" data-testid="wiki-search-loading">
            {t("wiki_search.searching")}
          </div>
        ) : isError || !data?.ok ? (
          <div
            className="flex flex-col items-start gap-2 px-2 py-3 text-muted-foreground"
            data-testid="wiki-search-error"
          >
            <span>{t("wiki_search.unavailable")}</span>
            <button
              type="button"
              onClick={() => refetch()}
              className="text-xs underline hover:text-foreground"
            >
              {t("common.retry")}
            </button>
          </div>
        ) : data.hits.length === 0 ? (
          <Command.Empty data-testid="wiki-search-empty">
            {t("wiki_search.no_hits_prefix")}&quot;{debouncedQuery}&quot;{t("wiki_search.no_hits_suffix")}
          </Command.Empty>
        ) : (
          <Command.Group heading={t("wiki_search.hits_heading")} data-testid="wiki-search-hits">
            {data.hits.map((hit) => (
              <Command.Item
                key={hit.slug}
                value={`hit:${hit.slug}`}
                onSelect={() => handlePick(hit.slug)}
                data-testid="wiki-search-hit"
                data-slug={hit.slug}
                className="flex cursor-pointer flex-col gap-1 rounded-md px-2 py-2 aria-selected:bg-secondary"
              >
                <span className="flex items-center gap-2.5">
                  <KindGlyph group={groupOfPath(hit.path)} className="h-2.5 w-2.5 text-foreground-faint" />
                  <span className="truncate text-foreground">{cleanTitle(hit.title, hit.slug)}</span>
                  <span className="ml-auto truncate text-sm text-foreground-faint">{hit.path}</span>
                </span>
                <span className="line-clamp-2 pl-5 text-sm text-muted-foreground">
                  {highlightSnippet(hit.snippet, highlightPattern)}
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
