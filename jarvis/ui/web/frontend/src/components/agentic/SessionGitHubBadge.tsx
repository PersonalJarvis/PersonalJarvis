import { GitHubIcon, type GitHubIconName } from "./GitHubIcon";
import { PANE_BRAND, themeFor, type TerminalAppearance } from "./terminalThemes";
import { useSessionGitHub, type SessionGitHubStatus } from "./useSessionGitHub";

const PR: Record<SessionGitHubStatus["state"], [GitHubIconName, string]> = {
  branch: ["git-branch", "Branch on GitHub · no current pull request"],
  draft: ["git-pull-request-draft", "Draft pull request"],
  open: ["git-pull-request", "Open pull request"],
  queued: ["git-merge-queue", "In the merge queue"],
  merged: ["git-merge", "Pull request merged"],
  closed: ["git-pull-request-closed", "Pull request closed without merging"],
};
const CI: Record<string, [GitHubIconName, string]> = {
  none: ["question", "No CI checks reported"],
  pending: ["clock", "CI queued"],
  running: ["sync", "CI running"],
  success: ["check-circle", "CI passed"],
  failure: ["x-circle", "CI failed"],
  cancelled: ["stop", "CI cancelled"],
  neutral: ["skip", "CI neutral"],
  skipped: ["skip", "CI skipped"],
};

function safeUrl(url: string): string | undefined {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && !parsed.username && !parsed.password ? url : undefined;
  } catch { return undefined; /* No link for absent or malformed provider URLs. */ }
}

export function GitHubStatusBadge({ status, appearance }: {
  status: SessionGitHubStatus | null;
  appearance: TerminalAppearance;
}) {
  if (!status?.published) return null;
  const palette = themeFor(appearance);
  const muted = PANE_BRAND[appearance].inkMuted;
  const unavailable = !status.available || Date.now() / 1000 - status.fetched_at > 90;
  const [prIcon, prLabel] = unavailable ? ["question" as const, "GitHub status unavailable"] : PR[status.state];
  const [ciIcon, ciLabel] = CI[status.ci.state] ?? ["question", "CI status unknown"];
  const identity = `${status.repo} · ${status.branch}${status.number ? ` · #${status.number}` : ""}`;
  const checked = status.fetched_at ? `Last checked ${new Date(status.fetched_at * 1000).toLocaleTimeString()}` : "";
  const prColor = unavailable ? muted : status.state === "merged" ? palette.magenta
    : status.state === "closed" ? palette.red : status.state === "open" ? palette.green
      : status.state === "queued" ? palette.yellow : muted;
  const ciColor = status.ci.state === "failure" ? palette.red : status.ci.state === "success" ? palette.green
    : ["pending", "running"].includes(status.ci.state) ? palette.yellow : muted;
  const linkClass = "inline-flex h-5 shrink-0 items-center gap-1 rounded px-1 text-[11px] hover:bg-[color:var(--pane-chip)] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-current";
  return <span data-header-control="true" data-testid="session-github-status" className="inline-flex shrink-0 items-center gap-0.5"
    onPointerDown={(event) => event.stopPropagation()} onClick={(event) => event.stopPropagation()}>
    <a href={safeUrl(status.url)} target="_blank" rel="noopener noreferrer" className={linkClass} style={{ color: prColor }}
      aria-label={`${prLabel}: ${identity}`} title={[identity, prLabel, unavailable ? status.reason : "", checked].filter(Boolean).join("\n")}>
      <GitHubIcon name={prIcon} className="h-3.5 w-3.5" />
      {status.number && !unavailable ? <span>#{status.number}</span> : null}
    </a>
    {!unavailable && <a href={safeUrl(status.ci.url || status.url)} target="_blank" rel="noopener noreferrer"
      className={linkClass} style={{ color: status.ci_stale ? muted : ciColor }}
      aria-label={`${ciLabel}${status.ci_stale ? " · local commits not checked" : ""}: ${identity}`}
      title={[ciLabel, status.ci.commit ? `Pushed commit ${status.ci.commit}` : "", status.ci_stale ? "Local HEAD differs: these checks do not verify local commits." : "", status.ci.names.join(", "), checked].filter(Boolean).join("\n")}>
      <GitHubIcon name={ciIcon} className={`h-3.5 w-3.5 ${status.ci.state === "running" ? "motion-safe:animate-spin" : ""}`} />
      {status.ci_stale ? <span aria-hidden="true">*</span> : null}
    </a>}
  </span>;
}

export function SessionGitHubBadge({ workspaceId, name, appearance }: {
  workspaceId?: string;
  name: string;
  appearance: TerminalAppearance;
}) {
  const status = useSessionGitHub(workspaceId, name);
  return <GitHubStatusBadge status={status} appearance={appearance} />;
}
