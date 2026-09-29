import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import {
  ArrowLeft,
  ChevronRight,
  ExternalLink,
  Folder,
  FolderOpen,
  GitBranch,
  Loader2,
  RefreshCw,
  Search,
  SquareArrowOutUpRight,
  X,
} from "lucide-react";
import { fill, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import {
  attachToTerminal,
  fetchWorkspaceFilePreview,
  fetchWorkspaceFiles,
  openTerminalTarget,
  workspaceFileUrl,
  type WorkspaceFileItem,
  type WorkspaceFilePreviewResponse,
} from "@/lib/agenticIdeApi";
import { WORKSPACE_PATH_TYPE } from "@/components/agentic/paneDrop";
import { AgentMark } from "@/components/agentic/AgentMark";
import { useEventStore } from "@/store/events";
import { useIdeChatStore } from "@/store/ideChat";
import { useIdeExplorerStore, type ExplorerView } from "@/store/ideExplorer";
import { paneTitleFrom, usePaneRecapPoll, usePaneRecapsStore } from "@/store/paneRecaps";
import { useWorkspacePanesStore } from "@/store/workspacePanes";
import { DiffView } from "./DiffView";
import {
  absoluteWorkspacePath,
  fetchFileDiff,
  fetchWorkspaceChanges,
  type ChangeAuthor,
  type ChangeStatus,
  type ChangedFile,
  type FileDiff,
  type WorkspaceChanges,
} from "./explorerApi";
import { fileIcon } from "./fileIcon";

/** How often git is asked what changed while the Changes or Folder tab is on screen. */
const CHANGES_POLL_MS = 6000;
const CHANGES_JITTER_MS = 1200;

/** Deleted is red; everything an agent added or edited is green. */
const STATUS_TONE: Record<ChangeStatus, string> = {
  modified: "text-success",
  added: "text-success",
  untracked: "text-success",
  deleted: "text-destructive",
  conflicted: "text-warning",
};
const STATUS_LETTER: Record<ChangeStatus, string> = {
  modified: "M",
  added: "A",
  untracked: "U",
  deleted: "D",
  conflicted: "!",
};

const baseName = (path: string) => path.split("/").filter(Boolean).pop() ?? path;
const parentPath = (path: string) => path.split("/").slice(0, -1).join("/");

/** Start a drag that a terminal pane turns into a file reference. */
function startFileDrag(event: DragEvent, absolute: string): void {
  event.dataTransfer.setData(WORKSPACE_PATH_TYPE, absolute);
  event.dataTransfer.setData("text/plain", absolute);
  event.dataTransfer.effectAllowed = "copy";
}

function useWorkspaceChanges(workspaceId: string | null) {
  const [changes, setChanges] = useState<WorkspaceChanges | null>(null);
  const [error, setError] = useState("");
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    if (!workspaceId) {
      setChanges(null);
      return;
    }
    let alive = true;
    let timer: number | undefined;
    const tick = async () => {
      if (useEventStore.getState().activeSection === "agentic-ide") {
        try {
          const next = await fetchWorkspaceChanges(workspaceId);
          if (alive) {
            setChanges(next);
            setError("");
          }
        } catch (err) {
          // Keep the last answer; the next tick tries again.
          if (alive) setError((err as Error).message);
        }
      }
      if (alive) timer = window.setTimeout(tick, CHANGES_POLL_MS + Math.random() * CHANGES_JITTER_MS);
    };
    void tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [workspaceId, nonce]);
  return { changes, error, refresh: () => setNonce((value) => value + 1) };
}

function ChangeCounts({ file }: { file: Pick<ChangedFile, "added" | "removed"> }) {
  if (file.added == null && file.removed == null) return null;
  return (
    <span className="shrink-0 font-mono text-[11px] tabular-nums">
      {file.added ? <span className="text-success">+{file.added}</span> : null}
      {file.added && file.removed ? " " : null}
      {file.removed ? <span className="text-destructive">−{file.removed}</span> : null}
    </span>
  );
}

