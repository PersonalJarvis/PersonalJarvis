/** The explorer's git reads: what changed in a workspace, and one file's diff. */

export type ChangeStatus = "modified" | "added" | "deleted" | "untracked" | "conflicted";

/** A pane whose coding agent wrote a changed file, read from that agent's own record. */
export interface ChangeAuthor {
  /** The pane's call-sign, e.g. "T3". */
  pane: string;
  history_id: string;
  agent: string;
  display_name: string;
  /** When it last wrote the file (epoch ms); 0 when unknown. */
  last_edit_ms: number;
}

export interface ChangedFile {
  /** POSIX path relative to the workspace root. */
  path: string;
  status: ChangeStatus;
  added: number | null;
  removed: number | null;
  is_directory: boolean;
  /** Newest first; empty when no agent's record names the file (a shell edit, a closed pane). */
  authors?: ChangeAuthor[];
}

export interface WorkspaceChanges {
  workspace_id: string;
  available: boolean;
  branch: string;
  files: ChangedFile[];
  truncated: boolean;
  reason: string;
}

export interface DiffLine {
  kind: "add" | "del" | "ctx";
  text: string;
  old_no: number | null;
  new_no: number | null;
}

export interface DiffHunk {
  header: string;
  lines: DiffLine[];
}

export interface FileDiff {
  workspace_id: string;
  path: string;
  status: ChangeStatus | "unchanged";
  binary: boolean;
  added: number;
  removed: number;
  hunks: DiffHunk[];
  truncated: boolean;
}

async function read<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    let message = `Request failed (${res.status}).`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      /* keep the status-code message */
    }
    throw new Error(message);
  }
  return (await res.json()) as T;
}

const base = (workspaceId: string) => `/api/agentic-ide/workspaces/${encodeURIComponent(workspaceId)}`;

export function fetchWorkspaceChanges(workspaceId: string): Promise<WorkspaceChanges> {
  return read<WorkspaceChanges>(`${base(workspaceId)}/changes`);
}

export function fetchFileDiff(workspaceId: string, path: string): Promise<FileDiff> {
  return read<FileDiff>(`${base(workspaceId)}/diff?${new URLSearchParams({ path }).toString()}`);
}

/** The absolute path a terminal drop needs, from the workspace root and a relative path. */
export function absoluteWorkspacePath(root: string, relative: string): string {
  const separator = root.includes("\\") && !root.includes("/") ? "\\" : "/";
  const trimmed = root.replace(/[\\/]+$/, "");
  return `${trimmed}${separator}${relative.split("/").join(separator)}`;
}
