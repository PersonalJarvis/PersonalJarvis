/**
 * Review everything a pane's coding agent changed, in one place.
 *
 * Opened from the pane's title bar. Lists every uncommitted file the agent
 * wrote and shows each file's full diff against the last commit underneath,
 * one card per file, so a whole session's work reads top to bottom before it
 * is committed. The file list on the left jumps to a card.
 *
 * Which files are "the agent's" comes from ./paneChangesApi: a pane on its own
 * worktree owns everything uncommitted there; a pane in the shared folder owns
 * the files its agent's record names. A toggle widens the shared case to the
 * whole folder, because a file a shell command wrote is named by no record.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ChevronDown, FileDiff, GitBranch, Loader2, RefreshCw, SquareArrowOutUpRight, X } from "lucide-react";

import { fill, useLocaleChunk, useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useCodeEditorStore } from "@/store/codeEditor";
import { usePaneTitle } from "@/store/paneRecaps";
import {
  changeTotals,
  fetchPaneChanges,
  fetchPaneFileDiff,
  type ChangeScope,
  type PaneChanges,
  type PaneChangesTarget,
  type ReviewFile,
} from "./paneChangesApi";
import { DiffView } from "./sidePanel/explorer/DiffView";
import { FileTypeIcon } from "./sidePanel/explorer/FileTypeIcon";
import type { ChangeStatus, FileDiff as FileDiffBody } from "./sidePanel/explorer/explorerApi";

/** How many diffs are read at once — enough to fill the screen fast, few enough to leave git alone. */
const DIFF_CONCURRENCY = 4;

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

type DiffState =
  | { kind: "loading" }
  | { kind: "ready"; diff: FileDiffBody }
  | { kind: "error"; message: string };

type ListState =
  | { kind: "loading" }
  | { kind: "ready"; changes: PaneChanges }
  | { kind: "error"; message: string };

const baseName = (path: string) => path.split("/").filter(Boolean).pop() ?? path;
const parentPath = (path: string) => path.split("/").slice(0, -1).join("/");

function Counts({ added, removed, className }: { added: number | null; removed: number | null; className?: string }) {
  if (added === null && removed === null) return null;
  return (
    <span className={cn("shrink-0 font-mono text-xs tabular-nums", className)}>
      <span className="text-success">+{added ?? 0}</span>{" "}
      <span className="text-destructive">−{removed ?? 0}</span>
    </span>
  );
}

