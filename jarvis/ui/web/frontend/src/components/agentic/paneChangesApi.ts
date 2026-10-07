/**
 * What one pane's coding agent changed, for the pane's "Review changes" dialog.
 *
 * "This agent" is everything the agent changed, committed or not. Agents
 * commit as they go, so the working tree's leftovers are usually empty by the
 * time someone looks. The pane route takes the files the agent's own record
 * names as written and compares each with the code before the agent's first
 * write (`base`), so committed work, pending work and new files all show.
 * A pane on its own worktree is read in that worktree.
 *
 * "Whole folder" is everything uncommitted in the shared folder, whoever
 * wrote it — the way to see a file a shell command changed, which no agent
 * record names.
 *
 * A backend older than the pane route (a running app updates its bundle
 * before its next restart loads new routes) gets the older reading: the
 * uncommitted files, filtered here.
 */
import {
  fetchFileDiff,
  fetchWorkspaceChanges,
  type ChangedFile,
  type FileDiff,
} from "./sidePanel/explorer/explorerApi";

/** Which files the dialog lists: this pane's own, or everything uncommitted in its folder. */
export type ChangeScope = "pane" | "folder";

/** A file's diff as the pane route carries it inline. */
export type InlineDiff = Omit<FileDiff, "workspace_id" | "path">;

/** A listed file; `committed` and `diff` are set by the pane route only. */
export interface ReviewFile extends ChangedFile {
  /** True when nothing of the change is left uncommitted. */
  committed?: boolean;
  /** Its diff, sent with the list so the review paints at once; absent = read on demand. */
  diff?: InlineDiff | null;
}

export interface PaneChanges {
  available: boolean;
  /** Why `available` is false, in one plain sentence from the backend. */
  reason: string;
  branch: string;
  /** The files in the requested scope. */
  files: ReviewFile[];
  /** How many uncommitted files the folder has in all, when that was read. */
  folderTotal: number | null;
  /** True when the pane has a checkout of its own: every change there is its own. */
  ownCheckout: boolean;
  truncated: boolean;
  /** The commit the pane's files are compared with; empty for an uncommitted-only reading. */
  base: string;
  /** When the agent first changed a file (epoch ms); 0 when unknown. */
  sinceMs: number;
  /** Generated files (build output) the agent changed, counted rather than listed. */
  generated: number;
}

/** Where a pane's work lives: its own worktree folder, else the workspace's shared one. */
export interface PaneChangesTarget {
  workspaceId: string;
  pane: string;
  /** Set only for a pane on its own git worktree. */
  folder?: string;
}

class HttpError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
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
    throw new HttpError(message, res.status);
  }
  return (await res.json()) as T;
}

interface ChangesBody {
  available: boolean;
  branch: string;
  files: ReviewFile[];
  truncated: boolean;
  reason: string;
  base?: string;
  since_ms?: number;
  generated?: number;
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

const paneUrl = (target: PaneChangesTarget, tail: string) =>
  `/api/agentic-ide/workspaces/${encodeURIComponent(target.workspaceId)}/terminals/${encodeURIComponent(target.pane)}/${tail}`;

function reading(body: ChangesBody, extra: Pick<PaneChanges, "folderTotal" | "ownCheckout">, files = body.files): PaneChanges {
  return {
    available: body.available,
    reason: body.reason,
    branch: body.branch,
    files,
    truncated: body.truncated,
    base: body.base ?? "",
    sinceMs: body.since_ms ?? 0,
    generated: body.generated ?? 0,
    ...extra,
  };
}

export async function fetchPaneChanges(target: PaneChangesTarget, scope: ChangeScope): Promise<PaneChanges> {
  const ownCheckout = Boolean(target.folder);
  if (scope === "pane" || ownCheckout) {
    try {
      return reading(await read<ChangesBody>(paneUrl(target, "changes")), { folderTotal: null, ownCheckout });
    } catch (error) {
      // FastAPI's own "Not Found" means no such route; the route's 404s say what is missing.
      if (!(error instanceof HttpError && error.status === 404 && error.message === "Not Found")) throw error;
    }
  }
  if (target.folder) {
    const body = await read<ChangesBody>(
      `/api/agentic-ide/git/changes?${new URLSearchParams({ folder: target.folder }).toString()}`,
    );
    return reading(body, { folderTotal: body.files.length, ownCheckout });
  }
  const body = await fetchWorkspaceChanges(target.workspaceId);
  const files = scope === "pane" ? filesWrittenBy(body.files, target.pane) : body.files;
  return reading(body, { folderTotal: body.files.length, ownCheckout }, files);
}

/**
 * The last reading per pane and scope, so the review opens already painted.
 *
 * Hovering the pane's title bar or its review button starts a reading
 * (`prefetchPaneChanges`); opening the review shows the newest reading at
 * once and reads again behind it, so what is on screen is never older than
 * the moment it was opened plus one round trip.
 */
interface CacheEntry {
  at: number;
  value?: PaneChanges;
  pending?: Promise<PaneChanges>;
}

/** A reading this young is reused as is instead of being read again. */
export const PANE_CHANGES_FRESH_MS = 5_000;

const readings = new Map<string, CacheEntry>();

const cacheKey = (target: PaneChangesTarget, scope: ChangeScope) =>
  `${target.workspaceId}|${target.pane}|${target.folder ?? ""}|${scope}`;

/** The newest finished reading, however old; null before the first one. */
export function cachedPaneChanges(target: PaneChangesTarget, scope: ChangeScope): PaneChanges | null {
  return readings.get(cacheKey(target, scope))?.value ?? null;
}

/**
 * Read the pane's changes, sharing a reading already under way and reusing
 * one younger than `PANE_CHANGES_FRESH_MS`; `force` (Refresh) always reads anew.
 */
export function loadPaneChanges(target: PaneChangesTarget, scope: ChangeScope, force = false): Promise<PaneChanges> {
  const key = cacheKey(target, scope);
  const entry = readings.get(key);
  if (entry?.pending && !force) return entry.pending;
  if (!force && entry?.value && Date.now() - entry.at < PANE_CHANGES_FRESH_MS) return Promise.resolve(entry.value);
  const pending = fetchPaneChanges(target, scope).then(
    (value) => {
      readings.set(key, { at: Date.now(), value });
      return value;
    },
    (error: unknown) => {
      readings.set(key, { at: entry?.at ?? 0, value: entry?.value });
      throw error;
    },
  );
  readings.set(key, { at: entry?.at ?? 0, value: entry?.value, pending });
  return pending;
}

/** Start reading a pane's own changes ahead of a click; failures stay silent until the review opens. */
export function prefetchPaneChanges(target: PaneChangesTarget): void {
  loadPaneChanges(target, "pane").catch(() => {
    /* the review reads again when it opens and shows the error there */
  });
}

/** Forget every reading — for tests. */
export function clearPaneChangesCache(): void {
  readings.clear();
}

/** One file's diff: against `base` when the pane route gave one, else against the last commit. */
export function fetchPaneFileDiff(target: PaneChangesTarget, path: string, base = ""): Promise<FileDiff> {
  if (base) return read<FileDiff>(`${paneUrl(target, "diff")}?${new URLSearchParams({ path, base }).toString()}`);
  if (target.folder) {
    return read<FileDiff>(
      `/api/agentic-ide/git/diff?${new URLSearchParams({ folder: target.folder, path }).toString()}`,
    );
  }
  return fetchFileDiff(target.workspaceId, path);
}
