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
  FileSearch,
  Link2,
  Loader2,
  RefreshCw,
  X,
} from "lucide-react";

import { useThemeValue } from "@/hooks/useTheme";
import { useT } from "@/i18n";
import {
  fetchWorkspaceFiles,
  type WorkspaceFileItem,
} from "@/lib/agenticIdeApi";
import { cn } from "@/lib/utils";

import { setWorkspaceEntryDrag } from "./explorerDrag";
import { materialFileIcon, useMaterialFileIcons } from "./materialFileIcons";

interface DirectoryState {
  entries: WorkspaceFileItem[];
  loading: boolean;
  truncated: boolean;
  error: string | null;
}

interface WorkspaceExplorerProps {
  workspaceId: string;
  rootName: string;
  /**
   * Absolute path of the workspace folder — what makes the rows draggable.
   *
   * The listing endpoint only ever returns paths relative to it, and a pane
   * needs the absolute one to tell a file inside the workspace from one outside
   * it. Optional because a caller that has not resolved the folder yet should
   * still get a working tree; the rows simply do not lift.
   */
  rootPath?: string;
  onClose: () => void;
  onOpenFile: (path: string, trigger?: HTMLElement) => void;
}

export function WorkspaceExplorer({
  workspaceId,
  rootName,
  rootPath,
  onClose,
  onOpenFile,
}: WorkspaceExplorerProps) {
  const t = useT();
  const theme = useThemeValue();
  useMaterialFileIcons();
  const tRef = useRef(t);
  tRef.current = t;
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
    (path: string, trigger?: HTMLElement) => {
      setSelectedPath(path);
      onOpenFile(path, trigger);
    },
    [onOpenFile],
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
      className="flex h-full w-full min-w-0 flex-col backdrop-blur-md"
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
              <MaterialEntryIcon
                icon={materialFileIcon({
                  name: resolvedRootName,
                  directory: true,
                  open: true,
                  root: true,
                  theme,
                })}
                className="h-4 w-4"
              />
            ) : (
              <MaterialEntryIcon
                icon={materialFileIcon({
                  name: resolvedRootName,
                  directory: true,
                  root: true,
                  theme,
                })}
                className="h-4 w-4"
              />
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
              theme={theme}
              rootPath={rootPath}
              onToggle={toggleDirectory}
              onSelect={setSelectedPath}
              onOpenFile={openFile}
            />
          )}
        </div>
      </div>

      <footer className="shrink-0 border-t border-border/60 px-3 py-2 text-[10px] leading-relaxed text-muted-foreground">
        {rootPath
          ? t("agentic_grid.explorer.drag_hint")
          : t("agentic_grid.explorer.open_hint")}
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
  theme,
  rootPath,
  onToggle,
  onSelect,
  onOpenFile,
}: {
  parentPath: string;
  depth: number;
  directories: Record<string, DirectoryState>;
  openPaths: Set<string>;
  selectedPath: string | null;
  theme: "dark" | "light";
  rootPath?: string;
  onToggle: (path: string) => void;
  onSelect: (path: string) => void;
  onOpenFile: (path: string, trigger?: HTMLElement) => void;
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
        const icon = materialFileIcon({
          name: entry.name,
          path: entry.path,
          directory: expandable,
          open: isOpen,
          theme,
        });
        const isFile = !entry.is_directory && !entry.is_symlink;
        const canOpen = isFile;
        const typeName = entry.is_symlink
          ? t("agentic_grid.explorer.symlink")
          : entry.is_directory
            ? t("agentic_grid.explorer.folder")
            : fileTypeName(entry.name, icon.id, t("agentic_grid.explorer.file"));
        return (
          <div key={entry.path}>
            <div
              // Draggable on the ROW rather than on the button inside it, so the
              // ghost the browser draws under the cursor is the whole line —
              // icon, name and indentation — instead of a cropped label.
              //
              // Cancelled when the path cannot be built (no workspace root yet):
              // a drag that carries nothing still suppresses the click that
              // would have opened the file, which reads as a dead row.
              draggable={Boolean(rootPath)}
              data-testid={`explorer-entry-${entry.path}`}
              onDragStart={(event) => {
                if (
                  !rootPath ||
                  !setWorkspaceEntryDrag(event.dataTransfer, {
                    root: rootPath,
                    path: entry.path,
                  })
                ) {
                  event.preventDefault();
                }
              }}
              className={cn(
                "group flex h-7 w-full items-center rounded-control transition-colors",
                selected
                  ? "bg-primary/10 text-foreground shadow-[inset_2px_0_0_hsl(var(--primary))]"
                  : "text-muted-foreground hover:bg-secondary/65 hover:text-foreground",
              )}
            >
              <button
                type="button"
                role="treeitem"
                aria-expanded={expandable ? isOpen : undefined}
                aria-selected={selected}
                aria-label={`${entry.name}, ${typeName}`}
                title={`${typeName} · ${entry.path}`}
                onClick={(event) => {
                  onSelect(entry.path);
                  if (expandable) onToggle(entry.path);
                  if (canOpen) onOpenFile(entry.path, event.currentTarget);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && canOpen) {
                    event.preventDefault();
                    onOpenFile(entry.path, event.currentTarget);
                  }
                }}
                className="flex h-full min-w-0 flex-1 items-center gap-1.5 text-left text-[12px] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring/60"
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
                {entry.is_symlink ? (
                  <Link2
                    className="h-4 w-4 shrink-0 text-muted-foreground/70"
                    aria-hidden
                  />
                ) : (
                  <MaterialEntryIcon icon={icon} className="h-4 w-4" />
                )}
                <span className="min-w-0 flex-1 truncate">{entry.name}</span>
              </button>

              {isFile && (
                <button
                  type="button"
                  onClick={(event) => {
                    onSelect(entry.path);
                    onOpenFile(entry.path, event.currentTarget);
                  }}
                  aria-label={`${t("agentic_grid.explorer.open_file")} ${entry.name}`}
                  title={t("agentic_grid.explorer.open_file")}
                  className="mr-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-control text-muted-foreground/70 opacity-70 transition hover:bg-primary/10 hover:text-primary hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring/60"
                >
                  <FileSearch className="h-3 w-3" aria-hidden />
                </button>
              )}
            </div>

            {isOpen && (
              <TreeLevel
                parentPath={entry.path}
                depth={depth + 1}
                directories={directories}
                openPaths={openPaths}
                selectedPath={selectedPath}
                theme={theme}
                rootPath={rootPath}
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

const TECHNICAL_FILE_TYPES: Record<string, string> = {
  csharp: "C#",
  cpp: "C++",
  docker: "Docker",
  javascript: "JavaScript",
  markdown: "Markdown",
  nodejs: "Node.js",
  powershell: "PowerShell",
  react_ts: "React TypeScript",
  readme: "Markdown README",
  typescript: "TypeScript",
};

function fileTypeName(name: string, iconId: string, genericLabel: string) {
  if (TECHNICAL_FILE_TYPES[iconId]) return TECHNICAL_FILE_TYPES[iconId];
  const dotIndex = name.lastIndexOf(".");
  if (dotIndex > -1 && dotIndex < name.length - 1) {
    return name.slice(dotIndex + 1).toUpperCase();
  }
  return genericLabel;
}

function MaterialEntryIcon({
  icon,
  className,
}: {
  icon: ReturnType<typeof materialFileIcon>;
  className?: string;
}) {
  return (
    <img
      src={icon.src}
      alt=""
      aria-hidden
      draggable={false}
      data-material-icon={icon.id}
      className={cn("shrink-0 select-none", className)}
    />
  );
}
