/**
 * A lazy, workspace-scoped file tree for the running Agentic IDE.
 *
 * It follows the familiar editor explorer shape without importing another
 * product's chrome: compact rows, folders first, one gold selection mark, and
 * the app's own translucent panel token over the shared wallpaper.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ChevronRight,
  File,
  FileCode2,
  FileText,
  Folder,
  FolderOpen,
  Image as ImageIcon,
  Link2,
  Loader2,
  RefreshCw,
  X,
} from "lucide-react";

import { useT } from "@/i18n";
import {
  fetchWorkspaceFiles,
  openTerminalTarget,
  type WorkspaceFileItem,
} from "@/lib/agenticIdeApi";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

interface DirectoryState {
  entries: WorkspaceFileItem[];
  loading: boolean;
  truncated: boolean;
  error: string | null;
}

interface WorkspaceExplorerProps {
  workspaceId: string;
  rootName: string;
  onClose: () => void;
}

export function WorkspaceExplorer({
  workspaceId,
  rootName,
  onClose,
}: WorkspaceExplorerProps) {
  const t = useT();
  const tRef = useRef(t);
  tRef.current = t;
  const pushToast = useEventStore((state) => state.pushToast);
  const [directories, setDirectories] = useState<Record<string, DirectoryState>>({});
  const [openPaths, setOpenPaths] = useState<Set<string>>(() => new Set([""]));
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [resolvedRootName, setResolvedRootName] = useState(rootName);

  const loadDirectory = useCallback(
    async (path: string) => {
      setDirectories((current) => ({
        ...current,
        [path]: {
          entries: current[path]?.entries ?? [],
          loading: true,
          truncated: false,
          error: null,
        },
      }));
      try {
        const response = await fetchWorkspaceFiles(workspaceId, path);
        if (path === "") setResolvedRootName(response.root_name || rootName);
        setDirectories((current) => ({
          ...current,
          [path]: {
            entries: response.entries,
            loading: false,
            truncated: response.truncated,
            error: response.error
              ? tRef.current("agentic_grid.explorer.load_failed")
              : null,
          },
        }));
      } catch {
        setDirectories((current) => ({
          ...current,
          [path]: {
            entries: current[path]?.entries ?? [],
            loading: false,
            truncated: false,
            error: tRef.current("agentic_grid.explorer.load_failed"),
          },
        }));
      }
    },
    [rootName, workspaceId],
  );

  useEffect(() => {
    setDirectories({});
    setOpenPaths(new Set([""]));
    setSelectedPath(null);
    setResolvedRootName(rootName);
    void loadDirectory("");
  }, [loadDirectory, rootName]);

  const toggleDirectory = useCallback(
    (path: string) => {
      setOpenPaths((current) => {
        const next = new Set(current);
        if (next.has(path)) {
          next.delete(path);
        } else {
          next.add(path);
          if (!directories[path]) void loadDirectory(path);
        }
        return next;
      });
    },
    [directories, loadDirectory],
  );

  const openFile = useCallback(
    async (path: string) => {
      try {
        await openTerminalTarget(workspaceId, path);
      } catch (error) {
        const detail = error instanceof Error ? error.message : "";
        pushToast(
          "error",
          detail || t("agentic_grid.explorer.open_failed"),
        );
      }
    },
    [pushToast, t, workspaceId],
  );

  const refresh = () => {
    setDirectories({});
    setOpenPaths(new Set([""]));
    setSelectedPath(null);
    void loadDirectory("");
  };

  const rootOpen = openPaths.has("");

  return (
    <aside
      id="workspace-explorer"
      data-testid="workspace-explorer"
      className="flex h-full w-[280px] shrink-0 flex-col border-l border-border/55 backdrop-blur-md"
      style={{ background: "rgb(var(--shell-rgb) / 0.22)" }}
      aria-label={t("agentic_grid.explorer.title")}
    >
      <header className="flex h-10 shrink-0 items-center gap-2 border-b border-border/60 px-3">
        <span className="min-w-0 flex-1 truncate text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
          {t("agentic_grid.explorer.title")}
        </span>
        <button
          type="button"
          onClick={refresh}
          aria-label={t("agentic_grid.explorer.refresh")}
          title={t("agentic_grid.explorer.refresh")}
          className="flex h-7 w-7 items-center justify-center rounded-control text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden />
        </button>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("agentic_grid.explorer.close")}
          title={t("agentic_grid.explorer.close")}
          className="flex h-7 w-7 items-center justify-center rounded-control text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-auto px-1.5 py-2 scrollbar-jarvis">
        <div role="tree" aria-label={resolvedRootName}>
          <button
            type="button"
            role="treeitem"
            aria-expanded={rootOpen}
            onClick={() => toggleDirectory("")}
            className="flex h-7 w-full items-center gap-1.5 rounded-control px-1.5 text-left text-xs font-semibold uppercase tracking-[0.08em] text-foreground transition-colors hover:bg-secondary/70 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring/60"
          >
            <ChevronRight
              className={cn(
                "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
                rootOpen && "rotate-90",
              )}
              aria-hidden
            />
            {rootOpen ? (
              <FolderOpen className="h-4 w-4 shrink-0 text-primary" aria-hidden />
            ) : (
              <Folder className="h-4 w-4 shrink-0 text-primary" aria-hidden />
            )}
            <span className="truncate">{resolvedRootName}</span>
          </button>

          {rootOpen && (
            <TreeLevel
              parentPath=""
              depth={1}
              directories={directories}
              openPaths={openPaths}
              selectedPath={selectedPath}
              onToggle={toggleDirectory}
              onSelect={setSelectedPath}
              onOpenFile={(path) => void openFile(path)}
            />
          )}
        </div>
      </div>

      <footer className="shrink-0 border-t border-border/60 px-3 py-2 text-[10px] leading-relaxed text-muted-foreground">
        {t("agentic_grid.explorer.open_hint")}
      </footer>
    </aside>
  );
}

function TreeLevel({
  parentPath,
  depth,
  directories,
  openPaths,
  selectedPath,
  onToggle,
  onSelect,
  onOpenFile,
}: {
  parentPath: string;
  depth: number;
  directories: Record<string, DirectoryState>;
  openPaths: Set<string>;
  selectedPath: string | null;
  onToggle: (path: string) => void;
  onSelect: (path: string) => void;
  onOpenFile: (path: string) => void;
}) {
  const t = useT();
  const directory = directories[parentPath];

  if (!directory || directory.loading) {
    return (
      <div
        role="status"
        className="flex h-7 items-center gap-2 text-[11px] text-muted-foreground"
        style={{ paddingLeft: `${depth * 12 + 9}px` }}
      >
        <Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" aria-hidden />
        {t("agentic_grid.explorer.loading")}
      </div>
    );
  }

  if (directory.error) {
    return (
      <div
        role="alert"
        className="my-1 border-l-2 border-destructive/70 py-1 pr-2 text-[11px] leading-relaxed text-destructive"
        style={{ marginLeft: `${depth * 12 + 9}px`, paddingLeft: "8px" }}
      >
        {directory.error}
      </div>
    );
  }

  return (
    <div role="group">
      {directory.entries.map((entry) => {
        const expandable = entry.is_directory && !entry.is_symlink;
        const isOpen = expandable && openPaths.has(entry.path);
        const selected = selectedPath === entry.path;
        const EntryIcon = iconFor(entry, isOpen);
        return (
          <div key={entry.path}>
            <button
              type="button"
              role="treeitem"
              aria-expanded={expandable ? isOpen : undefined}
              aria-selected={selected}
              title={entry.path}
              onClick={() => {
                onSelect(entry.path);
                if (expandable) onToggle(entry.path);
              }}
              onDoubleClick={() => {
                if (!entry.is_directory && !entry.is_symlink) onOpenFile(entry.path);
              }}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter" &&
                  !entry.is_directory &&
                  !entry.is_symlink
                ) {
                  event.preventDefault();
                  onOpenFile(entry.path);
                }
              }}
              className={cn(
                "group flex h-6 w-full items-center gap-1.5 rounded-control pr-2 text-left text-[12px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring/60",
                selected
                  ? "bg-primary/10 text-foreground shadow-[inset_2px_0_0_hsl(var(--primary))]"
                  : "text-muted-foreground hover:bg-secondary/65 hover:text-foreground",
              )}
              style={{ paddingLeft: `${depth * 12}px` }}
            >
              {expandable ? (
                <ChevronRight
                  className={cn(
                    "h-3 w-3 shrink-0 transition-transform",
                    isOpen && "rotate-90",
                  )}
                  aria-hidden
                />
              ) : (
                <span className="w-3 shrink-0" aria-hidden />
              )}
              <EntryIcon
                className={cn(
                  "h-3.5 w-3.5 shrink-0",
                  expandable
                    ? "text-primary/80"
                    : entry.is_symlink
                      ? "text-muted-foreground/70"
                      : selected
                        ? "text-primary"
                        : "text-muted-foreground group-hover:text-foreground/80",
                )}
                aria-hidden
              />
              <span className="min-w-0 flex-1 truncate">{entry.name}</span>
            </button>

            {isOpen && (
              <TreeLevel
                parentPath={entry.path}
                depth={depth + 1}
                directories={directories}
                openPaths={openPaths}
                selectedPath={selectedPath}
                onToggle={onToggle}
                onSelect={onSelect}
                onOpenFile={onOpenFile}
              />
            )}
          </div>
        );
      })}

      {directory.entries.length === 0 && (
        <div
          className="h-6 truncate text-[11px] italic leading-6 text-muted-foreground/70"
          style={{ paddingLeft: `${depth * 12 + 21}px` }}
        >
          {t("agentic_grid.explorer.empty")}
        </div>
      )}
      {directory.truncated && (
        <div
          role="note"
          className="py-1 pr-2 text-[10px] leading-relaxed text-amber-500"
          style={{ paddingLeft: `${depth * 12 + 21}px` }}
        >
          {t("agentic_grid.explorer.truncated")}
        </div>
      )}
    </div>
  );
}

function iconFor(entry: WorkspaceFileItem, open: boolean) {
  if (entry.is_symlink) return Link2;
  if (entry.is_directory) return open ? FolderOpen : Folder;
  const name = entry.name.toLowerCase();
  if (/\.(png|jpe?g|gif|webp|svg|ico|bmp)$/.test(name)) return ImageIcon;
  if (/\.(md|mdx|txt|rst|log)$/.test(name)) return FileText;
  if (
    /\.(py|js|jsx|ts|tsx|css|scss|html|rs|go|java|c|cc|cpp|h|hpp|sh|ps1|toml|ya?ml|json)$/.test(
      name,
    )
  ) {
    return FileCode2;
  }
  return File;
}
