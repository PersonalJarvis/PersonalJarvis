import { useEffect, useState } from "react";
import { ChevronLeft, FileDiff, Loader2, RefreshCw, X } from "lucide-react";
import { DiffView } from "@/components/agentic/sidePanel/explorer/DiffView";
import { fetchFolderChanges, fetchFolderDiff, type FolderChange, type FolderChanges, type FolderDiff } from "@/lib/gitApi";
import { cn } from "@/lib/utils";

const STATUS_MARK: Record<FolderChange["status"], { letter: string; className: string }> = {
  modified: { letter: "M", className: "text-warning" },
  added: { letter: "A", className: "text-success" },
  untracked: { letter: "U", className: "text-success" },
  deleted: { letter: "D", className: "text-destructive" },
  conflicted: { letter: "!", className: "text-destructive" },
};

/** Rows drawn at once; a tree with thousands of changes stays responsive. */
const SHOWN_FILES = 300;

function splitPath(path: string): { name: string; dir: string } {
  const parts = path.split("/");
  const name = parts.pop() ?? path;
  return { name, dir: parts.join("/") };
}

/**
 * The thread's own diff panel: every file changed under the thread's folder,
 * and one file's change against the last commit. It reads the thread's
 * folder — a worktree no workspace has open included — and reads again when
 * a turn ends (`version`).
 */
export function ThreadDiffPanel({ folder, version, onClose }: { folder: string; version: number; onClose: () => void }) {
  const [changes, setChanges] = useState<FolderChanges | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [nonce, setNonce] = useState(0);
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [diff, setDiff] = useState<FolderDiff | null>(null);
  const [diffError, setDiffError] = useState("");
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    fetchFolderChanges(folder, controller.signal)
      .then((next) => { if (!controller.signal.aborted) setChanges(next); })
      .catch((err: unknown) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [folder, version, nonce]);

  // Another thread's folder: nothing of the last one may show under its name.
  useEffect(() => {
    setOpenPath(null);
    setChanges(null);
    setShowAll(false);
  }, [folder]);

  useEffect(() => {
    if (!openPath) { setDiff(null); return; }
    const controller = new AbortController();
    setDiff(null);
    setDiffError("");
    fetchFolderDiff(folder, openPath, controller.signal)
      .then((next) => { if (!controller.signal.aborted) setDiff(next); })
      .catch((err: unknown) => { if (!controller.signal.aborted) setDiffError(err instanceof Error ? err.message : String(err)); });
    return () => controller.abort();
  }, [folder, openPath, version]);

  const files = (changes?.files ?? []).filter((file) => !file.is_directory);
  const added = files.reduce((sum, file) => sum + (file.added ?? 0), 0);
  const removed = files.reduce((sum, file) => sum + (file.removed ?? 0), 0);

  return <aside aria-label="Changes" data-testid="thread-diff-panel" className="flex h-full min-h-0 flex-col border-l border-border bg-background">
    <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
      {openPath
        ? <button type="button" aria-label="Back to the changed files" onClick={() => setOpenPath(null)}
          className="rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground"><ChevronLeft className="h-4 w-4" /></button>
        : <FileDiff aria-hidden className="h-4 w-4 text-muted-foreground" />}
      <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground-strong" title={openPath ?? undefined}>
        {openPath ? splitPath(openPath).name : "Changes"}
      </span>
      {!openPath && (added > 0 || removed > 0) && <span className="shrink-0 font-mono text-xs tabular-nums">
        <span className="text-success">+{added}</span> <span className="text-destructive">−{removed}</span>
      </span>}
      <button type="button" aria-label="Read the changes again" title="Refresh" onClick={() => setNonce((value) => value + 1)}
        className="rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground">
        {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
      </button>
      <button type="button" aria-label="Close changes" onClick={onClose} className="rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground"><X className="h-4 w-4" /></button>
    </div>
    <div className="min-h-0 flex-1 overflow-auto scrollbar-jarvis">
      {openPath
        ? diffError ? <p className="px-4 py-3 text-sm text-destructive">{diffError}</p>
          : !diff ? <p className="flex items-center gap-2 px-4 py-3 text-sm text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading the diff…</p>
            : diff.binary ? <p className="px-4 py-3 text-sm text-muted-foreground">A binary file — no line diff.</p>
              : diff.hunks.length === 0 ? <p className="px-4 py-3 text-sm text-muted-foreground">No changes in this file.</p>
                : <DiffView hunks={diff.hunks} />
        : error ? <p className="px-4 py-3 text-sm text-destructive">{error}</p>
          : changes && !changes.available ? <p className="px-4 py-3 text-sm text-muted-foreground">{changes.reason || "This folder is not a git repository."}</p>
            : files.length === 0 ? <p className="px-4 py-3 text-sm text-muted-foreground">{loading ? "Reading the changes…" : "No changes yet."}</p>
              : <ul className="py-1">
                {(showAll ? files : files.slice(0, SHOWN_FILES)).map((file) => {
                  const { name, dir } = splitPath(file.path);
                  const mark = STATUS_MARK[file.status] ?? STATUS_MARK.modified;
                  return <li key={file.path}>
                    <button type="button" onClick={() => setOpenPath(file.path)} title={file.path}
                      className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring">
                      <span className={cn("w-3 shrink-0 text-center font-mono text-xs", mark.className)}>{mark.letter}</span>
                      <span className="min-w-0 flex-1 truncate"><span className="text-foreground">{name}</span>{dir && <span className="ml-1.5 text-xs text-muted-foreground">{dir}</span>}</span>
                      <span className="shrink-0 font-mono text-xs tabular-nums">
                        {file.added != null && file.added > 0 && <span className="text-success">+{file.added}</span>}
                        {file.removed != null && file.removed > 0 && <span className="ml-1 text-destructive">−{file.removed}</span>}
                      </span>
                    </button>
                  </li>;
                })}
                {!showAll && files.length > SHOWN_FILES && <li>
                  <button type="button" onClick={() => setShowAll(true)} className="px-3 py-2 text-xs text-muted-foreground hover:text-foreground">
                    Show {files.length - SHOWN_FILES} more files
                  </button>
                </li>}
              </ul>}
    </div>
  </aside>;
}
