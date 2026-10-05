import { useCallback, useEffect, useMemo, useState, type ReactNode, type SVGProps } from "react";
import {
  ChevronRight,
  CircleCheck,
  CircleDashed,
  CircleDot,
  CircleHelp,
  CircleX,
  Ellipsis,
  ExternalLink,
  FolderGit2,
  GitBranch,
  GitMerge,
  GitPullRequest,
  GitPullRequestClosed,
  GitPullRequestDraft,
  Globe,
  Laptop,
  Loader2,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { fill, useT } from "@/i18n";
import { openExternalUrl } from "@/lib/openExternal";
import { cn } from "@/lib/utils";
import { QuickTooltip } from "@/components/ui/tooltip";
import { useEventStore } from "@/store/events";
import { useIdeChatStore } from "@/store/ideChat";
import { BranchMenu, branchUrl } from "./BranchMenu";
import { ConnectGitHubCard, GitHubRepoPicker } from "./GitHubRepoPicker";
import {
  fetchGitOverview,
  GitOverviewError,
  type BranchRow,
  type CiState,
  type CiStatus,
  type PullRequest,
  type PullRequestState,
  type RepoOverview,
} from "./gitOverviewApi";

/**
 * Local git is cheap and read on every tick; GitHub is cached per repository
 * on the backend (shorter while a check runs), so this cadence never turns
 * into one GitHub call per tick.
 */
const POLL_MS = 15_000;
const POLL_JITTER_MS = 3_000;

// GitHub's pull request colours: open green, merged purple, closed red, the
// merge queue amber, a draft grey. Each maps to a theme token that has a light
// and a dark value — `--gh-merged` is the one hue the app had no job for yet.
const PR_TONE: Record<PullRequestState, string> = {
  draft: "text-muted-foreground",
  open: "text-success",
  queued: "text-warning",
  merged: "text-[hsl(var(--gh-merged))]",
  closed: "text-destructive",
};

/** GitHub's merge-queue mark: the merge glyph with the arm still dashed — waiting to land. */
function MergeQueueIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <circle cx="18" cy="18" r="3" />
      <circle cx="6" cy="6" r="3" />
      <path d="M6 21V9" />
      <path d="M6 9a9 9 0 0 0 9 9" strokeDasharray="2.5 3" />
    </svg>
  );
}

const PR_ICON: Record<PullRequestState, (props: SVGProps<SVGSVGElement>) => ReactNode> = {
  draft: (props) => <GitPullRequestDraft {...props} />,
  open: (props) => <GitPullRequest {...props} />,
  queued: (props) => <MergeQueueIcon {...props} />,
  merged: (props) => <GitMerge {...props} />,
  closed: (props) => <GitPullRequestClosed {...props} />,
};

// CI gets shapes GitHub uses for checks — a circle that is dashed, spinning,
// ticked or crossed — so a green tick can never be read as the purple merge.
const CI_TONE: Record<CiState, string> = {
  none: "text-muted-foreground/60",
  pending: "text-warning",
  running: "text-warning",
  success: "text-success",
  failure: "text-destructive",
};

function CiIcon({ state, className }: { state: CiState; className?: string }) {
  const cls = cn("h-4 w-4", CI_TONE[state], className);
  if (state === "running") return <Loader2 className={cn(cls, "animate-spin motion-reduce:animate-none")} aria-hidden />;
  if (state === "success") return <CircleCheck className={cls} aria-hidden />;
  if (state === "failure") return <CircleX className={cls} aria-hidden />;
  return <CircleDashed className={cls} aria-hidden />;
}

