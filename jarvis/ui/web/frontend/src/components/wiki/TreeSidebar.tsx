/**
 * Folder tree for the on-disk Obsidian vault.
 *
 * Renders the response from `/api/wiki/tree` as a collapsible tree.
 * Folder open/closed state lives in local component state — not persisted.
 * Folders `entities` and `projects` start open; `concepts`, `sessions`,
 * `_archive` start closed (mockup contract).
 */
import { useState, useMemo, useEffect } from "react";
import { ChevronRight, FileText } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { cn } from "@/lib/utils";
import { fetchWikiTree, type WikiTreeFolder } from "@/lib/wikiApi";
import { useT } from "@/i18n";

interface TreeSidebarProps {
  selectedSlug: string | null;
  onSelect: (slug: string) => void;
}

const DEFAULT_OPEN = new Set(["entities", "projects"]);

export function TreeSidebar({ selectedSlug, onSelect }: TreeSidebarProps) {
  const t = useT();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["wiki", "tree"],
    queryFn: fetchWikiTree,
    staleTime: 5_000,
  });

  const folders: WikiTreeFolder[] = data?.folders ?? [];
  const stats = data?.stats;

  const [openFolders, setOpenFolders] = useState<Set<string>>(
    () => new Set(DEFAULT_OPEN),
  );

  // Reconcile open state when the tree arrives: non-empty folders that match
  // DEFAULT_OPEN remain open; otherwise leave user-controlled state alone.
  // Important: return the previous Set reference when no folder needed to be
  // added — returning a fresh Set every render triggers an infinite loop in
  // React 18 strict mode (and in tests).
  useEffect(() => {
    setOpenFolders((prev) => {
      let changed = false;
      const next = new Set(prev);
      for (const f of folders) {
        if (DEFAULT_OPEN.has(f.name) && f.count > 0 && !next.has(f.name)) {
          next.add(f.name);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [folders]);

  const toggle = (name: string) => {
    setOpenFolders((prev) => {
      const next = new Set(prev);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next;
    });
  };

  const lastChangedLabel = useMemo(() => {
    if (!stats?.last_curator_run) return "—";
    try {
      const d = new Date(stats.last_curator_run);
      return d.toLocaleString();
    } catch {
      return "—";
    }
  }, [stats?.last_curator_run]);

  return (
    <aside
      className="flex h-full w-[260px] shrink-0 flex-col overflow-y-auto bg-sidebar"
      data-testid="wiki-tree-sidebar"
    >
      <div className="flex items-center justify-between border-b border-border px-3 py-3">
        <h2 className="text-title font-semibold text-foreground-strong">Vault</h2>
        <span className="text-meta text-muted-foreground">wiki/obsidian-vault/</span>
      </div>

      <div className="flex-1 p-2">
        {isLoading && <TreeSkeleton />}
        {isError && (
          <div
            role="alert"
            className="m-2 text-meta text-destructive"
          >
            {t("tree_sidebar.load_error")}
          </div>
        )}

        {!isLoading && !isError &&
          folders.map((folder) => {
            const isOpen = openFolders.has(folder.name);
            return (
              <div key={folder.name} className="mb-1">
                <button
                  type="button"
                  onClick={() => toggle(folder.name)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-body",
                    "text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground",
                  )}
                  data-folder={folder.name}
                  data-open={isOpen ? "true" : "false"}
                >
                  <ChevronRight
                    className={cn(
                      "h-3 w-3 shrink-0 transition-transform",
                      isOpen && "rotate-90",
                    )}
                  />
                  <span className="flex-1 text-left">{folder.name}</span>
                  <span className="text-meta tabular-nums">{folder.count}</span>
                </button>

                {isOpen && folder.files.length > 0 && (
                  <ul className="ml-3 mt-0.5 space-y-0.5" data-testid={`wiki-folder-${folder.name}`}>
                    {folder.files.map((file) => {
                      const isActive = file.slug === selectedSlug;
                      return (
                        <li key={file.slug}>
                          <button
                            type="button"
                            onClick={() => onSelect(file.slug)}
                            data-slug={file.slug}
                            data-active={isActive ? "true" : "false"}
                            className={cn(
                              "flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-body transition-colors",
                              isActive
                                ? "bg-secondary text-foreground-strong"
                                : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                            )}
                          >
                            <FileText className="h-3 w-3 shrink-0" />
                            <span className="truncate">{file.slug}.md</span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            );
          })}
      </div>

      {stats && (
        <div
          className="space-y-1 border-t border-border px-3 py-3 text-meta text-muted-foreground"
          data-testid="wiki-tree-meta"
        >
          <div className="flex justify-between gap-2">
            <span>Total pages</span>
            <span className="tabular-nums text-foreground">{stats.total_pages}</span>
          </div>
          <div className="flex justify-between gap-2">
            <span>Total links</span>
            <span className="tabular-nums text-foreground">{stats.total_links}</span>
          </div>
          <div className="flex justify-between gap-2">
            <span>{t("tree_sidebar.last_change")}</span>
            <span className="text-foreground">{lastChangedLabel}</span>
          </div>
        </div>
      )}
    </aside>
  );
}

function TreeSkeleton() {
  return (
    <div className="space-y-2 p-2" data-testid="wiki-tree-skeleton">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="h-4 animate-pulse rounded-full bg-sheen/[0.06]" />
      ))}
    </div>
  );
}
