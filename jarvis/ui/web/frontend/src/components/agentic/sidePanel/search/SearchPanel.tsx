import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { CaseSensitive, ChevronDown, ChevronRight, Loader2, MoreHorizontal, Regex, Replace, WholeWord } from "lucide-react";

import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useCodeEditorStore, type EditorFile } from "@/store/codeEditor";
import { useIdeChatStore } from "@/store/ideChat";
import { replaceInFiles, searchWorkspace, type SearchQuery, type SearchResult } from "@/components/agentic/editor/editorApi";
import { checkDisk } from "@/components/agentic/editor/editorModels";
import { forgetFileList } from "@/components/agentic/editor/QuickOpen";
import { FileTypeIcon } from "../explorer/FileTypeIcon";

/** Typing pauses this long before the search runs. */
const SEARCH_DELAY_MS = 350;

/** Ask the Search tab to take the keyboard (Ctrl+Shift+F). */
export const SEARCH_FOCUS_EVENT = "jarvis:editor-search-focus";

const baseName = (path: string) => path.split("/").pop() ?? path;
const folderOf = (path: string) => path.split("/").slice(0, -1).join("/");

function Toggle({ on, label, onClick, children }: { on: boolean; label: string; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      aria-label={label}
      title={label}
      onClick={onClick}
      className={cn(
        "inline-flex h-6 w-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        on && "bg-primary/15 text-foreground ring-1 ring-inset ring-primary/40",
      )}
    >
      {children}
    </button>
  );
}

/**
 * Search in every file of the workspace (Ctrl+Shift+F), with optional
 * replace. A result opens the file in the code editor with the match selected.
 */