function useGitOverview(workspaceId: string | null) {
  const [data, setData] = useState<RepoOverview | null>(null);
  const [error, setError] = useState("");
  const [routeMissing, setRouteMissing] = useState(false);
  const [loading, setLoading] = useState(false);
  const [force, setForce] = useState(0);
  useEffect(() => {
    setData(null);
    setError("");
  }, [workspaceId]);
  useEffect(() => {
    if (!workspaceId) return;
    let alive = true;
    let timer: number | undefined;
    let refresh = force > 0;
    const tick = async () => {
      if (useEventStore.getState().activeSection === "agentic-ide") {
        setLoading(true);
        try {
          const next = await fetchGitOverview(workspaceId, refresh);
          refresh = false;
          if (alive) {
            setData(next);
            setError("");
            setRouteMissing(false);
          }
        } catch (err) {
          // Keep the last answer on screen; the next tick tries again.
          if (alive) {
            setError((err as Error).message);
            setRouteMissing(err instanceof GitOverviewError && err.routeMissing);
          }
        } finally {
          if (alive) setLoading(false);
        }
      }
      if (alive) timer = window.setTimeout(tick, POLL_MS + Math.random() * POLL_JITTER_MS);
    };
    void tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [workspaceId, force]);
  return { data, error, routeMissing, loading, refresh: () => setForce((value) => value + 1) };
}

/** An icon that opens a GitHub page; a plain span when there is nowhere to go. */
function LinkIcon({
  url,
  tip,
  label,
  testId,
  children,
}: {
  url: string;
  tip: string;
  label: string;
  testId: string;
  children: ReactNode;
}) {
  const body = url ? (
    <button
      type="button"
      data-testid={testId}
      aria-label={label}
      onClick={(event) => {
        event.stopPropagation();
        void openExternalUrl(url);
      }}
      className="inline-flex h-6 min-w-6 items-center justify-center gap-0.5 rounded-md px-1 hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {children}
    </button>
  ) : (
    <span data-testid={testId} aria-label={label} className="inline-flex h-6 min-w-6 items-center justify-center gap-0.5 px-1">
      {children}
    </span>
  );
  return (
    <QuickTooltip content={tip} side="bottom" className="inline-flex shrink-0">
      {body}
    </QuickTooltip>
  );
}

function prStateLabel(t: (key: string) => string, pr: PullRequest): string {
  if (pr.state === "queued" && pr.queue_position) return fill(t("ide_side_panel.git.pr.queued_position"), { n: pr.queue_position });
  return t(`ide_side_panel.git.pr.${pr.state}`);
}

function PullRequestBadge({ prs }: { prs: PullRequest[] }) {
  const t = useT();
  const [pr, ...others] = prs;
  if (!pr) return null;
  const Icon = PR_ICON[pr.state];
  const lines = [
    fill(t("ide_side_panel.git.pr_tooltip"), { state: prStateLabel(t, pr), number: pr.number, title: pr.title, base: pr.base }),
    ...others.map((other) => `#${other.number} · ${prStateLabel(t, other)}`),
    t("ide_side_panel.git.click_pr"),
  ];
  return (
    <LinkIcon url={pr.url} tip={lines.join("\n")} label={`${prStateLabel(t, pr)} #${pr.number}`} testId="git-pr">
      <Icon className={cn("h-4 w-4", PR_TONE[pr.state])} data-pr-state={pr.state} aria-hidden />
      <span className={cn("font-mono text-[11px] tabular-nums", PR_TONE[pr.state])}>#{pr.number}</span>
    </LinkIcon>
  );
}

function CiBadge({ ci, stale }: { ci: CiStatus; stale: boolean }) {
  const t = useT();
  if (ci.state === "none") return null;
  const lines = [
    t(`ide_side_panel.git.ci.${ci.state}`),
    fill(t("ide_side_panel.git.ci_counts"), {
      passed: ci.passed,
      total: ci.total,
      failed: ci.failed,
      running: ci.running,
      pending: ci.pending,
    }),
  ];
  if (ci.names.length > 0) {
    const key = ci.state === "failure" ? "ide_side_panel.git.ci_failed_names" : "ide_side_panel.git.ci_running_names";
    lines.push(fill(t(key), { names: ci.names.join(", ") }));
  }
  if (stale && ci.commit) lines.push(fill(t("ide_side_panel.git.ci_stale"), { commit: ci.commit.slice(0, 7) }));
  if (ci.url) lines.push(t("ide_side_panel.git.click_ci"));
  return (
    <LinkIcon url={ci.url} tip={lines.join("\n")} label={t(`ide_side_panel.git.ci.${ci.state}`)} testId="git-ci">
      <span data-ci-state={ci.state} className={cn("inline-flex", stale && "opacity-60")}>
        <CiIcon state={ci.state} />
      </span>
    </LinkIcon>
  );
}

function MergedChips({ row }: { row: BranchRow }) {
  const t = useT();
  if (row.merged_into.length === 0) return null;
  return (
    <>
      {row.merged_into.map((merged) => {
        const tip =
          merged.via === "pull_request"
            ? fill(t("ide_side_panel.git.merged_pr"), { target: merged.target, number: merged.number ?? "" })
            : fill(t("ide_side_panel.git.merged_git"), { target: merged.target });
        return (
          <QuickTooltip key={merged.target} content={tip} side="bottom" className="inline-flex min-w-0">
            <span
              data-testid="git-merged-into"
              data-target={merged.target}
              className="inline-flex min-w-0 items-center gap-0.5 text-[10.5px] text-[hsl(var(--gh-merged))]"
            >
              <GitMerge className="h-3 w-3 shrink-0" aria-hidden />
              <span className="truncate">{fill(t("ide_side_panel.git.merged_short"), { target: merged.target })}</span>
            </span>
          </QuickTooltip>
        );
      })}
    </>
  );
}

type BranchLocation = "local" | "github" | "github_only";

function branchLocation(row: BranchRow): BranchLocation {
  if (row.remote_only) return "github_only";
  return row.upstream || row.on_github ? "github" : "local";
}

// Where the branch lives, readable at a glance: a laptop while it exists only
// on this computer (amber — nothing backs it up yet), a globe once it is on GitHub.
const LOCATION_STYLE: Record<BranchLocation, { icon: typeof Globe; tone: string; label: string; tip: string }> = {
  local: { icon: Laptop, tone: "border-warning/40 bg-warning/10 text-warning", label: "local_only", tip: "not_pushed" },
  github: { icon: Globe, tone: "border-info/35 bg-info/10 text-info", label: "on_github", tip: "on_github_tip" },
  github_only: { icon: Globe, tone: "border-info/35 bg-info/10 text-info", label: "github_only", tip: "github_only_tip" },
};

function LocationChip({ row }: { row: BranchRow }) {
  const t = useT();
  const location = branchLocation(row);
  const style = LOCATION_STYLE[location];
  const Icon = style.icon;
  return (
    <QuickTooltip content={t(`ide_side_panel.git.${style.tip}`)} side="bottom" className="inline-flex shrink-0">
      <span
        data-testid="git-location"
        data-location={location}
        className={cn("inline-flex items-center gap-1 rounded-full border px-1.5 py-px text-[10.5px] font-medium leading-none", style.tone)}
      >
        <Icon className="h-3 w-3 shrink-0" aria-hidden />
        {t(`ide_side_panel.git.${style.label}`)}
      </span>
    </QuickTooltip>
  );
}

function BranchLine({
  row,
  isDefault,
  repoUrl,
  menuOpen,
  onMenu,
}: {
  row: BranchRow;
  isDefault: boolean;
  repoUrl: string;
  menuOpen: boolean;
  onMenu: (row: BranchRow, x: number, y: number) => void;
}) {
  const t = useT();
  const meta: string[] = [];
  if (row.upstream && (row.ahead || row.behind)) {
    meta.push(fill(t("ide_side_panel.git.ahead_behind"), { ahead: row.ahead, behind: row.behind }));
  }
  const unpushed = !row.remote_only && !row.upstream && !row.on_github;
  const nameTip = [
    row.name,
    row.current ? t("ide_side_panel.git.current") : "",
    isDefault ? t("ide_side_panel.git.default") : "",
    row.worktree ? fill(t("ide_side_panel.git.in_worktree"), { path: row.worktree }) : "",
    row.upstream ? fill(t("ide_side_panel.git.tracks"), { upstream: row.upstream, ahead: row.ahead, behind: row.behind }) : "",
    unpushed ? t("ide_side_panel.git.not_pushed") : "",
  ]
    .filter(Boolean)
    .join("\n");
  const BranchIcon = row.current ? CircleDot : GitBranch;
  const url = branchUrl(repoUrl, row);
  const openTip = fill(t("ide_side_panel.git.open_branch"), { branch: row.name });
  return (
    <li
      data-testid="git-branch-row"
      data-branch={row.name}
      data-current={row.current || undefined}
      onContextMenu={(event) => {
        event.preventDefault();
        onMenu(row, event.clientX, event.clientY);
      }}
      className={cn("group/row flex min-h-9 items-center gap-1 py-1 pl-2 pr-1", row.current && "bg-accent/10", menuOpen && "bg-secondary/60")}
    >
      <LinkIcon url={url} tip={url ? openTip : nameTip} label={url ? openTip : row.name} testId="git-branch-link">
        <BranchIcon className={cn("h-4 w-4 shrink-0", row.current ? "text-accent" : "text-muted-foreground")} aria-hidden />
      </LinkIcon>
      <span className="flex min-w-0 flex-1 flex-col leading-tight">
        <QuickTooltip content={nameTip} side="bottom" className="flex min-w-0 items-center gap-1.5">
          <span className={cn("truncate font-mono text-[12.5px]", row.current ? "font-semibold text-foreground" : "text-foreground")}>
            {row.name}
          </span>
          {isDefault && (
            <span className="shrink-0 rounded border border-border px-1 text-[9.5px] uppercase tracking-wide text-muted-foreground">
              {t("ide_side_panel.git.default_badge")}
            </span>
          )}
        </QuickTooltip>
        <span className="mt-0.5 flex min-w-0 items-center gap-2 text-[10.5px] text-muted-foreground">
          <LocationChip row={row} />
          <MergedChips row={row} />
          {meta.length > 0 && <span className="shrink-0 tabular-nums">{meta.join(" · ")}</span>}
          {row.worktree && <span className="shrink-0">{t("ide_side_panel.git.worktree_badge")}</span>}
        </span>
      </span>
      <PullRequestBadge prs={row.pull_requests} />
      <CiBadge ci={row.ci} stale={row.ci_stale} />
      <QuickTooltip content={t("ide_side_panel.git.more")} side="bottom" className="inline-flex shrink-0">
        <button
          type="button"
          data-testid="git-branch-more"
          data-branch-menu-anchor
          aria-label={fill(t("ide_side_panel.git.menu.aria"), { branch: row.name })}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={(event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            onMenu(row, rect.right - 264, rect.bottom + 4);
          }}
          className={cn(
            "inline-flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground opacity-0 hover:bg-secondary hover:text-foreground focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring group-hover/row:opacity-100",
            menuOpen && "bg-secondary text-foreground opacity-100",
          )}
        >
          <Ellipsis className="h-4 w-4" aria-hidden />
        </button>
      </QuickTooltip>
    </li>
  );
}

type BranchFilter = "all" | "pr" | "failing" | "unmerged" | "local";
const BRANCH_FILTERS: BranchFilter[] = ["all", "pr", "failing", "unmerged", "local"];

function matchesFilter(row: BranchRow, filter: BranchFilter): boolean {
  if (filter === "pr") return row.pull_requests.some((pr) => pr.state !== "merged" && pr.state !== "closed");
  if (filter === "failing") return row.ci.state === "failure";
  if (filter === "unmerged") return row.merged_into.length === 0;
  if (filter === "local") return !row.remote_only && !row.upstream && !row.on_github;
  return true;
}

function matchesQuery(row: BranchRow, query: string): boolean {
  if (!query) return true;
  const needle = query.toLowerCase().replace(/^#/, "");
  return (
    row.name.toLowerCase().includes(needle) ||
    row.pull_requests.some((pr) => String(pr.number) === needle || pr.title.toLowerCase().includes(needle))
  );
}

/** Search by name or pull request, and narrow the list to what needs a look. */
function BranchFilterBar({
  query,
  onQuery,
  filter,
  onFilter,
  counts,
}: {
  query: string;
  onQuery: (value: string) => void;
  filter: BranchFilter;
  onFilter: (value: BranchFilter) => void;
  counts: Record<BranchFilter, number>;
}) {
  const t = useT();
  return (
    <div data-testid="git-filter-bar" className="shrink-0 space-y-1.5 border-b border-border/60 px-3 py-2">
      <label className="flex h-7 items-center gap-1.5 rounded-md border border-border/70 bg-background px-2 focus-within:ring-2 focus-within:ring-ring">
        <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <input
          data-testid="git-search"
          value={query}
          onChange={(event) => onQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape" && query) {
              event.stopPropagation();
              onQuery("");
            }
          }}
          placeholder={t("ide_side_panel.git.search")}
          aria-label={t("ide_side_panel.git.search")}
          className="min-w-0 flex-1 bg-transparent text-[12px] text-foreground outline-none placeholder:text-muted-foreground"
        />
        {query && (
          <button
            type="button"
            aria-label={t("ide_side_panel.git.search_clear")}
            onClick={() => onQuery("")}
            className="inline-flex h-4 w-4 items-center justify-center rounded text-muted-foreground hover:text-foreground"
          >
            <X className="h-3 w-3" aria-hidden />
          </button>
        )}
      </label>
      <div role="group" aria-label={t("ide_side_panel.git.filter_aria")} className="flex flex-wrap gap-1">
        {BRANCH_FILTERS.map((value) => (
          <button
            key={value}
            type="button"
            data-testid={`git-filter-${value}`}
            aria-pressed={filter === value}
            onClick={() => onFilter(value)}
            className={cn(
              "inline-flex h-6 items-center gap-1 rounded-full border px-2 text-[11px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              filter === value
                ? "border-primary/40 bg-primary/10 text-foreground"
                : "border-border/70 text-muted-foreground hover:bg-secondary hover:text-foreground",
            )}
          >
            {t(`ide_side_panel.git.filter.${value}`)}
            <span className="tabular-nums text-muted-foreground">{counts[value]}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function Legend() {
  const t = useT();
  const pr = (["draft", "open", "queued", "merged", "closed"] as const).map((state) => {
    const Icon = PR_ICON[state];
    return (
      <li key={state} className="flex items-center gap-1.5">
        <Icon className={cn("h-3.5 w-3.5", PR_TONE[state])} aria-hidden />
        {t(`ide_side_panel.git.pr.${state}`)}
      </li>
    );
  });
  const ci = (["pending", "running", "success", "failure"] as const).map((state) => (
    <li key={state} className="flex items-center gap-1.5">
      <CiIcon state={state} className="h-3.5 w-3.5 animate-none" />
      {t(`ide_side_panel.git.ci.${state}`)}
    </li>
  ));
  return (
    <div data-testid="git-legend" className="grid grid-cols-2 gap-x-3 gap-y-1 px-3 pb-2 text-[10.5px] text-muted-foreground">
      <ul className="space-y-1">{pr}</ul>
      <ul className="space-y-1">
        {ci}
        <li className="flex items-center gap-1.5">
          <GitMerge className="h-3.5 w-3.5 text-[hsl(var(--gh-merged))]" aria-hidden />
          {t("ide_side_panel.git.legend_merged")}
        </li>
        <li className="flex items-center gap-1.5">
          <Laptop className="h-3.5 w-3.5 text-warning" aria-hidden />
          {t("ide_side_panel.git.not_pushed")}
        </li>
        <li className="flex items-center gap-1.5">
          <Globe className="h-3.5 w-3.5 text-info" aria-hidden />
          {t("ide_side_panel.git.on_github_tip")}
        </li>
      </ul>
    </div>
  );
}

/**
 * The running backend predates this tab: the one thing to do is restart, so
 * the card says what the tab is for and restarts on a click. A click in the
 * app is the person asking; control clients can never restart it.
 */
function RestartToConnectCard() {
  const t = useT();
  const pushToast = useEventStore((state) => state.pushToast);
  const [restarting, setRestarting] = useState(false);
  const restart = async () => {
    if (restarting) return;
    setRestarting(true);
    try {
      const response = await fetch("/api/settings/restart-app", { method: "POST" });
      if (response.status === 409) {
        pushToast("warning", t("topbar.restart_missions_running"));
        setRestarting(false);
        return;
      }
      if (!response.ok) throw new Error(`restart-failed:${response.status}`);
      // The window closes and relaunches; the button stays busy until then.
    } catch {
      pushToast("error", t("permissions.restart_failed"));
      setRestarting(false);
    }
  };
  return (
    <div data-testid="git-error" className="mx-3 my-3 space-y-2 rounded-lg border border-border/60 bg-muted/30 p-3">
      <p className="text-[12.5px] font-medium text-foreground">{t("ide_side_panel.git.connect_repo_title")}</p>
      <p className="text-[11.5px] text-muted-foreground">{t("ide_side_panel.git.needs_restart")}</p>
      <button
        type="button"
        data-testid="git-restart"
        disabled={restarting}
        onClick={() => void restart()}
        className="inline-flex h-8 items-center gap-1.5 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
      >
        {restarting ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : <RefreshCw className="h-3.5 w-3.5" aria-hidden />}
        {t("ide_side_panel.git.restart_button")}
      </button>
    </div>
  );
}

function sinceLabel(t: (key: string) => string, fetchedAt: number): string {
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - fetchedAt));
  if (seconds < 60) return fill(t("ide_side_panel.git.seconds_ago"), { n: seconds });
  return fill(t("ide_side_panel.git.minutes_ago"), { n: Math.round(seconds / 60) });
}

/**
 * The Git tab: the open workspace's branches, the checked-out one marked,
 * what each is merged into, its pull request and — separately — its CI.
 * Every icon explains itself on hover and opens its GitHub page on click.
 */
export function GitOverviewTab() {
  const t = useT();
  const workspace = useIdeChatStore((state) => state.workspace);
  const workspaceId = workspace?.id ?? null;
  const { data, error, routeMissing, loading, refresh } = useGitOverview(workspaceId);
  const [showRemote, setShowRemote] = useState(false);
  const [showLegend, setShowLegend] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<BranchFilter>("all");
  const [menu, setMenu] = useState<{ row: BranchRow; x: number; y: number } | null>(null);
  const openMenu = useCallback((row: BranchRow, x: number, y: number) => {
    setMenu((current) => (current?.row.name === row.name ? null : { row, x, y }));
  }, []);
  const closeMenu = useCallback(() => setMenu(null), []);
  // Reopened from the repository chip to change an earlier choice.
  const [changing, setChanging] = useState(false);
  // The repository just picked. The pick is saved at once, but the GitHub
  // read it starts takes seconds (up to the request timeout); until that
  // answer lands the tab shows the choice and the local branches, never the
  // picker again — a picker that stays put reads as "the click did nothing".
  const [pickedRepo, setPickedRepo] = useState("");
  useEffect(() => setChanging(false), [workspaceId]);
  useEffect(() => setPickedRepo(""), [data, workspaceId]);
  useEffect(() => {
    setQuery("");
    setFilter("all");
    setMenu(null);
  }, [workspaceId]);
  const allRows = useMemo(() => [...(data?.branches ?? []), ...(data?.remote_branches ?? [])], [data]);
  const trimmed = query.trim();
  const counts = useMemo(() => {
    const result = {} as Record<BranchFilter, number>;
    for (const value of BRANCH_FILTERS) {
      result[value] = allRows.filter((row) => matchesFilter(row, value) && matchesQuery(row, trimmed)).length;
    }
    return result;
  }, [allRows, trimmed]);
  const narrowed = filter !== "all" || trimmed !== "";
  const visible = (rows: BranchRow[]) => rows.filter((row) => matchesFilter(row, filter) && matchesQuery(row, trimmed));

  if (!workspace) {
    return <p className="px-4 py-6 text-center text-xs text-muted-foreground">{t("ide_side_panel.git.no_workspace")}</p>;
  }
  const github = data?.github;
  const picking = Boolean(
    data && workspaceId && !pickedRepo && (github?.code === "needs_repo" || (changing && github?.code !== "not_connected")),
  );
  const boundRepo = pickedRepo || github?.repo || "";
  const repoName = (data?.root || workspace.path).replace(/\\/g, "/").split("/").filter(Boolean).pop() ?? workspace.name;

  return (
    <section data-testid="ide-git-tab" aria-label={t("ide_side_panel.git.aria")} className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 space-y-1.5 border-b border-border/60 px-3 pb-2.5 pt-3">
        <div className="flex items-center gap-2">
          <FolderGit2 className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          <span className="min-w-0 truncate text-sm font-semibold text-foreground">{repoName}</span>
          {data?.available && (
            <span
              data-testid="git-current-branch"
              className="flex min-w-0 shrink items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
            >
              <GitBranch className="h-3 w-3 shrink-0" aria-hidden />
              <span className="truncate">{data.detached ? data.head.slice(0, 7) : data.branch}</span>
            </span>
          )}
          <span className="ml-auto flex shrink-0 items-center">
            {github?.repo_url && (
              <LinkIcon url={github.repo_url} tip={t("ide_side_panel.git.open_repo")} label={t("ide_side_panel.git.open_repo")} testId="git-open-repo">
                <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
              </LinkIcon>
            )}
            <QuickTooltip content={t("ide_side_panel.git.legend")} side="bottom" className="inline-flex">
              <button
                type="button"
                aria-label={t("ide_side_panel.git.legend")}
                aria-pressed={showLegend}
                onClick={() => setShowLegend((value) => !value)}
                className={cn(
                  "inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  showLegend && "bg-secondary text-foreground",
                )}
              >
                <CircleHelp className="h-3.5 w-3.5" aria-hidden />
              </button>
            </QuickTooltip>
            <QuickTooltip content={t("ide_side_panel.git.refresh")} side="bottom" className="inline-flex">
              <button
                type="button"
                data-testid="git-refresh"
                aria-label={t("ide_side_panel.git.refresh")}
                onClick={refresh}
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin motion-reduce:animate-none")} aria-hidden />
              </button>
            </QuickTooltip>
          </span>
        </div>
        {boundRepo && (
          <p data-testid="git-github-state" className="flex min-w-0 items-center gap-1.5 text-[10.5px] text-muted-foreground">
            <QuickTooltip content={t("ide_side_panel.git.change_repo")} side="bottom" className="inline-flex min-w-0">
              <button
                type="button"
                data-testid="git-bound-repo"
                onClick={() => setChanging(true)}
                className="inline-flex min-w-0 items-center gap-1 rounded px-0.5 font-mono text-foreground/80 hover:bg-secondary hover:text-foreground"
              >
                <span className="truncate">{boundRepo}</span>
              </button>
            </QuickTooltip>
            <span aria-hidden>·</span>
            <span className="min-w-0 truncate">
              {pickedRepo
                ? t("ide_side_panel.git.github_reading")
                : github?.available
                  ? fill(t("ide_side_panel.git.github_updated"), { when: sinceLabel(t, github.fetched_at) })
                  : github?.reason}
            </span>
          </p>
        )}
      </div>
      {showLegend && (
        <div className="shrink-0 border-b border-border/60 pt-2">
          <Legend />
        </div>
      )}
      {!picking && data?.available && allRows.length > 0 && (
        <BranchFilterBar query={query} onQuery={setQuery} filter={filter} onFilter={setFilter} counts={counts} />
      )}
      {picking && data && workspaceId ? (
        <GitHubRepoPicker
          key={workspaceId}
          workspaceId={workspaceId}
          current={github?.repo ?? ""}
          suggested={github?.suggested_repo ?? ""}
          onPicked={(repo) => {
            setPickedRepo(repo);
            setChanging(false);
            refresh();
          }}
          onCancel={github?.repo ? () => setChanging(false) : undefined}
        />
      ) : (
      <div className="min-h-0 flex-1 overflow-y-auto py-1">
        {github?.code === "not_connected" && <ConnectGitHubCard />}
        {!data ? (
          error ? (
            routeMissing ? (
              <RestartToConnectCard />
            ) : (
              <p data-testid="git-error" className="px-4 py-6 text-center text-xs text-destructive">
                {error}
              </p>
            )
          ) : (
            <p className="flex items-center justify-center gap-2 px-4 py-6 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              {t("ide_side_panel.git.loading")}
            </p>
          )
        ) : !data.available ? (
          <p data-testid="git-unavailable" className="px-4 py-6 text-center text-xs text-muted-foreground">
            {data.reason}
          </p>
        ) : (
          <>
            <ul aria-label={t("ide_side_panel.git.branches")}>
              {visible(data.branches).map((row) => (
                <BranchLine
                  key={row.name}
                  row={row}
                  isDefault={row.name === data.default_branch}
                  repoUrl={github?.repo_url ?? ""}
                  menuOpen={menu?.row.name === row.name}
                  onMenu={openMenu}
                />
              ))}
            </ul>
            {narrowed && counts[filter] === 0 && (
              <p data-testid="git-filter-empty" className="px-4 py-6 text-center text-xs text-muted-foreground">
                {t("ide_side_panel.git.filter_empty")}
              </p>
            )}
            {data.truncated && (
              <p className="px-3 py-2 text-[11px] text-muted-foreground">
                {fill(t("ide_side_panel.git.truncated"), { n: data.branches.length })}
              </p>
            )}
            {visible(data.remote_branches).length > 0 && (
              <div className="mt-1 border-t border-border/60">
                <button
                  type="button"
                  data-testid="git-remote-toggle"
                  aria-expanded={showRemote || narrowed}
                  onClick={() => setShowRemote((value) => !value)}
                  className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-[11px] font-medium text-muted-foreground hover:text-foreground"
                >
                  <ChevronRight className={cn("h-3.5 w-3.5 transition-transform", (showRemote || narrowed) && "rotate-90")} aria-hidden />
                  {fill(t("ide_side_panel.git.remote_only"), { n: visible(data.remote_branches).length })}
                </button>
                {(showRemote || narrowed) && (
                  <ul aria-label={fill(t("ide_side_panel.git.remote_only"), { n: visible(data.remote_branches).length })}>
                    {visible(data.remote_branches).map((row) => (
                      <BranchLine
                        key={row.name}
                        row={row}
                        isDefault={false}
                        repoUrl={github?.repo_url ?? ""}
                        menuOpen={menu?.row.name === row.name}
                        onMenu={openMenu}
                      />
                    ))}
                  </ul>
                )}
              </div>
            )}
            {error && <p className="px-3 py-2 text-[11px] text-destructive">{error}</p>}
          </>
        )}
      </div>
      )}
      {menu && data && (
        <BranchMenu
          row={menu.row}
          repoUrl={github?.repo_url ?? ""}
          defaultBranch={data.default_branch}
          x={menu.x}
          y={menu.y}
          onDismiss={closeMenu}
        />
      )}
    </section>
  );
}