function StatusLetter({ status }: { status: ChangeStatus }) {
  return (
    <span
      data-status={status}
      className={cn("w-3 shrink-0 text-center font-mono text-[11px] font-semibold", STATUS_TONE[status])}
    >
      {STATUS_LETTER[status]}
    </span>
  );
}

/**
 * Which coding agents wrote a changed file: each one's brand mark and the
 * pane's title (its call-sign's CLI when it has none yet), newest first.
 */
function ChangeAuthors({ authors, workspaceId }: { authors: ChangeAuthor[]; workspaceId: string }) {
  const t = useT();
  const recaps = usePaneRecapsStore((state) => (state.workspaceId === workspaceId ? state.byName : undefined));
  const rows = useWorkspacePanesStore((state) => state.panes);
  if (authors.length === 0) return null;
  const named = authors.map((author) => {
    const row = rows.find((pane) => pane.history_id === author.history_id);
    const title = paneTitleFrom(recaps?.[author.pane], row);
    return { ...author, label: title || author.display_name || author.agent };
  });
  const who = named.map((author) => `${author.label} (${author.pane}, ${author.display_name})`).join(", ");
  const [first, ...rest] = named;
  return (
    <span
      data-testid="explorer-change-authors"
      title={fill(t("ide_side_panel.explorer.changed_by"), { who })}
      className="flex min-w-0 max-w-[60%] shrink items-center gap-1 text-[10.5px] text-muted-foreground"
    >
      <span className="flex shrink-0 items-center -space-x-1">
        {named.slice(0, 3).map((author) => (
          <AgentMark
            key={author.history_id}
            agent={author.agent}
            label={author.display_name}
            size="sm"
            variant="plain"
            className="h-3.5 w-3.5"
          />
        ))}
      </span>
      <span className="truncate">{first.label}</span>
      {rest.length > 0 && <span className="shrink-0 tabular-nums">+{rest.length}</span>}
    </span>
  );
}

/**
 * The Changes tab (what the agents changed, and which agent) or the Folder tab
 * (the workspace's folder as a tree); either opens any file's diff — drag a
 * row onto a terminal to reference that file.
 */
