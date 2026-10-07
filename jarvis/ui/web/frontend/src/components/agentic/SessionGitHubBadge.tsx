import { openExternalUrl } from "@/lib/openExternal";
import { GitHubIcon, type GitHubIconName } from "./GitHubIcon";
import { PANE_BRAND, type TerminalAppearance } from "./terminalThemes";
import { useSessionGitHub, type SessionGitHubStatus } from "./useSessionGitHub";

const PR: Record<SessionGitHubStatus["state"], [GitHubIconName, string]> = {
  branch: ["git-branch", "Published branch"],
  draft: ["git-pull-request-draft", "Draft pull request"],
  open: ["git-pull-request", "Open pull request"],
  queued: ["git-merge-queue", "In the merge queue"],
  merged: ["git-merge", "Pull request merged"],
  closed: ["git-pull-request-closed", "Closed without merging"],
};

export function GitHubStatusBadge({ status, appearance }: {
  status: SessionGitHubStatus | null;
  appearance: TerminalAppearance;
}) {
  if (!status?.owned || !status.published) return null;
  if (!/^[A-Za-z0-9][A-Za-z0-9-]*\/[A-Za-z0-9._-]+$/.test(status.repo) || !status.branch) return null;
  // The PR may belong to an upstream repository when this branch is a fork.
  const pullRequest = /^https:\/\/github\.com\/[A-Za-z0-9][A-Za-z0-9-]*\/[A-Za-z0-9._-]+\/pull\/([1-9]\d*)$/.exec(status.url);
  const url = pullRequest ? status.url : `https://github.com/${status.repo}/tree/${encodeURIComponent(status.branch)}`;
  const destination = pullRequest ? `pull request #${pullRequest[1]}` : "branch";
  const unavailable = !status.available || Date.now() / 1000 - status.fetched_at > 60;
  const [icon, label] = unavailable
    ? ["git-branch" as const, "GitHub status unavailable"]
    : PR[status.state] ?? ["git-branch" as const, "GitHub branch"];
  const identity = `${status.repo} · ${status.branch}`;
  return <a data-header-control="true" data-testid="session-github-status"
    href={url} target="_blank" rel="noopener noreferrer"
    aria-label={`Open GitHub ${destination}: ${identity}`} title={`${identity}\n${label}\nOpen ${destination}`}
    className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded opacity-75 transition-opacity hover:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-current"
    style={{ color: PANE_BRAND[appearance].inkMuted }}
    onPointerDown={(event) => event.stopPropagation()}
    onClick={(event) => {
      event.preventDefault();
      event.stopPropagation();
      // WebView2 drops target=_blank: use the desktop's real-browser bridge.
      void openExternalUrl(url);
    }}>
    <GitHubIcon name={icon} width={16} height={16} className="block shrink-0" />
  </a>;
}

export function SessionGitHubBadge({ workspaceId, name, appearance }: {
  workspaceId?: string;
  name: string;
  appearance: TerminalAppearance;
}) {
  const status = useSessionGitHub(workspaceId, name);
  return <GitHubStatusBadge status={status} appearance={appearance} />;
}
