/**
 * The Git tab's one read: a workspace repository's branches with what each is
 * merged into, its pull requests and its CI (`GET /api/agentic-ide/git/overview`).
 *
 * Mirrors `jarvis/agentic_ide/git_overview.py`. Merged, pull request and CI
 * are three separate facts on purpose — passing checks never mean "merged".
 */

export type PullRequestState = "draft" | "open" | "queued" | "merged" | "closed";
export type CiState = "none" | "pending" | "running" | "success" | "failure";

export interface CiStatus {
  state: CiState;
  /** The failing / running workflow run, or the pull request's checks page. */
  url: string;
  total: number;
  passed: number;
  failed: number;
  running: number;
  pending: number;
  /** The commit the checks ran on (12 chars). */
  commit: string;
  /** Failed checks, or the running ones while nothing failed. */
  names: string[];
}

export interface PullRequest {
  number: number;
  title: string;
  url: string;
  state: PullRequestState;
  base: string;
  head: string;
  head_oid: string;
  merged_at: string;
  updated_at: string;
  queue_position: number | null;
  ci: CiStatus;
}

export interface MergedInto {
  target: string;
  /** `git`: its commits are in the target; `pull_request`: GitHub merged its PR. */
  via: "git" | "pull_request";
  number: number | null;
  url: string;
}

export interface BranchRow {
  name: string;
  current: boolean;
  remote_only: boolean;
  upstream: string;
  ahead: number;
  behind: number;
  head: string;
  committed_at: number;
  /** Another checkout this branch is open in. */
  worktree: string;
  on_github: boolean;
  merged_into: MergedInto[];
  /** Most relevant first: queued, open, draft, merged, closed. */
  pull_requests: PullRequest[];
  ci: CiStatus;
  /** Local commits CI has not seen yet. */
  ci_stale: boolean;
}

export interface GitHubState {
  available: boolean;
  reason: string;
  /** The next step the tab offers: connect GitHub, pick the folder's repository, or a failure code. */
  code: "" | "not_connected" | "needs_repo" | string;
  /** The `owner/name` picked for this folder. */
  repo: string;
  /** What the folder's git remote points at — the picker's recommendation. */
  suggested_repo: string;
  /** `app` (Plugins → GitHub) or `gh` (the GitHub CLI). */
  source: string;
  repo_url: string;
  /** Epoch seconds of the GitHub answer in use. */
  fetched_at: number;
}

export interface RepoOverview {
  available: boolean;
  reason: string;
  root: string;
  branch: string;
  detached: boolean;
  head: string;
  default_branch: string;
  branches: BranchRow[];
  remote_branches: BranchRow[];
  truncated: boolean;
  github: GitHubState;
}

/** A failed call; `routeMissing` means the running backend predates this tab (restart once). */
export class GitOverviewError extends Error {
  readonly routeMissing: boolean;

  constructor(message: string, routeMissing = false) {
    super(message);
    this.routeMissing = routeMissing;
  }
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let message = `Request failed (${res.status}).`;
    let detail: unknown;
    try {
      detail = ((await res.json()) as { detail?: unknown }).detail;
      if (typeof detail === "string") message = detail;
    } catch {
      /* keep the status-code message */
    }
    // FastAPI's own 404 for an unknown path says exactly "Not Found"; this
    // API's 404s always explain themselves ("Workspace not found.").
    throw new GitOverviewError(message, res.status === 404 && detail === "Not Found");
  }
  return (await res.json()) as T;
}

export function fetchGitOverview(workspaceId: string, refresh = false): Promise<RepoOverview> {
  const query = new URLSearchParams({ workspace_id: workspaceId });
  if (refresh) query.set("refresh", "true");
  return call<RepoOverview>(`/api/agentic-ide/git/overview?${query.toString()}`);
}

export interface GitHubRepoChoice {
  /** `owner/name`. */
  name: string;
  description: string;
  private: boolean;
  fork: boolean;
  url: string;
  pushed_at: string;
}

export interface GitHubRepoList {
  connected: boolean;
  source: string;
  login: string;
  repos: GitHubRepoChoice[];
  reason: string;
}

export function fetchGitHubRepos(refresh = false): Promise<GitHubRepoList> {
  return call<GitHubRepoList>(`/api/agentic-ide/git/github/repos${refresh ? "?refresh=true" : ""}`);
}

/** Remember which repository this workspace's folder is; `""` forgets it. */
export function bindGitHubRepo(workspaceId: string, repo: string): Promise<{ ok: boolean; repo: string }> {
  return call(`/api/agentic-ide/git/github/binding`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace_id: workspaceId, repo }),
  });
}