export function ExplorerPanel({ view }: { view: ExplorerView }) {
  const t = useT();
  const workspace = useIdeChatStore((state) => state.workspace);
  const stagedPane = useIdeChatStore((state) => state.stagedPane);
  const opened = useIdeExplorerStore((state) => state.opened[view]);
  const openIn = useIdeExplorerStore((state) => state.open);
  const closeIn = useIdeExplorerStore((state) => state.close);
  const close = () => closeIn(view);
  // Pane titles for the "changed by" line.
  usePaneRecapPoll();
  const pushToast = useEventStore((state) => state.pushToast);
  const [filter, setFilter] = useState("");
  const workspaceId = workspace?.id ?? null;
  const { changes, error: changesError, refresh } = useWorkspaceChanges(workspaceId);

  const changeMap = useMemo(() => {
    const map = new Map<string, ChangedFile>();
    for (const file of changes?.files ?? []) map.set(file.path, file);
    return map;
  }, [changes]);
  // Every folder that holds a change, so a collapsed tree still shows where to look.
  const changedFolders = useMemo(() => {
    const folders = new Set<string>();
    for (const file of changes?.files ?? []) {
      const parts = file.path.split("/");
      for (let index = 1; index < parts.length; index += 1) folders.add(parts.slice(0, index).join("/"));
      if (file.is_directory) folders.add(file.path);
    }
    return folders;
  }, [changes]);

  if (!workspace || !workspaceId) {
    return <p className="px-4 py-3 text-sm text-muted-foreground">{t("ide_side_panel.explorer.no_workspace")}</p>;
  }

  const absolute = (relative: string) => absoluteWorkspacePath(workspace.path, relative);
  const referenceInPane = async (relative: string) => {
    if (!stagedPane) return;
    try {
      await attachToTerminal(stagedPane, { paths: [absolute(relative)] });
    } catch (error) {
      pushToast("error", (error as Error).message);
    }
  };

  if (opened && opened.workspaceId === workspaceId) {
    return (
      <FileViewer
        workspaceId={workspaceId}
        path={opened.path}
        change={changeMap}
        onBack={close}
        onReference={stagedPane ? referenceInPane : undefined}
        referenceLabel={stagedPane ? fill(t("ide_side_panel.explorer.reference_in"), { pane: stagedPane }) : ""}
        onOpenExternally={(path) =>
          void openTerminalTarget(workspaceId, path).catch((error: unknown) =>
            pushToast("error", (error as Error).message),
          )
        }
        absolute={absolute}
      />
    );
  }

  const needle = filter.trim().toLowerCase();
  const changedFiles = (changes?.files ?? []).filter((file) => !needle || file.path.toLowerCase().includes(needle));
  const openFile = (path: string) => openIn(view, { workspaceId, path });
  const changeCount = changes?.files.length ?? 0;

  return (
    <section
      data-testid="ide-explorer"
      data-view={view}
      aria-label={t(view === "changes" ? "ide_side_panel.tabs.changes" : "ide_side_panel.tabs.files")}
      className="flex h-full min-h-0 flex-col"
    >
      <div className="shrink-0 space-y-2 border-b border-border/60 px-3 pb-2.5 pt-3">
        <div className="flex items-center gap-2">
          <FolderOpen className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          <span className="min-w-0 truncate text-sm font-semibold text-foreground" title={workspace.path}>
            {baseName(workspace.path.replace(/\\/g, "/")) || workspace.name}
          </span>
          {changes?.branch && (
            <span
              data-testid="explorer-branch"
              className="flex min-w-0 shrink items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
            >
              <GitBranch className="h-3 w-3 shrink-0" aria-hidden />
              <span className="truncate">{changes.branch}</span>
            </span>
          )}
          {view === "changes" && changeCount > 0 && (
            <span
              data-testid="explorer-change-count"
              className="shrink-0 rounded bg-success/15 px-1 text-[10px] tabular-nums text-success"
            >
              {changeCount}
            </span>
          )}
          <button
            type="button"
            onClick={refresh}
            aria-label={t("ide_side_panel.explorer.refresh")}
            title={t("ide_side_panel.explorer.refresh")}
            className="ml-auto inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
          </button>
        </div>
        <label className="flex h-8 items-center gap-2 rounded-lg border border-border/60 bg-background/50 px-2.5 focus-within:border-ring">
          <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <input
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder={t("ide_side_panel.explorer.filter")}
            aria-label={t("ide_side_panel.explorer.filter")}
            className="min-w-0 flex-1 bg-transparent text-xs text-foreground outline-none placeholder:text-muted-foreground"
          />
          {filter && (
            <button type="button" aria-label={t("ide_side_panel.explorer.clear_filter")} onClick={() => setFilter("")} className="text-muted-foreground hover:text-foreground">
              <X className="h-3.5 w-3.5" aria-hidden />
            </button>
          )}
        </label>
      </div>

      <div className="scrollbar-jarvis min-h-0 flex-1 overflow-y-auto py-1.5">
        {view === "changes" ? (
          !changes && changesError ? (
            <p className="px-4 py-3 text-xs text-muted-foreground">
              {fill(t("ide_side_panel.explorer.changes_error"), { error: changesError })}
            </p>
          ) : !changes ? (
            <p className="flex items-center gap-2 px-4 py-3 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              {t("ide_side_panel.explorer.loading")}
            </p>
          ) : !changes.available ? (
            <p className="px-4 py-3 text-xs text-muted-foreground">{changes.reason || t("ide_side_panel.explorer.no_git")}</p>
          ) : changedFiles.length === 0 ? (
            <p className="px-4 py-3 text-xs text-muted-foreground">
              {needle ? t("ide_side_panel.explorer.no_match") : t("ide_side_panel.explorer.clean")}
            </p>
          ) : (
            <ul data-testid="explorer-changes">
              {changedFiles.map((file) => {
                const Icon = file.is_directory ? Folder : fileIcon(file.path);
                const authors = file.authors ?? [];
                return (
                  <li key={file.path}>
                    <button
                      type="button"
                      draggable
                      onDragStart={(event) => startFileDrag(event, absolute(file.path))}
                      onClick={() => !file.is_directory && openFile(file.path)}
                      data-testid="explorer-change-row"
                      data-path={file.path}
                      title={`${file.path} — ${t("ide_side_panel.explorer.drag_hint")}`}
                      className="group flex min-h-9 w-full items-center gap-2 px-3 py-1 text-left hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none"
                    >
                      <Icon className={cn("h-4 w-4 shrink-0", STATUS_TONE[file.status])} aria-hidden />
                      <span className="flex min-w-0 flex-1 flex-col leading-tight">
                        <span className={cn("truncate text-[13px] text-foreground", file.status === "deleted" && "line-through decoration-destructive/60")}>
                          {baseName(file.path)}
                        </span>
                        {(parentPath(file.path) || authors.length > 0) && (
                          <span className="flex min-w-0 items-center gap-1.5">
                            {parentPath(file.path) && (
                              <span className="min-w-0 truncate text-[10.5px] text-muted-foreground">{parentPath(file.path)}</span>
                            )}
                            {parentPath(file.path) && authors.length > 0 && (
                              <span className="shrink-0 text-[10.5px] text-muted-foreground/60" aria-hidden>
                                ·
                              </span>
                            )}
                            <ChangeAuthors authors={authors} workspaceId={workspaceId} />
                          </span>
                        )}
                      </span>
                      <ChangeCounts file={file} />
                      <StatusLetter status={file.status} />
                    </button>
                  </li>
                );
              })}
              {changes.truncated && <li className="px-3 py-2 text-[11px] text-muted-foreground">{t("ide_side_panel.explorer.truncated")}</li>}
            </ul>
          )
        ) : (
          <FileTree
            workspaceId={workspaceId}
            filter={needle}
            changeMap={changeMap}
            changedFolders={changedFolders}
            onOpen={openFile}
            absolute={absolute}
          />
        )}
      </div>
    </section>
  );
}

