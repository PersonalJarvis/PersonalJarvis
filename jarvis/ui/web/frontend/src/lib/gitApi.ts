/**
 * Client for `/api/agentic-ide/git` — the Agentic IDE's Git panel and the
 * git options offered when a workspace or an agent is opened.
 *
 * Every call names the folder it works on (a workspace folder, a project path
 * or a worktree). A failed git call rejects with a {@link GitApiError} whose
 * `code` tells the UI which follow-up it can offer (`dirty` → "remove anyway?").
 */

export type GitPrepareMode =
  | "current"
  | "init"
  | "new_branch"
  | "switch_branch"
  | "new_worktree"
  | "open_worktree";

export interface GitChange {
  path: string;
  /** Git's own letters: M modified, A added, D deleted, R renamed, ? untracked, U conflicted, . unchanged. */
  index: string;
  worktree: string;
}

export interface GitBranch {
  name: string;
  current: boolean;
  upstream: string;
  committed_at: number;
  /** The checkout this branch is open in, if any. */
  worktree: string;
}

export interface GitWorktree {
  path: string;
  branch: string;
  head: string;
  main: boolean;
  detached: boolean;
  locked: boolean;
  prunable: boolean;
  current: boolean;
}

export interface GitRepoInfo {
  folder: string;
  git_available: boolean;
  gh_available: boolean;
  is_repo: boolean;
  root: string;
  main_root: string;
  is_worktree: boolean;
  branch: string;
  detached: boolean;
  unborn: boolean;
  head: string;
  upstream: string;
  ahead: number;
  behind: number;
  staged: number;
  unstaged: number;
  untracked: number;
  conflicted: number;
  insertions: number;
  deletions: number;
  dirty: boolean;
  default_branch: string;
  remotes: string[];
  branches: GitBranch[];
  remote_branches: string[];
  worktrees: GitWorktree[];
  changes: GitChange[];
  suggested_branch: string;
  worktree_dir: string;
}

/** What a workspace or agent should do with git before it starts. */
export interface GitPlan {
  mode: GitPrepareMode;
  branch: string;
  /** Base branch/commit for a new branch or worktree; the worktree path for `open_worktree`. */
  base: string;
}

export interface GitPrepared {
  folder: string;
  branch: string;
  created: boolean;
  message: string;
}

export class GitApiError extends Error {
  constructor(message: string, readonly code: string) {
    super(message);
    this.name = "GitApiError";
  }
}

const BASE = "/api/agentic-ide/git";

async function failure(res: Response): Promise<GitApiError> {
  // A backend that predates these routes: new code ships as a frontend bundle
  // that reloads on its own, while Python routes load at the next app start.
  if (res.status === 404) return new GitApiError("Git options load after the next app restart.", "unavailable");
  try {
    const body = (await res.json()) as { detail?: unknown };
    const detail = body.detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      const { message, code } = detail as { message: string; code?: string };
      return new GitApiError(message, code ?? "failed");
    }
    if (typeof detail === "string") return new GitApiError(detail, "failed");
  } catch {
    // Not JSON (a proxy error page): the status line is all there is to say.
  }
  return new GitApiError(`Git request failed (${res.status})`, "failed");
}

async function post<T>(path: string, body: object): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await failure(res);
  return (await res.json()) as T;
}

export async function inspectGit(folder: string, signal?: AbortSignal): Promise<GitRepoInfo> {
  const res = await fetch(`${BASE}/inspect?folder=${encodeURIComponent(folder)}`, { signal });
  if (!res.ok) throw await failure(res);
  return (await res.json()) as GitRepoInfo;
}

export function prepareGit(folder: string, plan: GitPlan): Promise<GitPrepared> {
  return post<GitPrepared>("/prepare", { folder, mode: plan.mode, branch: plan.branch, base: plan.base });
}

export function commitAll(folder: string, message: string): Promise<{ commit: string }> {
  return post("/commit", { folder, message });
}

export function pushBranch(folder: string): Promise<{ upstream: string }> {
  return post("/push", { folder });
}

export function fetchRemotes(folder: string): Promise<{ ok: boolean }> {
  return post("/fetch", { folder });
}

export function openPullRequest(folder: string, options: { title?: string; draft?: boolean } = {}): Promise<{ url: string }> {
  return post("/pull-request", { folder, title: options.title ?? "", draft: options.draft ?? false });
}

export function mergeBack(folder: string, into = ""): Promise<{ commit: string }> {
  return post("/merge", { folder, into });
}

export function removeWorktree(
  folder: string,
  worktree: string,
  options: { force?: boolean; deleteBranch?: boolean } = {},
): Promise<{ message: string }> {
  return post("/worktrees/remove", {
    folder, worktree, force: options.force ?? false, delete_branch: options.deleteBranch ?? false,
  });
}

export function pruneWorktrees(folder: string): Promise<{ ok: boolean }> {
  return post("/worktrees/prune", { folder });
}

/** The plan that changes nothing: run in the folder as it is. */
export const KEEP_CHECKOUT: GitPlan = { mode: "current", branch: "", base: "" };

/** The last path segment, for labels ("…/.worktrees/agent-x" → "agent-x"). */
export function folderName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? path;
}
