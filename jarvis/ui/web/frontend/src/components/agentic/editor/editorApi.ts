/** The code editor's file reads and writes against an open workspace. */

/** A Python codec name: utf-8, utf-8-sig, utf-16-le, cp1252, … (see encodings.ts). */
export type TextEncoding = string;
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
  /** The file mixes line endings; a save writes `eol` throughout. */
  mixed_eol?: boolean;
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

/** `encoding` forces how the bytes are read ("Reopen with encoding"). */
export function loadTextFile(workspaceId: string, path: string, encoding?: string): Promise<TextFile> {
  const params = new URLSearchParams({ path });
  if (encoding) params.set("encoding", encoding);
  return send<TextFile>(`${base(workspaceId)}/text-file?${params.toString()}`);
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

/** The file's text at the last commit, or at commit `ref`; null for a file git does not know. */
export async function fetchHeadText(workspaceId: string, path: string, ref = ""): Promise<string | null> {
  const params = new URLSearchParams(ref ? { path, ref } : { path }).toString();
  const answer = await send<{ text: string | null }>(`${base(workspaceId)}/head-text?${params}`);
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

/** Copy a file or folder; `unique` picks a free "copy" name when the destination is taken. */
export function copyEntry(workspaceId: string, source: string, destination: string, unique = false): Promise<{ path: string }> {
  return send(`${base(workspaceId)}/entries/copy`, json("POST", { source, destination, unique }));
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

export interface SearchMatch {
  line: number;
  column: number;
  length: number;
  /** The line around the match, possibly trimmed. */
  preview: string;
  /** Where the match starts inside `preview`. */
  preview_start: number;
}

export interface SearchResult {
  results: { path: string; matches: SearchMatch[] }[];
  match_count: number;
  file_count: number;
  searched_files: number;
  truncated: boolean;
}

export interface SearchQuery {
  query: string;
  regex: boolean;
  caseSensitive: boolean;
  wholeWord: boolean;
  include: string;
  exclude: string;
}

export function searchWorkspace(workspaceId: string, search: SearchQuery, signal?: AbortSignal): Promise<SearchResult> {
  const params = new URLSearchParams({
    q: search.query,
    regex: String(search.regex),
    case: String(search.caseSensitive),
    word: String(search.wholeWord),
    include: search.include,
    exclude: search.exclude,
  });
  return send<SearchResult>(`${base(workspaceId)}/search?${params.toString()}`, { signal });
}

export interface ReplaceResult {
  replaced_files: string[];
  replacements: number;
  skipped: { path: string; reason: string }[];
}

export function replaceInFiles(
  workspaceId: string,
  search: SearchQuery,
  replacement: string,
  paths: string[],
): Promise<ReplaceResult> {
  return send<ReplaceResult>(
    `${base(workspaceId)}/search/replace`,
    json("POST", {
      query: search.query,
      replacement,
      regex: search.regex,
      case_sensitive: search.caseSensitive,
      whole_word: search.wholeWord,
      paths,
    }),
  );
}

export interface PythonPosition {
  path: string;
  text: string;
  /** 1-based, as Monaco counts. */
  line: number;
  column: number;
}

export interface PythonCompletion {
  name: string;
  /** Jedi's kind: module, class, function, instance, param, keyword, property, statement, path. */
  type: string;
  detail: string;
}

export interface PythonLocation {
  path: string;
  line: number;
  column: number;
}

/** Python completions, hover text or definitions for the live buffer. */
export async function pythonIntel<T>(
  workspaceId: string,
  action: "complete" | "hover" | "definition",
  position: PythonPosition,
  signal?: AbortSignal,
): Promise<T> {
  const answer = await send<{ result: T }>(`${base(workspaceId)}/python/${action}`, { ...json("POST", position), signal });
  return answer.result;
}
