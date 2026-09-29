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

export async function fetchGitOverview(workspaceId: string, refresh = false): Promise<RepoOverview> {
  const query = new URLSearchParams({ workspace_id: workspaceId });
  if (refresh) query.set("refresh", "true");
  const res = await fetch(`/api/agentic-ide/git/overview?${query.toString()}`);
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
  return (await res.json()) as RepoOverview;
}
