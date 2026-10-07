import { useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { Loader2, Search } from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { tabsOf, useCodeEditorStore } from "@/store/codeEditor";
import { FileTypeIcon } from "@/components/agentic/sidePanel/explorer/FileTypeIcon";
import { fetchFileList } from "./editorApi";
import { parseQuickOpen, rankPaths } from "./quickOpenMatch";

/** A listing is reused for this long, so Ctrl+P twice in a row opens instantly. */
const LIST_TTL_MS = 20_000;
const listCache = new Map<string, { at: number; paths: string[]; truncated: boolean }>();

/** Drop a workspace's cached listing after the explorer created, renamed or deleted something. */
export function forgetFileList(workspaceId: string): void {
  listCache.delete(workspaceId);
}

/**
 * Go to File (Ctrl+P): type part of a name, Enter opens it. `name:42` opens at
 * line 42. With nothing typed the open tabs are offered first.
 */
export function QuickOpen({ workspaceId }: { workspaceId: string }) {
  const t = useT();
  const close = () => useCodeEditorStore.getState().setQuickOpen(false);
  const openTabs = useCodeEditorStore(useShallow((state) => tabsOf(state, workspaceId)));
  const [query, setQuery] = useState("");
  const [paths, setPaths] = useState<string[] | null>(() => listCache.get(workspaceId)?.paths ?? null);
  const [error, setError] = useState("");
  const [cursor, setCursor] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);

  useEffect(() => {
    input.current?.focus();
    const cached = listCache.get(workspaceId);
    if (cached && Date.now() - cached.at < LIST_TTL_MS) return;
    let alive = true;
    fetchFileList(workspaceId)
      .then((answer) => {
        listCache.set(workspaceId, { at: Date.now(), ...answer });
        if (alive) setPaths(answer.paths);
      })
      .catch((err: unknown) => alive && setError((err as Error).message));
    return () => {
      alive = false;
    };
  }, [workspaceId]);

  const parsed = parseQuickOpen(query);
  const results = useMemo(() => {
    if (!parsed.needle) {
      const recent = [...openTabs].reverse().map((tab) => tab.path);
      const unique = [...new Set(recent)];
      return [...unique, ...(paths ?? []).filter((path) => !unique.includes(path))].slice(0, 50);
    }
    return rankPaths(paths ?? [], parsed.needle);
  }, [parsed.needle, paths, openTabs]);

  useEffect(() => setCursor(0), [parsed.needle]);
  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-index="${cursor}"]`)?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  const choose = (path: string | undefined) => {
    if (!path) return;
    close();
    useCodeEditorStore.getState().openFile(workspaceId, path, { preview: false, line: parsed.line, column: parsed.column });
  };

  return (
    <div
      role="presentation"
      className="fixed inset-0 z-[85] flex justify-center bg-background/40 px-4 pt-[12vh]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label={t("code_editor.quick_open.title")}
        data-testid="code-editor-quick-open"
        className="flex h-fit max-h-[60vh] w-full max-w-[620px] flex-col overflow-hidden rounded-xl border border-border bg-popover text-popover-foreground shadow-float"
      >
        <label className="flex h-11 shrink-0 items-center gap-2 border-b border-border/70 px-3">
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          <input
            ref={input}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                const step = event.key === "ArrowDown" ? 1 : -1;
                setCursor((current) => Math.max(0, Math.min(results.length - 1, current + step)));
              } else if (event.key === "Enter") {
                event.preventDefault();
                choose(results[cursor]);
              } else if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                close();
              }
            }}
            placeholder={t("code_editor.quick_open.placeholder")}
            aria-label={t("code_editor.quick_open.placeholder")}
            aria-controls="code-editor-quick-open-list"
            aria-activedescendant={results.length ? `quick-open-${cursor}` : undefined}
            className="min-w-0 flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
          />
          {!paths && !error && <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" aria-hidden />}
        </label>
        <ul ref={list} id="code-editor-quick-open-list" role="listbox" className="scrollbar-jarvis min-h-0 flex-1 overflow-y-auto py-1">
          {error && <li className="px-3 py-2 text-xs text-destructive">{error}</li>}
          {paths && results.length === 0 && <li className="px-3 py-2 text-xs text-muted-foreground">{t("code_editor.quick_open.no_match")}</li>}
          {results.map((path, index) => {
            const slash = path.lastIndexOf("/");
            return (
              <li
                key={path}
                id={`quick-open-${index}`}
                data-index={index}
                role="option"
                aria-selected={index === cursor}
                onMouseMove={() => setCursor(index)}
                onMouseDown={(event) => {
                  event.preventDefault();
                  choose(path);
                }}
                className={cn(
                  "flex h-8 cursor-pointer items-center gap-2 px-3 text-[13px]",
                  index === cursor && "bg-secondary",
                )}
              >
                <FileTypeIcon path={path} />
                <span className="shrink-0 text-foreground">{path.slice(slash + 1)}</span>
                {slash > 0 && <span className="min-w-0 truncate text-xs text-muted-foreground">{path.slice(0, slash)}</span>}
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}
