import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GitOverviewTab } from "./GitOverviewTab";
import type { BranchRow, CiStatus, RepoOverview } from "./gitOverviewApi";
import { openExternalUrl } from "@/lib/openExternal";
import { useEventStore } from "@/store/events";
import { useIdeChatStore } from "@/store/ideChat";

vi.mock("@/lib/openExternal", () => ({ openExternalUrl: vi.fn(async () => true) }));

const noCi: CiStatus = { state: "none", url: "", total: 0, passed: 0, failed: 0, running: 0, pending: 0, commit: "", names: [] };

function branch(name: string, extra: Partial<BranchRow> = {}): BranchRow {
  return {
    name,
    current: false,
    remote_only: false,
    upstream: `origin/${name}`,
    ahead: 0,
    behind: 0,
    head: "abc123def456",
    committed_at: 0,
    worktree: "",
    on_github: true,
    merged_into: [],
    pull_requests: [],
    ci: noCi,
    ci_stale: false,
    ...extra,
  };
}

const pr = (number: number, state: "draft" | "open" | "queued" | "merged" | "closed", ci: CiStatus = noCi) => ({
  number,
  title: `Change ${number}`,
  url: `https://github.com/o/r/pull/${number}`,
  state,
  base: "main",
  head: "x",
  head_oid: "",
  merged_at: "",
  updated_at: "",
  queue_position: state === "queued" ? 2 : null,
  ci,
});

const OVERVIEW: RepoOverview = {
  available: true,
  reason: "",
  root: "/code/app",
  branch: "feature/wip",
  detached: false,
  head: "abc123def456",
  default_branch: "main",
  truncated: false,
  github: {
    available: true,
    reason: "",
    code: "",
    repo: "o/r",
    suggested_repo: "o/r",
    source: "app",
    repo_url: "https://github.com/o/r",
    fetched_at: Date.now() / 1000,
  },
  branches: [
    branch("feature/wip", {
      current: true,
      ahead: 2,
      pull_requests: [pr(7, "open")],
      ci: { ...noCi, state: "running", url: "https://github.com/o/r/actions/runs/7", total: 3, passed: 1, running: 2, names: ["tests"] },
    }),
    branch("main", { ci: { ...noCi, state: "success", total: 4, passed: 4, url: "https://github.com/o/r/actions/runs/1" } }),
    branch("feature/done", {
      merged_into: [{ target: "main", via: "pull_request", number: 5, url: "" }],
      pull_requests: [pr(5, "merged")],
      ci: { ...noCi, state: "success", total: 2, passed: 2 },
    }),
    branch("feature/queued", { pull_requests: [pr(8, "queued")] }),
    branch("feature/draft", { pull_requests: [pr(9, "draft")] }),
    branch("feature/dropped", {
      pull_requests: [pr(4, "closed")],
      ci: { ...noCi, state: "failure", total: 2, passed: 1, failed: 1, names: ["lint"], url: "https://github.com/o/r/actions/runs/4" },
    }),
  ],
  remote_branches: [branch("only-remote", { remote_only: true, upstream: "" })],
};

let answer: unknown = OVERVIEW;
let status = 200;
/** Holds overview answers back, like a slow GitHub read. */
let gate: Promise<void> | null = null;
const calls: string[] = [];
const puts: string[] = [];
const REPOS = {
  connected: true,
  source: "app",
  login: "octo",
  reason: "",
  repos: [
    { name: "octo/other", description: "Something else", private: true, fork: false, url: "", pushed_at: "" },
    { name: "o/r", description: "", private: false, fork: false, url: "", pushed_at: "" },
  ],
};

beforeEach(() => {
  answer = OVERVIEW;
  status = 200;
  gate = null;
  calls.length = 0;
  puts.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(url);
      const json = (body: unknown, code = 200) =>
        new Response(JSON.stringify(body), { status: code, headers: { "Content-Type": "application/json" } });
      if (url.includes("/github/repos")) return json(REPOS);
      if (url.includes("/github/binding")) {
        puts.push(String(init?.body));
        answer = OVERVIEW;
        return json({ ok: true, repo: "o/r" });
      }
      if (gate) await gate;
      return json(answer, status);
    }),
  );
  useEventStore.setState({ activeSection: "agentic-ide" });
  useIdeChatStore.setState({ workspace: { id: "w1", name: "App", path: "/code/app" }, stagedPane: null });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.mocked(openExternalUrl).mockClear();
});