export function PaneChangesDialog({
  open,
  onOpenChange,
  workspaceId,
  pane,
  folder,
  branch,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspaceId: string;
  /** The pane's call-sign. */
  pane: string;
  /** Set only for a pane on its own git worktree. */
  folder?: string;
  branch?: string;
}): JSX.Element {
  const t = useT();
  // Until the strings land, `t()` answers with keys; the dialog waits for them.
  const stringsReady = useLocaleChunk("pane_review");
  const title = usePaneTitle(workspaceId, pane) || pane;
  const openFile = useCodeEditorStore((state) => state.openFile);
  const [scope, setScope] = useState<ChangeScope>("pane");
  const [revision, setRevision] = useState(0);
  const [list, setList] = useState<ListState>({ kind: "loading" });
  const [diffs, setDiffs] = useState<Record<string, DiffState>>({});
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set());
  const cards = useRef(new Map<string, HTMLElement>());

  // Read the list, then every file's diff a few at a time. A newer read (a
  // scope switch, Refresh, closing the dialog) cancels the older one.
  useEffect(() => {
    if (!open) return;
    const target: PaneChangesTarget = { workspaceId, pane, folder };
    let cancelled = false;
    setList({ kind: "loading" });
    setDiffs({});
    void (async () => {
      let changes: PaneChanges;
      try {
        changes = await fetchPaneChanges(target, scope);
      } catch (error) {
        if (!cancelled) setList({ kind: "error", message: error instanceof Error ? error.message : String(error) });
        return;
      }
      if (cancelled) return;
      setList({ kind: "ready", changes });
      const queue = changes.files.filter((file) => !file.is_directory).map((file) => file.path);
      setDiffs(Object.fromEntries(queue.map((path) => [path, { kind: "loading" } as DiffState])));
      const worker = async () => {
        for (let path = queue.shift(); path !== undefined && !cancelled; path = queue.shift()) {
          let next: DiffState;
          try {
            next = { kind: "ready", diff: await fetchPaneFileDiff(target, path, changes.base) };
          } catch (error) {
            next = { kind: "error", message: error instanceof Error ? error.message : String(error) };
          }
          const done = path;
          if (!cancelled) setDiffs((current) => ({ ...current, [done]: next }));
        }
      };
      await Promise.all(Array.from({ length: DIFF_CONCURRENCY }, worker));
    })();
    return () => {
      cancelled = true;
    };
  }, [open, workspaceId, pane, folder, scope, revision]);

  const toggle = useCallback((path: string) => {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const jumpTo = (path: string) => {
    setCollapsed((current) => {
      if (!current.has(path)) return current;
      const next = new Set(current);
      next.delete(path);
      return next;
    });
    cards.current.get(path)?.scrollIntoView({ block: "start", behavior: "smooth" });
  };

  const changes = list.kind === "ready" ? list.changes : null;
  const files = changes?.files ?? [];
  const totals = changeTotals(files);
  const fileCount = files.length === 1 ? t("pane_review.files_one") : fill(t("pane_review.files_other"), { count: files.length });
  // A worktree's files are relative to that worktree, not to the workspace the
  // editor opens files in, so only the shared folder offers "Open in editor".
  const canOpen = !folder;

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay onMouseDown={(event) => event.stopPropagation()} className="fixed inset-0 z-[80] bg-scrim/75 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid={`pane-changes-dialog-${pane}`}
          // React events bubble through the portal to the pane, whose own
          // mousedown makes it the prompt target; reading a diff is not that.
          onMouseDown={(event) => event.stopPropagation()}
          className={cn(
            "fixed left-1/2 top-1/2 z-[90] flex h-[min(88dvh,60rem)] w-[min(1200px,calc(100vw-2rem))]",
            "-translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-lg",
            "bg-popover shadow-float outline-none",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none",
          )}
        >
          {!stringsReady ? (
            <div className="flex flex-1 items-center justify-center">
              <Dialog.Title className="sr-only">{pane}</Dialog.Title>
              <Dialog.Description className="sr-only">{pane}</Dialog.Description>
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden="true" />
            </div>
          ) : (<>
          <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-border px-5 py-4">
            <div className="min-w-0 flex-1">
              <div className="mb-1 flex items-center gap-2">
                <FileDiff className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
                <Dialog.Title className="truncate font-display text-base font-semibold tracking-tight text-foreground">
                  {fill(t("pane_review.title"), { name: title })}
                </Dialog.Title>
              </div>
              <Dialog.Description className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-relaxed text-muted-foreground">
                {folder && (
                  <span className="inline-flex min-w-0 items-center gap-1 font-mono">
                    <GitBranch className="h-3 w-3 shrink-0" aria-hidden="true" />
                    {branch || changes?.branch}
                  </span>
                )}
                <span>
                  {scope === "folder" && !folder
                    ? t("pane_review.description_folder")
                    : changes?.base
                      ? changes.sinceMs > 0
                        ? fill(t("pane_review.description_since"), { time: new Date(changes.sinceMs).toLocaleString() })
                        : t("pane_review.description_pane")
                      : t("pane_review.description_uncommitted")}
                </span>
                {changes && files.length > 0 && (
                  <span data-testid="pane-changes-summary" className="inline-flex items-center gap-2 text-foreground-secondary">
                    <span aria-hidden="true">·</span>
                    {fileCount}
                    <Counts added={totals.added} removed={totals.removed} />
                  </span>
                )}
              </Dialog.Description>
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              {!folder && (
                <div role="radiogroup" aria-label={t("pane_review.scope_label")} className="flex rounded-lg bg-muted p-0.5 text-xs">
                  {(["pane", "folder"] as const).map((value) => (
                    <button
                      key={value}
                      type="button"
                      role="radio"
                      aria-checked={scope === value}
                      data-testid={`pane-changes-scope-${value}`}
                      onClick={() => setScope(value)}
                      className={cn(
                        "rounded-md px-2.5 py-1 transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                        scope === value ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      {value === "pane"
                        ? t("pane_review.scope_pane")
                        : changes?.folderTotal != null
                          ? fill(t("pane_review.scope_folder_count"), { count: changes.folderTotal })
                          : t("pane_review.scope_folder")}
                    </button>
                  ))}
                </div>
              )}
              <button
                type="button"
                aria-label={t("pane_review.refresh")}
                title={t("pane_review.refresh")}
                data-testid="pane-changes-refresh"
                onClick={() => setRevision((value) => value + 1)}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground active:translate-y-px"
              >
                <RefreshCw className="h-4 w-4" aria-hidden="true" />
              </button>
              <Dialog.Close asChild>
                <button
                  type="button"
                  aria-label={t("pane_review.close")}
                  className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground active:translate-y-px"
                >
                  <X className="h-4 w-4" aria-hidden="true" />
                </button>
              </Dialog.Close>
            </div>
          </header>

          {list.kind === "loading" ? (
            <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              {t("pane_review.loading")}
            </div>
          ) : list.kind === "error" ? (
            <p role="alert" className="flex flex-1 items-center justify-center px-6 text-center text-sm text-destructive">
              {fill(t("pane_review.load_failed"), { error: list.message })}
            </p>
          ) : !list.changes.available ? (
            <p className="flex flex-1 items-center justify-center px-6 text-center text-sm text-muted-foreground">
              {fill(t("pane_review.unavailable"), { reason: list.changes.reason })}
            </p>
          ) : files.length === 0 ? (
            <div data-testid="pane-changes-empty" className="flex flex-1 flex-col items-center justify-center gap-1.5 px-6 text-center">
              <p className="text-sm text-foreground">
                {t(scope === "pane" || folder ? "pane_review.empty_pane" : "pane_review.empty_folder")}
              </p>
              <p className="max-w-md text-xs text-muted-foreground">
                {t(scope === "pane" || folder ? "pane_review.empty_pane_hint" : "pane_review.empty_hint")}
              </p>
            </div>
          ) : (
            <div className="flex min-h-0 flex-1">
              <nav
                aria-label={t("pane_review.file_list")}
                className="hidden w-64 shrink-0 overflow-y-auto border-r border-border py-2 scrollbar-jarvis md:block"
              >
                <ul data-testid="pane-changes-list">
                  {files.map((file) => (
                    <li key={file.path}>
                      <button
                        type="button"
                        onClick={() => jumpTo(file.path)}
                        title={file.path}
                        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none"
                      >
                        <FileTypeIcon path={file.path} />
                        <span className="flex min-w-0 flex-1 flex-col leading-tight">
                          <span className={cn("truncate text-foreground", file.status === "deleted" && "line-through decoration-destructive/60")}>
                            {baseName(file.path)}
                          </span>
                          {parentPath(file.path) && (
                            <span className="truncate text-[11px] text-muted-foreground">{parentPath(file.path)}</span>
                          )}
                        </span>
                        <span
                          aria-label={t(`pane_review.status.${file.status}`)}
                          className={cn("w-3 shrink-0 text-center font-mono text-[11px] font-semibold", STATUS_TONE[file.status])}
                        >
                          {STATUS_LETTER[file.status]}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
                {changes?.truncated && (
                  <p className="px-3 pt-2 text-[11px] text-muted-foreground">{t("pane_review.truncated_list")}</p>
                )}
              </nav>
              <div className="min-w-0 flex-1 space-y-3 overflow-y-auto p-4 scrollbar-jarvis" data-testid="pane-changes-diffs">
                {files.map((file) => (
                  <FileCard
                    key={file.path}
                    file={file}
                    diff={diffs[file.path]}
                    collapsed={collapsed.has(file.path)}
                    onToggle={() => toggle(file.path)}
                    onOpen={canOpen && !file.is_directory && file.status !== "deleted"
                      ? () => {
                        onOpenChange(false);
                        openFile(workspaceId, file.path, { mode: "diff", preview: false });
                      }
                      : undefined}
                    cardRef={(node) => {
                      if (node) cards.current.set(file.path, node);
                      else cards.current.delete(file.path);
                    }}
                  />
                ))}
              </div>
            </div>
          )}
          </>)}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function FileCard({
  file,
  diff,
  collapsed,
  onToggle,
  onOpen,
  cardRef,
}: {
  file: ReviewFile;
  diff: DiffState | undefined;
  collapsed: boolean;
  onToggle: () => void;
  onOpen?: () => void;
  cardRef: (node: HTMLElement | null) => void;
}) {
  const t = useT();
  const ready = diff?.kind === "ready" ? diff.diff : null;
  return (
    <section
      ref={cardRef}
      data-testid="pane-changes-file"
      data-path={file.path}
      className="scroll-mt-4 overflow-hidden rounded-lg border border-border bg-card"
    >
      <div className="flex items-center gap-2 border-b border-border bg-muted/40 px-2 py-1.5">
        <button
          type="button"
          aria-expanded={!collapsed}
          aria-label={t(collapsed ? "pane_review.expand" : "pane_review.collapse")}
          onClick={onToggle}
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <ChevronDown className={cn("h-4 w-4 transition-transform", collapsed && "-rotate-90")} aria-hidden="true" />
        </button>
        <FileTypeIcon path={file.path} />
        <span className="flex min-w-0 flex-1 items-baseline gap-1.5 font-mono text-xs" title={file.path}>
          {parentPath(file.path) && (
            <span dir="rtl" className="min-w-0 shrink-[999] truncate text-left text-muted-foreground">
              <bdi dir="ltr">{parentPath(file.path)}/</bdi>
            </span>
          )}
          <span className={cn("min-w-0 shrink-0 truncate text-foreground", file.status === "deleted" && "line-through decoration-destructive/60")}>
            {baseName(file.path)}
          </span>
        </span>
        <span className={cn("shrink-0 text-[11px] font-medium", STATUS_TONE[file.status])}>
          {t(`pane_review.status.${file.status}`)}
        </span>
        {file.committed !== undefined && (
          <span
            data-testid="pane-changes-commit-state"
            className={cn(
              "shrink-0 rounded px-1.5 py-px text-[10.5px]",
              file.committed ? "bg-muted text-muted-foreground" : "bg-warning/15 text-warning",
            )}
          >
            {t(file.committed ? "pane_review.committed" : "pane_review.pending")}
          </span>
        )}
        <Counts added={ready ? ready.added : file.added} removed={ready ? ready.removed : file.removed} />
        {onOpen && (
          <button
            type="button"
            aria-label={t("pane_review.open_editor")}
            title={t("pane_review.open_editor")}
            onClick={onOpen}
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            <SquareArrowOutUpRight className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        )}
      </div>
      {!collapsed && (
        file.is_directory ? (
          <p className="px-4 py-3 text-xs text-muted-foreground">{t("pane_review.directory")}</p>
        ) : !diff || diff.kind === "loading" ? (
          <p className="flex items-center gap-2 px-4 py-3 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            {t("pane_review.loading_diff")}
          </p>
        ) : diff.kind === "error" ? (
          <p role="alert" className="px-4 py-3 text-xs text-destructive">{fill(t("pane_review.diff_failed"), { error: diff.message })}</p>
        ) : diff.diff.binary ? (
          <p className="px-4 py-3 text-xs text-muted-foreground">{t("pane_review.binary")}</p>
        ) : diff.diff.hunks.length === 0 ? (
          <p className="px-4 py-3 text-xs text-muted-foreground">{t("pane_review.no_lines")}</p>
        ) : (
          <>
            <div className="max-h-[70vh] overflow-auto scrollbar-jarvis">
              <DiffView hunks={diff.diff.hunks} />
            </div>
            {diff.diff.truncated && (
              <p className="border-t border-border px-4 py-2 text-xs text-muted-foreground">{t("pane_review.truncated_diff")}</p>
            )}
          </>
        )
      )}
    </section>
  );
}
