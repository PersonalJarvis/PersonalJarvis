/**
 * What one pane's coding agent changed, for the pane's "Review changes" dialog.
 *
 * Two places a pane's work can live, and each has its own honest reading:
 *
 * - A pane on its own git worktree (a worktree fork) owns that checkout, so
 *   every uncommitted change in it is this agent's. Read through the folder
 *   routes (`/api/agentic-ide/git/changes` and `/diff`).
 * - A pane in the workspace's shared folder shares it with its neighbours.
 *   The workspace's Changes reading already names, per file, which panes'
 *   agents wrote it (read from each agent's own record), so this pane's
 *   changes are the files that name it. The whole folder stays one toggle away
 *   for a file a shell command wrote, which no agent record names.
 *
 * Only uncommitted work shows: once the agent commits, the change belongs to
 * a commit and the Git tab is where it is read.
 */
import {
  fetchFileDiff,
  fetchWorkspaceChanges,
  type ChangedFile,
  type FileDiff,
} from "./sidePanel/explorer/explorerApi";

/** Which files the dialog lists: this pane's own, or everything uncommitted in its folder. */
export type ChangeScope = "pane" | "folder";

export interface PaneChanges {
  available: boolean;
  /** Why `available` is false, in one plain sentence from the backend. */
  reason: string;
  branch: string;
  /** The files in the requested scope. */
  files: ChangedFile[];
  /** How many uncommitted files the folder has in all — the "whole folder" count. */
  folderTotal: number;
  /** True when the pane has a checkout of its own: every change there is its own. */
  ownCheckout: boolean;
  truncated: boolean;
}

/** Where a pane's work lives: its own worktree folder, else the workspace's shared one. */
export interface PaneChangesTarget {
  workspaceId: string;
  pane: string;
  /** Set only for a pane on its own git worktree. */
  folder?: string;
}

async function read<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    let message = `Request failed (${res.status}).`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
      else if (body.detail && typeof body.detail === "object" && "message" in body.detail) {
        message = String((body.detail as { message: unknown }).message);
      }
    } catch {
      /* keep the status-code message */
    }
    throw new Error(message);
  }
  return (await res.json()) as T;
}

interface FolderChangesBody {
  available: boolean;
  branch: string;
  files: ChangedFile[];
  truncated: boolean;
  reason: string;
}

/** The files whose agent records name `pane` as a writer. Directories are kept only when named too. */
export function filesWrittenBy(files: readonly ChangedFile[], pane: string): ChangedFile[] {
  return files.filter((file) => (file.authors ?? []).some((author) => author.pane === pane));
}

/** Lines added and removed across `files`; a file whose counts are unknown adds nothing. */
export function changeTotals(files: readonly ChangedFile[]): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const file of files) {
    added += file.added ?? 0;
    removed += file.removed ?? 0;
  }
  return { added, removed };
}

export async function fetchPaneChanges(target: PaneChangesTarget, scope: ChangeScope): Promise<PaneChanges> {
  if (target.folder) {
    const body = await read<FolderChangesBody>(
      `/api/agentic-ide/git/changes?${new URLSearchParams({ folder: target.folder }).toString()}`,
    );
    return {
      available: body.available,
      reason: body.reason,
      branch: body.branch,
      files: body.files,
      folderTotal: body.files.length,
      ownCheckout: true,
      truncated: body.truncated,
    };
  }
  const body = await fetchWorkspaceChanges(target.workspaceId);
  return {
    available: body.available,
    reason: body.reason,
    branch: body.branch,
    files: scope === "pane" ? filesWrittenBy(body.files, target.pane) : body.files,
    folderTotal: body.files.length,
    ownCheckout: false,
    truncated: body.truncated,
  };
}

export function fetchPaneFileDiff(target: PaneChangesTarget, path: string): Promise<FileDiff> {
  if (target.folder) {
    return read<FileDiff>(
      `/api/agentic-ide/git/diff?${new URLSearchParams({ folder: target.folder, path }).toString()}`,
    );
  }
  return fetchFileDiff(target.workspaceId, path);
}