interface TreeProps {
  workspaceId: string;
  filter: string;
  changeMap: Map<string, ChangedFile>;
  changedFolders: Set<string>;
  onOpen: (path: string) => void;
  absolute: (relative: string) => string;
}

/** The folder, one lazily loaded level at a time. */
function FileTree({ workspaceId, filter, changeMap, changedFolders, onOpen, absolute }: TreeProps) {
  const t = useT();
  const [children, setChildren] = useState<Record<string, WorkspaceFileItem[]>>({});
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set([""]));
  const [loading, setLoading] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState("");
  const loaded = useRef<Set<string>>(new Set());

  const load = useCallback(
    async (path: string) => {
      setLoading((current) => new Set(current).add(path));
      try {
        const answer = await fetchWorkspaceFiles(workspaceId, path);
        loaded.current.add(path);
        setChildren((current) => ({ ...current, [path]: answer.entries }));
        if (answer.error) setError(answer.error);
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setLoading((current) => {
          const next = new Set(current);
          next.delete(path);
          return next;
        });
      }
    },
    [workspaceId],
  );

  useEffect(() => {
    loaded.current = new Set();
    setChildren({});
    setExpanded(new Set([""]));
    void load("");
  }, [load]);

  const toggle = (path: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else {
        next.add(path);
        if (!loaded.current.has(path)) void load(path);
      }
      return next;
    });
  };

  const rows: { item: WorkspaceFileItem; depth: number }[] = [];
  const walk = (path: string, depth: number) => {
    for (const item of sortEntries(children[path] ?? [])) {
      const matches = !filter || item.name.toLowerCase().includes(filter);
      if (matches || item.is_directory) {
        if (matches) rows.push({ item, depth });
        if (item.is_directory && expanded.has(item.path)) walk(item.path, matches ? depth + 1 : depth);
      }
    }
  };
  walk("", 0);

  if (!children[""] && loading.has("")) {
    return (
      <p className="flex items-center gap-2 px-4 py-3 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
        {t("ide_side_panel.explorer.loading")}
      </p>
    );
  }

  return (
    <ul data-testid="explorer-tree" role="tree" aria-label={t("ide_side_panel.explorer.view_files")}>
      {error && <li className="px-3 py-2 text-[11px] text-destructive">{error}</li>}
      {rows.map(({ item, depth }) => {
        const change = changeMap.get(item.path);
        const open = expanded.has(item.path);
        const Icon = item.is_directory ? (open ? FolderOpen : Folder) : fileIcon(item.name);
        const hasChangeInside = item.is_directory && changedFolders.has(item.path);
        return (
          <li key={item.path} role="treeitem" aria-expanded={item.is_directory ? open : undefined}>
            <button
              type="button"
              draggable
              onDragStart={(event) => startFileDrag(event, absolute(item.path))}
              onClick={() => (item.is_directory ? toggle(item.path) : onOpen(item.path))}
              data-testid="explorer-tree-row"
              data-path={item.path}
              title={item.path}
              style={{ paddingLeft: 10 + depth * 14 }}
              className="flex h-7 w-full items-center gap-1.5 pr-3 text-left text-[13px] hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none"
            >
              <ChevronRight
                aria-hidden
                className={cn(
                  "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-150 motion-reduce:transition-none",
                  !item.is_directory && "invisible",
                  open && "rotate-90",
                )}
              />
              <Icon
                aria-hidden
                className={cn(
                  "h-4 w-4 shrink-0",
                  change ? STATUS_TONE[change.status] : item.is_directory ? "text-muted-foreground" : "text-muted-foreground/80",
                )}
              />
              <span className={cn("min-w-0 flex-1 truncate", change ? STATUS_TONE[change.status] : "text-foreground/90")}>
                {item.name}
              </span>
              {loading.has(item.path) && <Loader2 className="h-3 w-3 shrink-0 animate-spin text-muted-foreground" aria-hidden />}
              {hasChangeInside && !change && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-success/80" aria-hidden />}
              {change && <StatusLetter status={change.status} />}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/** Tool-owned folders nobody browses; the agents' own config folders stay visible. */
const HIDDEN_ENTRIES = new Set([".git", "__pycache__", ".DS_Store", "Thumbs.db", ".pytest_cache", ".mypy_cache", ".ruff_cache"]);

function sortEntries(entries: WorkspaceFileItem[]): WorkspaceFileItem[] {
  return entries
    .filter((entry) => !HIDDEN_ENTRIES.has(entry.name))
    .sort((a, b) =>
      a.is_directory === b.is_directory
        ? a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" })
        : a.is_directory
          ? -1
          : 1,
    );
}

const IMAGE_EXTENSIONS = /\.(png|jpe?g|gif|webp|svg|bmp|ico|avif)$/i;

interface ViewerProps {
  workspaceId: string;
  path: string;
  change: Map<string, ChangedFile>;
  onBack: () => void;
  onReference?: (relative: string) => void;
  referenceLabel: string;
  onOpenExternally: (path: string) => void;
  absolute: (relative: string) => string;
}

/** One file: its diff when it changed, else its contents. */
function FileViewer({ workspaceId, path, change, onBack, onReference, referenceLabel, onOpenExternally, absolute }: ViewerProps) {
  const t = useT();
  const [diff, setDiff] = useState<FileDiff | null>(null);
  const [preview, setPreview] = useState<WorkspaceFilePreviewResponse | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setDiff(null);
    setPreview(null);
    setError("");
    void (async () => {
      try {
        const answer = await fetchFileDiff(workspaceId, path);
        if (!alive) return;
        setDiff(answer);
        if (answer.hunks.length === 0 && !answer.binary && answer.status !== "deleted") {
          const body = await fetchWorkspaceFilePreview(workspaceId, answer.path);
          if (alive) setPreview(body);
        }
      } catch (err) {
        if (alive) setError((err as Error).message);
      }
    })();
    return () => {
      alive = false;
    };
  }, [workspaceId, path]);

  const relative = diff?.path ?? path;
  const Icon = fileIcon(baseName(relative));
  const known = change.get(relative);
  const status = diff && diff.status !== "unchanged" ? diff.status : known?.status;

  return (
    <section data-testid="explorer-viewer" data-path={relative} className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-border/60 px-2 py-2">
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={onBack}
            aria-label={t("ide_side_panel.explorer.back")}
            title={t("ide_side_panel.explorer.back")}
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden />
          </button>
          <span
            draggable
            onDragStart={(event) => startFileDrag(event, absolute(relative))}
            title={t("ide_side_panel.explorer.drag_hint")}
            className="flex min-w-0 flex-1 cursor-grab items-center gap-2"
          >
            <Icon className={cn("h-4 w-4 shrink-0", status ? STATUS_TONE[status as ChangeStatus] : "text-muted-foreground")} aria-hidden />
            <span className="flex min-w-0 flex-col leading-tight">
              <span className="truncate text-[13px] font-semibold text-foreground">{baseName(relative)}</span>
              {parentPath(relative) && <span className="truncate text-[10.5px] text-muted-foreground">{parentPath(relative)}</span>}
            </span>
          </span>
          {diff && (diff.added > 0 || diff.removed > 0) && <ChangeCounts file={diff} />}
        </div>
        <div className="mt-2 flex flex-wrap gap-1.5 pl-8">
          {onReference && (
            <button
              type="button"
              data-testid="explorer-reference"
              onClick={() => onReference(relative)}
              className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border/70 px-2 text-xs text-foreground hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <SquareArrowOutUpRight className="h-3.5 w-3.5" aria-hidden />
              {referenceLabel}
            </button>
          )}
          <button
            type="button"
            onClick={() => onOpenExternally(relative)}
            className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border/70 px-2 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            {t("ide_side_panel.explorer.open_externally")}
          </button>
        </div>
      </div>
      <div className="scrollbar-jarvis min-h-0 flex-1 overflow-auto">
        {error ? (
          <p className="px-4 py-3 text-xs text-destructive">{error}</p>
        ) : !diff ? (
          <p className="flex items-center gap-2 px-4 py-3 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            {t("ide_side_panel.explorer.loading")}
          </p>
        ) : diff.binary ? (
          IMAGE_EXTENSIONS.test(relative) && diff.status !== "deleted" ? (
            <img src={workspaceFileUrl(workspaceId, relative)} alt={baseName(relative)} className="m-3 max-w-[calc(100%-24px)] rounded-md border border-border/60" />
          ) : (
            <p className="px-4 py-3 text-xs text-muted-foreground">{t("ide_side_panel.explorer.binary")}</p>
          )
        ) : diff.hunks.length > 0 ? (
          <>
            <DiffView hunks={diff.hunks} />
            {diff.truncated && <p className="px-4 py-2 text-[11px] text-muted-foreground">{t("ide_side_panel.explorer.truncated")}</p>}
          </>
        ) : IMAGE_EXTENSIONS.test(relative) ? (
          <img src={workspaceFileUrl(workspaceId, relative)} alt={baseName(relative)} className="m-3 max-w-[calc(100%-24px)] rounded-md border border-border/60" />
        ) : preview?.text != null ? (
          <div className="min-w-max py-1 font-mono text-[12px] leading-[1.55]">
            <p className="px-3 pb-1 font-sans text-[11px] text-muted-foreground">{t("ide_side_panel.explorer.unchanged")}</p>
            {preview.text.split("\n").map((line, index) => (
              <div key={index} className="flex">
                <span className="w-10 shrink-0 select-none pr-3 text-right tabular-nums text-muted-foreground/70">{index + 1}</span>
                <span className="whitespace-pre pr-4 text-foreground/85">{line || " "}</span>
              </div>
            ))}
          </div>
        ) : preview ? (
          <p className="px-4 py-3 text-xs text-muted-foreground">{t("ide_side_panel.explorer.binary")}</p>
        ) : (
          <p className="flex items-center gap-2 px-4 py-3 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            {t("ide_side_panel.explorer.loading")}
          </p>
        )}
      </div>
    </section>
  );
}
