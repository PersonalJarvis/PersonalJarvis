import { GitHubIcon, type GitHubIconName } from "./GitHubIcon";
import { PANE_BRAND, PANE_CHROME, themeFor, type TerminalAppearance } from "./terminalThemes";
import { useSessionGitHub, type SessionCiState, type SessionGitHubStatus } from "./useSessionGitHub";

type Tone = "muted" | "green" | "yellow" | "red" | "magenta";
type Mark = { icon: GitHubIconName; label: string; tone: Tone };
const PR: Record<SessionGitHubStatus["state"], Mark> = {
  branch: { icon: "git-branch", label: "Published branch", tone: "muted" },
  draft: { icon: "git-pull-request-draft", label: "Draft pull request", tone: "muted" },
  open: { icon: "git-pull-request", label: "Open pull request", tone: "green" },
  queued: { icon: "git-merge-queue", label: "In the merge queue", tone: "yellow" },
  merged: { icon: "git-merge", label: "Pull request merged", tone: "magenta" },
  closed: { icon: "git-pull-request-closed", label: "Closed without merging", tone: "red" },
};
export const CI_MARKS: Record<SessionCiState, Mark> = {
  none: { icon: "circle-slash", label: "No checks", tone: "muted" },
  pending: { icon: "clock", label: "Queued", tone: "yellow" },
  expected: { icon: "dot-fill", label: "Expected", tone: "yellow" },
  requested: { icon: "clock", label: "Requested", tone: "yellow" },
  waiting: { icon: "hourglass", label: "Waiting", tone: "yellow" },
  running: { icon: "sync", label: "Running", tone: "yellow" },
  success: { icon: "check-circle", label: "Passed", tone: "green" },
  failure: { icon: "x-circle", label: "Failed", tone: "red" },
  error: { icon: "alert", label: "Error", tone: "red" },
  action_required: { icon: "alert", label: "Action required", tone: "yellow" },
  timed_out: { icon: "stopwatch", label: "Timed out", tone: "red" },
  startup_failure: { icon: "alert", label: "Startup failed", tone: "red" },
  cancelled: { icon: "circle-slash", label: "Cancelled", tone: "muted" },
  neutral: { icon: "dash", label: "Neutral", tone: "muted" },
  skipped: { icon: "skip", label: "Skipped", tone: "muted" },
  stale: { icon: "history", label: "Stale", tone: "yellow" },
  unknown: { icon: "question", label: "Unknown", tone: "muted" },
};

const MERGE: Record<string, string> = {
  behind: "Branch is behind its target", blocked: "Merge is blocked", clean: "Ready to merge",
  dirty: "Merge conflicts", draft: "Draft", has_hooks: "Mergeable with repository hooks",
  unknown: "Mergeability not yet known", unstable: "Checks do not pass",
};
const REVIEW: Record<string, Mark> = {
  approved: { icon: "check-circle", label: "Review approved", tone: "green" },
  changes_requested: { icon: "file-diff", label: "Changes requested", tone: "red" },
  review_required: { icon: "code-review", label: "Review required", tone: "yellow" },
};

function safeUrl(url: string): string | undefined {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && !parsed.username && !parsed.password ? url : undefined;
  } catch { return undefined; /* Absent/malformed provider URLs have no link. */ }
}

export function GitHubStatusBadge({ status, appearance }: {
  status: SessionGitHubStatus | null;
  appearance: TerminalAppearance;
}) {
  // A GitHub remote on the workspace alone is not evidence of session ownership.
  // This also hides badges from an older backend until its route is upgraded.
  if (!status?.owned || !status.published) return null;
  const brand = PANE_BRAND[appearance];
  const palette = themeFor(appearance);
  const color = (tone: Tone) => tone === "muted" ? brand.inkMuted : palette[tone];
  const unavailable = !status.available || Date.now() / 1000 - status.fetched_at > 60;
  let primary: Mark = unavailable
    ? { icon: "question", label: "GitHub status unavailable", tone: "muted" }
    : PR[status.state] ?? { icon: "question", label: "Unknown branch status", tone: "muted" };
  if (!unavailable && status.state === "branch" && status.ci.state === "success") {
    primary = { icon: "git-branch-check", label: "Published branch · checks passed", tone: "green" };
  }
  if (!unavailable && status.locked && status.state === "open") {
    primary = { ...primary, icon: "git-pull-request-locked", label: "Open pull request · discussion locked" };
  }
  const open = !unavailable && ["open", "draft", "queued"].includes(status.state);
  const review = open && status.review ? REVIEW[status.review] : undefined;
  const warning: Mark | undefined = open && ["dirty", "behind", "blocked"].includes(status.merge_status ?? "")
    ? { icon: status.merge_status === "behind" ? "git-compare" : "alert", label: MERGE[status.merge_status!], tone: status.merge_status === "dirty" ? "red" : "yellow" }
    : review;
  const ci = CI_MARKS[status.ci.state] ?? CI_MARKS.unknown;
  const identity = `${status.repo} · ${status.branch}${status.number ? ` · #${status.number}` : ""}`;
  const checked = status.fetched_at ? `Checked ${new Date(status.fetched_at * 1000).toLocaleTimeString()}` : "";
  const title = [identity, primary.label, open ? MERGE[status.merge_status ?? ""] : "", review?.label,
    status.locked ? "Pull request discussion is locked" : "", unavailable ? status.reason : `CI: ${ci.label}`, checked].filter(Boolean).join("\n");
  const linkClass = "inline-flex h-6 min-w-0 items-center gap-1.5 rounded px-1.5 text-[11px] font-medium leading-none hover:brightness-125 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-current";
  const icon = (name: GitHubIconName, spinning = false) => <span className={`inline-flex shrink-0 ${spinning ? "motion-safe:animate-spin" : ""}`}>
    <GitHubIcon name={name} width={18} height={18} className="block shrink-0" style={{ width: 18, height: 18, flex: "none" }} />
  </span>;
  return <span data-header-control="true" data-testid="session-github-status"
    className="inline-flex min-w-0 max-w-[min(50%,240px)] shrink items-center gap-0.5 rounded border"
    style={{ background: brand.chip, borderColor: PANE_CHROME[appearance].border }}
    onPointerDown={(event) => event.stopPropagation()} onClick={(event) => event.stopPropagation()}>
    <a href={safeUrl(status.url)} target="_blank" rel="noopener noreferrer" className={linkClass}
      style={{ color: color(primary.tone) }} aria-label={`${primary.label}: ${identity}`} title={title}>
      {icon(primary.icon)}
      <span className="max-w-[110px] truncate">{unavailable ? "Unavailable" : status.number ? `#${status.number}` : status.branch}</span>
    </a>
    {warning && <a href={safeUrl(status.url)} target="_blank" rel="noopener noreferrer"
      className="inline-flex h-6 shrink-0 items-center px-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-current"
      style={{ color: color(warning.tone) }} aria-label={`${warning.label}: ${identity}`} title={title}>{icon(warning.icon)}</a>}
    {!unavailable && status.ci.state !== "none" && <a href={safeUrl(status.ci.url || status.url)} target="_blank" rel="noopener noreferrer"
      className={linkClass} style={{ color: color(ci.tone) }} aria-label={`CI ${ci.label.toLowerCase()}: ${identity}`}
      title={[`CI: ${ci.label}`, `GitHub commit ${status.ci.commit}`, (status.ci.states ?? []).map((state) => CI_MARKS[state]?.label ?? state).join(", "), status.ci.names.join(", "), checked].filter(Boolean).join("\n")}>
      {icon(ci.icon, status.ci.state === "running")}
      <span className="truncate">{ci.label}</span>
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