export function SearchPanel() {
  const t = useT();
  const workspace = useIdeChatStore((state) => state.workspace);
  const workspaceId = workspace?.id ?? null;
  const [query, setQuery] = useState("");
  const [replacement, setReplacement] = useState("");
  const [showReplace, setShowReplace] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [wholeWord, setWholeWord] = useState(false);
  const [regex, setRegex] = useState(false);
  const [include, setInclude] = useState("");
  const [exclude, setExclude] = useState("");
  const [result, setResult] = useState<SearchResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set());
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState("");
  const [nonce, setNonce] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  const search: SearchQuery = useMemo(
    () => ({ query, regex, caseSensitive, wholeWord, include, exclude }),
    [query, regex, caseSensitive, wholeWord, include, exclude],
  );

  useEffect(() => {
    input.current?.focus();
    const focus = () => {
      input.current?.focus();
      input.current?.select();
    };
    window.addEventListener(SEARCH_FOCUS_EVENT, focus);
    return () => window.removeEventListener(SEARCH_FOCUS_EVENT, focus);
  }, []);

  useEffect(() => {
    setConfirming(false);
    if (!workspaceId || !search.query) {
      setResult(null);
      setError("");
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setBusy(true);
      searchWorkspace(workspaceId, search, controller.signal)
        .then((answer) => {
          setResult(answer);
          setError("");
        })
        .catch((err: unknown) => {
          if ((err as Error).name !== "AbortError") setError((err as Error).message);
        })
        .finally(() => setBusy(false));
    }, SEARCH_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [workspaceId, search, nonce]);

  if (!workspace || !workspaceId) {
    return <p className="px-4 py-3 text-sm text-muted-foreground">{t("ide_side_panel.explorer.no_workspace")}</p>;
  }

  const open = (path: string, line: number, column: number, length: number) =>
    useCodeEditorStore.getState().openFile(workspaceId, path, { line, column, length });

  const runReplace = async () => {
    if (!result) return;
    setConfirming(false);
    // A file with unsaved edits in the editor is left alone: replacing on
    // disk under it would only turn into a conflict.
    const files = Object.values(useCodeEditorStore.getState().files) as EditorFile[];
    const dirty = new Set(files.filter((file) => file.workspaceId === workspaceId && file.dirty).map((file) => file.path));
    const paths = result.results.map((entry) => entry.path).filter((path) => !dirty.has(path));
    const skippedDirty = result.results.length - paths.length;
    if (paths.length === 0) {
      setNotice(fill(t("ide_side_panel.search.skipped_dirty"), { count: skippedDirty }));
      return;
    }
    setBusy(true);
    try {
      const answer = await replaceInFiles(workspaceId, search, replacement, paths);
      let message = fill(t("ide_side_panel.search.replaced"), {
        count: answer.replacements,
        files: answer.replaced_files.length,
      });
      if (skippedDirty) message += ` ${fill(t("ide_side_panel.search.skipped_dirty"), { count: skippedDirty })}`;
      if (answer.skipped.length) message += ` ${fill(t("ide_side_panel.search.skipped_other"), { count: answer.skipped.length })}`;
      setNotice(message);
      forgetFileList(workspaceId);
      // Open buffers follow the files they show.
      const state = useCodeEditorStore.getState();
      for (const path of answer.replaced_files) {
        const key = Object.keys(state.files).find((fileKey) => state.files[fileKey].workspaceId === workspaceId && state.files[fileKey].path === path);
        if (key) void checkDisk(key);
      }
      setNonce((value) => value + 1);
    } catch (err) {
      setNotice((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const fieldClass =
    "flex h-8 min-w-0 flex-1 items-center gap-1 rounded-lg border border-border/60 bg-background/50 pl-2.5 pr-1 focus-within:border-ring";
  const textClass = "min-w-0 flex-1 bg-transparent text-xs text-foreground outline-none placeholder:text-muted-foreground";

  return (
    <section data-testid="ide-search" aria-label={t("ide_side_panel.tabs.search")} className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 space-y-1.5 border-b border-border/60 px-3 pb-2.5 pt-3">
        <div className="flex items-start gap-1">
          <button
            type="button"
            aria-expanded={showReplace}
            aria-label={t("ide_side_panel.search.toggle_replace")}
            title={t("ide_side_panel.search.toggle_replace")}
            onClick={() => setShowReplace((value) => !value)}
            className="mt-1 inline-flex h-6 w-5 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-secondary hover:text-foreground"
          >
            {showReplace ? <ChevronDown className="h-3.5 w-3.5" aria-hidden /> : <ChevronRight className="h-3.5 w-3.5" aria-hidden />}
          </button>
          <div className="min-w-0 flex-1 space-y-1.5">
            <label className={fieldClass}>
              <input
                ref={input}
                data-testid="ide-search-input"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") setNonce((value) => value + 1);
                }}
                placeholder={t("ide_side_panel.search.placeholder")}
                aria-label={t("ide_side_panel.search.placeholder")}
                className={textClass}
              />
              <Toggle on={caseSensitive} label={t("ide_side_panel.search.match_case")} onClick={() => setCaseSensitive((v) => !v)}>
                <CaseSensitive className="h-4 w-4" aria-hidden />
              </Toggle>
              <Toggle on={wholeWord} label={t("ide_side_panel.search.whole_word")} onClick={() => setWholeWord((v) => !v)}>
                <WholeWord className="h-4 w-4" aria-hidden />
              </Toggle>
              <Toggle on={regex} label={t("ide_side_panel.search.regex")} onClick={() => setRegex((v) => !v)}>
                <Regex className="h-4 w-4" aria-hidden />
              </Toggle>
            </label>
            {showReplace && (
              <div className="flex items-center gap-1">
                <label className={fieldClass}>
                  <input
                    data-testid="ide-search-replace"
                    value={replacement}
                    onChange={(event) => setReplacement(event.target.value)}
                    placeholder={t("ide_side_panel.search.replace_placeholder")}
                    aria-label={t("ide_side_panel.search.replace_placeholder")}
                    className={textClass}
                  />
                </label>
                <button
                  type="button"
                  data-testid="ide-search-replace-all"
                  disabled={!result?.match_count || busy}
                  onClick={() => (confirming ? void runReplace() : setConfirming(true))}
                  title={t("ide_side_panel.search.replace_all")}
                  className={cn(
                    "inline-flex h-8 shrink-0 items-center gap-1 rounded-lg border px-2 text-xs disabled:opacity-40",
                    confirming ? "border-destructive/60 bg-destructive/10 text-destructive" : "border-border/60 text-foreground hover:bg-secondary",
                  )}
                >
                  <Replace className="h-3.5 w-3.5" aria-hidden />
                  {confirming
                    ? fill(t("ide_side_panel.search.replace_confirm"), { count: result?.match_count ?? 0, files: result?.file_count ?? 0 })
                    : t("ide_side_panel.search.replace_all")}
                </button>
              </div>
            )}
          </div>
          <button
            type="button"
            aria-expanded={showFilters}
            aria-label={t("ide_side_panel.search.filters")}
            title={t("ide_side_panel.search.filters")}
            onClick={() => setShowFilters((value) => !value)}
            className="mt-1 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-secondary hover:text-foreground"
          >
            <MoreHorizontal className="h-4 w-4" aria-hidden />
          </button>
        </div>
        {showFilters && (
          <div className="space-y-1.5 pl-6 pr-7">
            <label className={fieldClass}>
              <input value={include} onChange={(event) => setInclude(event.target.value)} placeholder={t("ide_side_panel.search.include")} aria-label={t("ide_side_panel.search.include")} className={textClass} />
            </label>
            <label className={fieldClass}>
              <input value={exclude} onChange={(event) => setExclude(event.target.value)} placeholder={t("ide_side_panel.search.exclude")} aria-label={t("ide_side_panel.search.exclude")} className={textClass} />
            </label>
          </div>
        )}
        {(result || busy || error || notice) && (
          <p className="flex items-center gap-1.5 pl-6 text-[11px] text-muted-foreground" role="status">
            {busy && <Loader2 className="h-3 w-3 animate-spin" aria-hidden />}
            {error ? (
              <span className="text-destructive">{error}</span>
            ) : notice ? (
              <span>{notice}</span>
            ) : result ? (
              <span>
                {result.match_count === 0
                  ? t("ide_side_panel.search.no_results")
                  : fill(t("ide_side_panel.search.summary"), { count: result.match_count, files: result.file_count })}
                {result.truncated && ` ${t("ide_side_panel.search.truncated")}`}
              </span>
            ) : (
              <span>{t("ide_side_panel.search.searching")}</span>
            )}
          </p>
        )}
      </div>

      <ul data-testid="ide-search-results" className="scrollbar-jarvis min-h-0 flex-1 overflow-y-auto py-1">
        {result?.results.map((file) => {
          const closed = collapsed.has(file.path);
          return (
            <li key={file.path}>
              <button
                type="button"
                onClick={() =>
                  setCollapsed((current) => {
                    const next = new Set(current);
                    if (next.has(file.path)) next.delete(file.path);
                    else next.add(file.path);
                    return next;
                  })
                }
                title={file.path}
                className="flex h-7 w-full items-center gap-1.5 px-2 text-left text-[13px] hover:bg-muted/60"
              >
                <ChevronRight className={cn("h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform", !closed && "rotate-90")} aria-hidden />
                <FileTypeIcon path={file.path} />
                <span className="shrink-0 text-foreground">{baseName(file.path)}</span>
                <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground">{folderOf(file.path)}</span>
                <span className="shrink-0 rounded-full bg-secondary px-1.5 text-[10px] tabular-nums text-muted-foreground">{file.matches.length}</span>
              </button>
              {!closed && (
                <ul>
                  {file.matches.map((match) => (
                    <li key={`${match.line}:${match.column}`}>
                      <button
                        type="button"
                        data-testid="ide-search-match"
                        onClick={() => open(file.path, match.line, match.column, match.length)}
                        title={`${file.path}:${match.line}`}
                        className="flex h-6 w-full items-center gap-2 pl-9 pr-2 text-left font-mono text-[12px] hover:bg-muted/60"
                      >
                        <span className="min-w-0 flex-1 truncate whitespace-pre text-muted-foreground">
                          {match.preview.slice(0, match.preview_start).trimStart()}
                          <mark className="rounded-sm bg-warning/30 text-foreground">
                            {match.preview.slice(match.preview_start, match.preview_start + match.length)}
                          </mark>
                          {match.preview.slice(match.preview_start + match.length)}
                        </span>
                        <span className="shrink-0 text-[10px] tabular-nums text-muted-foreground/70">{match.line}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
