import { fill, useT } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";
import { GitHubIcon, type GitHubIconName } from "./GitHubIcon";
import { PANE_BRAND, type TerminalAppearance } from "./terminalThemes";
import { useSessionGitHub, type SessionGitHubStatus } from "./useSessionGitHub";

const PR: Record<SessionGitHubStatus["state"], [GitHubIconName, string]> = {
  branch: ["git-branch", "ide_panes.github.state_branch"],
  draft: ["git-pull-request-draft", "ide_panes.github.state_draft"],
  open: ["git-pull-request", "ide_panes.github.state_open"],
  queued: ["git-merge-queue", "ide_panes.github.state_queued"],
  merged: ["git-merge", "ide_panes.github.state_merged"],
  closed: ["git-pull-request-closed", "ide_panes.github.state_closed"],
};

export function GitHubStatusBadge({ status, appearance }: {
  status: SessionGitHubStatus | null;
  appearance: TerminalAppearance;
}) {
  const t = useT();
  if (!status?.owned || !status.published) return null;
  if (!/^[A-Za-z0-9][A-Za-z0-9-]*\/[A-Za-z0-9._-]+$/.test(status.repo) || !status.branch) return null;
  // The PR may belong to an upstream repository when this branch is a fork.
  const pullRequest = /^https:\/\/github\.com\/[A-Za-z0-9][A-Za-z0-9-]*\/[A-Za-z0-9._-]+\/pull\/([1-9]\d*)$/.exec(status.url);
  const url = pullRequest ? status.url : `https://github.com/${status.repo}/tree/${encodeURIComponent(status.branch)}`;
  const destination = pullRequest
    ? fill(t("ide_panes.github.dest_pr"), { number: pullRequest[1] })
    : t("ide_panes.github.dest_branch");
  const unavailable = !status.available || Date.now() / 1000 - status.fetched_at > 60;
  const [icon, labelKey] = unavailable
    ? ["git-branch" as const, "ide_panes.github.unavailable"]
    : PR[status.state] ?? ["git-branch" as const, "ide_panes.github.branch_generic"];
  const label = t(labelKey);
  const identity = `${status.repo} · ${status.branch}`;
  return <a data-header-control="true" data-testid="session-github-status"
    href={url} target="_blank" rel="noopener noreferrer"
    aria-label={fill(t("ide_panes.github.open_aria"), { destination, identity })}
    title={`${identity}\n${label}\n${fill(t("ide_panes.github.open_title"), { destination })}`}
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
