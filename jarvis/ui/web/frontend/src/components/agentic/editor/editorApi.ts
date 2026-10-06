/** The code editor's file reads and writes against an open workspace. */

export type TextEncoding = "utf-8" | "utf-8-sig";
export type LineEnding = "\n" | "\r\n";

export interface TextFile {
  workspace_id: string;
  /** POSIX path relative to the workspace root. */
  path: string;
  /** Exact file text; null when the file is binary or too large to edit. */
  text: string | null;
  /** Content hash; a save names it so an agent's edit is never overwritten. */
  version: string;
  size: number;
  encoding: TextEncoding;
  eol: LineEnding;
  binary: boolean;
  too_large: boolean;
}

/** A save was refused because the file changed on disk since it was loaded. */
export class SaveConflictError extends Error {
  constructor(
    message: string,
    /** The file's version now; null when it was deleted. */
    readonly currentVersion: string | null,
  ) {
    super(message);
    this.name = "SaveConflictError";
  }
}

/** A delete was refused because this computer has no trash to move it to. */
export class TrashUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TrashUnavailableError";
  }
}

const base = (workspaceId: string) => `/api/agentic-ide/workspaces/${encodeURIComponent(workspaceId)}`;
const query = (path: string) => new URLSearchParams({ path }).toString();

async function failure(res: Response): Promise<{ message: string; detail: unknown }> {
  let detail: unknown = null;
  try {
    detail = ((await res.json()) as { detail?: unknown }).detail ?? null;
  } catch {
    /* the status code is all there is */
  }
  const message =
    typeof detail === "string"
      ? detail
      : detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string"
        ? (detail as { message: string }).message
        : `Request failed (${res.status}).`;
  return { message, detail };
}

async function send<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { cache: "no-store", ...init });
  if (!res.ok) throw new Error((await failure(res)).message);
  return (await res.json()) as T;
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export function loadTextFile(workspaceId: string, path: string): Promise<TextFile> {
  return send<TextFile>(`${base(workspaceId)}/text-file?${query(path)}`);
}

/** The file's version on disk now; null when it no longer exists. */
export async function fetchFileVersion(workspaceId: string, path: string): Promise<string | null> {
  const answer = await send<{ version: string | null }>(`${base(workspaceId)}/text-file/version?${query(path)}`);
  return answer.version;
}

export interface SaveRequest {
  path: string;
  text: string;
  expectedVersion: string | null;
  encoding: TextEncoding;
  /** Allowed to create the file when it does not exist (a new or deleted file). */
  create?: boolean;
}

export async function saveTextFile(workspaceId: string, request: SaveRequest): Promise<TextFile> {
  const res = await fetch(
    `${base(workspaceId)}/text-file`,
    json("PUT", {
      path: request.path,
      text: request.text,
      expected_version: request.expectedVersion,
      encoding: request.encoding,
      create: request.create ?? false,
    }),
  );
  if (res.status === 409) {
    const { message, detail } = await failure(res);
    const current = (detail as { current_version?: string | null } | null)?.current_version ?? null;
    throw new SaveConflictError(message, current);
  }
  if (!res.ok) throw new Error((await failure(res)).message);
  return (await res.json()) as TextFile;
}

/** The file's text at the last commit; null for a file git does not know. */
export async function fetchHeadText(workspaceId: string, path: string): Promise<string | null> {
  const answer = await send<{ text: string | null }>(`${base(workspaceId)}/head-text?${query(path)}`);
  return answer.text;
}

export interface FileList {
  paths: string[];
  truncated: boolean;
}

export function fetchFileList(workspaceId: string): Promise<FileList> {
  return send<FileList>(`${base(workspaceId)}/file-list`);
}

export function createEntry(workspaceId: string, path: string, kind: "file" | "directory"): Promise<{ path: string }> {
  return send(`${base(workspaceId)}/entries`, json("POST", { path, kind }));
}

export function moveEntry(workspaceId: string, source: string, destination: string): Promise<{ path: string }> {
  return send(`${base(workspaceId)}/entries/move`, json("POST", { source, destination }));
}

/** Move to the system trash, or delete for good when `permanent` is set. */
export async function deleteEntry(workspaceId: string, path: string, permanent = false): Promise<{ trashed: boolean }> {
  const res = await fetch(`${base(workspaceId)}/entries/delete`, json("POST", { path, permanent }));
  if (res.status === 409) {
    const { message, detail } = await failure(res);
    if ((detail as { trash_unavailable?: boolean } | null)?.trash_unavailable) throw new TrashUnavailableError(message);
    throw new Error(message);
  }
  if (!res.ok) throw new Error((await failure(res)).message);
  return (await res.json()) as { trashed: boolean };
}

export interface EditorTabRecord {
  path: string;
  mode: "edit" | "diff";
  preview: boolean;
}

export interface EditorBackup {
  path: string;
  text: string;
  /** The file version the unsaved edits were made on; null for a file not on disk. */
  base_version: string | null;
  encoding: TextEncoding;
  saved_at: number;
}

export interface EditorState {
  tabs: EditorTabRecord[];
  active: string | null;
  backups: EditorBackup[];
}

export function fetchEditorState(workspaceId: string): Promise<EditorState> {
  return send<EditorState>(`${base(workspaceId)}/editor-state`);
}

/** `keepalive` lets the request finish while the window is closing. */
export async function putEditorTabs(
  workspaceId: string,
  tabs: EditorTabRecord[],
  active: string | null,
  keepalive = false,
): Promise<void> {
  await send(`${base(workspaceId)}/editor-state/tabs`, { ...json("PUT", { tabs, active }), keepalive });
}

export async function putEditorBackup(
  workspaceId: string,
  backup: Omit<EditorBackup, "saved_at">,
  keepalive = false,
): Promise<void> {
  await send(`${base(workspaceId)}/editor-state/backup`, { ...json("PUT", backup), keepalive });
}

export async function deleteEditorBackup(workspaceId: string, path: string): Promise<void> {
  await send(`${base(workspaceId)}/editor-state/backup?${query(path)}`, { method: "DELETE" });
}