const row = (name: string) => screen.getAllByTestId("git-branch-row").find((el) => el.dataset.branch === name)!;

describe("GitOverviewTab", () => {
  it("lists the workspace's branches and marks the checked-out one", async () => {
    render(<GitOverviewTab />);
    const rows = await screen.findAllByTestId("git-branch-row");
    expect(rows.map((el) => el.dataset.branch)).toEqual([
      "feature/wip",
      "main",
      "feature/done",
      "feature/queued",
      "feature/draft",
      "feature/dropped",
    ]);
    expect(row("feature/wip").dataset.current).toBe("true");
    expect(row("main").dataset.current).toBeUndefined();
    expect(screen.getByTestId("git-current-branch").textContent).toBe("feature/wip");
    expect(calls[0]).toContain("/api/agentic-ide/git/overview?workspace_id=w1");
  });

  it("shows each pull request state with GitHub's colour", async () => {
    render(<GitOverviewTab />);
    await screen.findAllByTestId("git-branch-row");
    const state = (name: string) => within(row(name)).getByTestId("git-pr").querySelector("[data-pr-state]");
    expect(state("feature/wip")?.getAttribute("class")).toContain("text-success");
    expect(state("feature/done")?.getAttribute("class")).toContain("--gh-merged");
    expect(state("feature/queued")?.getAttribute("class")).toContain("text-warning");
    expect(state("feature/draft")?.getAttribute("class")).toContain("text-muted-foreground");
    expect(state("feature/dropped")?.getAttribute("class")).toContain("text-destructive");
    expect(state("feature/dropped")?.getAttribute("data-pr-state")).toBe("closed");
  });

  it("keeps CI apart from merging: a merged branch still shows its own green check", async () => {
    render(<GitOverviewTab />);
    await screen.findAllByTestId("git-branch-row");
    const done = row("feature/done");
    expect(within(done).getByTestId("git-merged-into").dataset.target).toBe("main");
    expect(within(done).getByTestId("git-ci").querySelector("[data-ci-state]")?.getAttribute("data-ci-state")).toBe("success");
    expect(within(row("main")).queryByTestId("git-merged-into")).toBeNull();
    expect(within(row("feature/wip")).getByTestId("git-ci").querySelector("[data-ci-state]")?.getAttribute("data-ci-state")).toBe("running");
    expect(within(row("feature/dropped")).getByTestId("git-ci").querySelector("[data-ci-state]")?.getAttribute("data-ci-state")).toBe("failure");
    // No checks at all shows nothing rather than a misleading icon.
    expect(within(row("feature/queued")).queryByTestId("git-ci")).toBeNull();
  });

  it("opens the pull request and the CI run in the browser", async () => {
    render(<GitOverviewTab />);
    await screen.findAllByTestId("git-branch-row");
    fireEvent.click(within(row("feature/wip")).getByTestId("git-pr"));
    expect(openExternalUrl).toHaveBeenCalledWith("https://github.com/o/r/pull/7");
    fireEvent.click(within(row("feature/dropped")).getByTestId("git-ci"));
    expect(openExternalUrl).toHaveBeenCalledWith("https://github.com/o/r/actions/runs/4");
  });

  it("explains every icon in its tooltip", async () => {
    render(<GitOverviewTab />);
    await screen.findAllByTestId("git-branch-row");
    fireEvent.mouseEnter(within(row("feature/dropped")).getByTestId("git-ci").parentElement!);
    const tip = await screen.findByRole("tooltip");
    expect(tip.textContent).toContain("CI failed");
    expect(tip.textContent).toContain("lint");
  });

  it("folds GitHub-only branches away until asked, and forces a GitHub read on refresh", async () => {
    render(<GitOverviewTab />);
    await screen.findAllByTestId("git-branch-row");
    expect(screen.queryAllByTestId("git-branch-row").some((el) => el.dataset.branch === "only-remote")).toBe(false);
    fireEvent.click(screen.getByTestId("git-remote-toggle"));
    expect(row("only-remote")).toBeTruthy();
    fireEvent.click(screen.getByTestId("git-refresh"));
    await vi.waitFor(() => expect(calls.some((url) => url.includes("refresh=true"))).toBe(true));
  });

  it("says why when the folder is not a repository", async () => {
    answer = { ...OVERVIEW, available: false, reason: "This folder is not a git repository.", branches: [], remote_branches: [] };
    render(<GitOverviewTab />);
    expect((await screen.findByTestId("git-unavailable")).textContent).toContain("not a git repository");
  });

  it("asks once which GitHub repository the folder is, recommending its remote", async () => {
    answer = { ...OVERVIEW, github: { ...OVERVIEW.github, available: false, code: "needs_repo", repo: "", repo_url: "" } };
    render(<GitOverviewTab />);
    // The recommendation is clickable at once; the rest of the list follows.
    await vi.waitFor(() => expect(screen.getAllByTestId("git-repo-choice")).toHaveLength(2));
    const choices = screen.getAllByTestId("git-repo-choice");
    expect(choices.map((el) => el.dataset.repo)).toEqual(["o/r", "octo/other"]);
    expect(choices[0].textContent).toContain("Recommended");
    expect(screen.getByTestId("git-repo-picker").textContent).toContain("Connect a GitHub repository");
    fireEvent.click(choices[0]);
    await vi.waitFor(() => expect(puts).toEqual([JSON.stringify({ workspace_id: "w1", repo: "o/r" })]));
    expect(await screen.findAllByTestId("git-branch-row")).toHaveLength(6);
    expect(screen.queryByTestId("git-repo-picker")).toBeNull();
  });

  it("leaves the picker at once after a pick, while GitHub is still being read", async () => {
    answer = { ...OVERVIEW, github: { ...OVERVIEW.github, available: false, code: "needs_repo", repo: "", repo_url: "" } };
    render(<GitOverviewTab />);
    await vi.waitFor(() => expect(screen.getAllByTestId("git-repo-choice")).toHaveLength(2));
    let release: () => void = () => {};
    gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    fireEvent.click(screen.getAllByTestId("git-repo-choice")[0]);
    // The local branches and the choice show before GitHub has answered.
    expect(await screen.findAllByTestId("git-branch-row")).toHaveLength(6);
    expect(screen.queryByTestId("git-repo-picker")).toBeNull();
    const state = screen.getByTestId("git-github-state");
    expect(state.textContent).toContain("o/r");
    expect(state.textContent).toContain("Reading pull requests and CI from GitHub");
    release();
    await vi.waitFor(() => expect(screen.getByTestId("git-github-state").textContent).toContain("GitHub status from"));
  });

  it("reopens the pick from the repository name to change it", async () => {
    render(<GitOverviewTab />);
    fireEvent.click(await screen.findByTestId("git-bound-repo"));
    await vi.waitFor(() => expect(screen.getAllByTestId("git-repo-choice")).toHaveLength(2));
    fireEvent.click(screen.getByLabelText("Keep the current repository"));
    expect(await screen.findAllByTestId("git-branch-row")).toHaveLength(6);
  });

  it("offers to connect GitHub and still lists the local branches", async () => {
    answer = { ...OVERVIEW, github: { ...OVERVIEW.github, available: false, code: "not_connected", repo: "" } };
    render(<GitOverviewTab />);
    expect(await screen.findByTestId("git-connect-github")).toBeTruthy();
    expect(screen.getAllByTestId("git-branch-row")).toHaveLength(6);
    fireEvent.click(screen.getByText("Connect GitHub"));
    expect(useEventStore.getState().activeSection).toBe("plugins");
  });

  it("asks for one restart when the running backend does not know the route yet", async () => {
    answer = { detail: "Not Found" };
    status = 404;
    render(<GitOverviewTab />);
    expect((await screen.findByTestId("git-error")).textContent).toContain("Connect a GitHub repository");
    fireEvent.click(screen.getByTestId("git-restart"));
    await vi.waitFor(() => expect(calls).toContain("/api/settings/restart-app"));
  });
});
