import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GitPanelDialog } from "./GitPanelDialog";
import { GitCheckoutPicker } from "./GitCheckoutPicker";
import { GitApiError, KEEP_CHECKOUT, type GitRepoInfo } from "@/lib/gitApi";

const git = vi.hoisted(() => ({
  inspectGit: vi.fn(), commitAll: vi.fn(), pushBranch: vi.fn(), fetchRemotes: vi.fn(), openPullRequest: vi.fn(),
  mergeBack: vi.fn(), removeWorktree: vi.fn(), pruneWorktrees: vi.fn(), prepareGit: vi.fn(),
}));
vi.mock("@/lib/gitApi", async (importOriginal) => ({ ...(await importOriginal<object>()), ...git }));
vi.mock("@/lib/openExternal", () => ({ openExternalUrl: vi.fn() }));

const worktree = { path: "/code/app/.worktrees/agent-x", branch: "agent/x", head: "def", main: false, detached: false, locked: false, prunable: false, current: false };
const info: GitRepoInfo = {
  folder: "/code/app", git_available: true, gh_available: true, is_repo: true, root: "/code/app", main_root: "/code/app",
  is_worktree: false, branch: "feature/login", detached: false, unborn: false, head: "abc", upstream: "origin/feature/login",
  ahead: 2, behind: 1, staged: 0, unstaged: 1, untracked: 1, conflicted: 0, insertions: 12, deletions: 3, dirty: true,
  default_branch: "main", remotes: ["origin"],
  branches: [
    { name: "feature/login", current: true, upstream: "origin/feature/login", committed_at: 0, worktree: "/code/app" },
    { name: "main", current: false, upstream: "origin/main", committed_at: 0, worktree: "" },
    { name: "agent/x", current: false, upstream: "", committed_at: 0, worktree: worktree.path },
  ],
  remote_branches: ["origin/main"],
  worktrees: [{ ...worktree, path: "/code/app", branch: "feature/login", main: true, current: true }, worktree],
  changes: [{ path: "src/app.ts", index: ".", worktree: "M" }, { path: "notes.md", index: "?", worktree: "?" }],
  suggested_branch: "agent/brave-river-0001", worktree_dir: "/code/app/.worktrees/agent-brave-river-0001",
};

beforeEach(() => { vi.clearAllMocks(); git.inspectGit.mockResolvedValue(info); });
afterEach(cleanup);

function panel(overrides: Partial<Parameters<typeof GitPanelDialog>[0]> = {}) {
  return render(<GitPanelDialog open onOpenChange={vi.fn()} folder="/code/app" workspace="App"
    onOpenWorktree={vi.fn()} onNewWorktree={vi.fn()} {...overrides} />);
}

describe("GitPanelDialog", () => {
  it("shows branch, changes and ahead/behind", async () => {
    panel();
    expect(await screen.findByText("feature/login", { selector: "span" })).toBeTruthy();
    expect(screen.getByText("2 changes")).toBeTruthy();
    expect(screen.getByText("src/app.ts")).toBeTruthy();
    expect(screen.getByText("+12")).toBeTruthy();
  });

  it("commits everything with the typed message", async () => {
    git.commitAll.mockResolvedValue({ commit: "1234abc" });
    panel();
    fireEvent.change(await screen.findByLabelText("Commit message"), { target: { value: "feat: login" } });
    fireEvent.click(screen.getByRole("button", { name: "Commit all" }));
    await waitFor(() => expect(git.commitAll).toHaveBeenCalledWith("/code/app", "feat: login"));
    expect(await screen.findByText("Committed 1234abc.")).toBeTruthy();
  });

  it("opens a draft pull request and offers its link", async () => {
    git.openPullRequest.mockResolvedValue({ url: "https://example.invalid/pr/1" });
    panel();
    fireEvent.click(await screen.findByLabelText("Open pull requests as drafts"));
    fireEvent.click(screen.getByRole("button", { name: "Pull request" }));
    await waitFor(() => expect(git.openPullRequest).toHaveBeenCalledWith("/code/app", { draft: true }));
    expect(await screen.findByRole("button", { name: "Open link" })).toBeTruthy();
  });

  it("asks a second time before removing a worktree with uncommitted work", async () => {
    git.removeWorktree.mockRejectedValueOnce(new GitApiError("agent-x has 1 uncommitted change. Remove anyway?", "dirty"))
      .mockResolvedValueOnce({ message: "Removed agent-x." });
    panel();
    fireEvent.click(await screen.findByRole("button", { name: "Remove agent-x" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove worktree" }));
    fireEvent.click(await screen.findByRole("button", { name: "Remove anyway" }));
    await waitFor(() => expect(git.removeWorktree).toHaveBeenLastCalledWith("/code/app", worktree.path, { force: true, deleteBranch: false }));
    expect(await screen.findByText("Removed agent-x.")).toBeTruthy();
  });

  it("offers to initialize a folder that is not a repository", async () => {
    git.inspectGit.mockResolvedValue({ ...info, is_repo: false });
    git.prepareGit.mockResolvedValue({ folder: "/code/app", branch: "main", created: true, message: "" });
    panel();
    fireEvent.click(await screen.findByRole("button", { name: "Initialize git" }));
    await waitFor(() => expect(git.prepareGit).toHaveBeenCalledWith("/code/app", { mode: "init", branch: "", base: "" }));
  });
});

describe("GitCheckoutPicker", () => {
  it("folds the default choice into one plain summary", async () => {
    render(<GitCheckoutPicker folder="/code/app" value={KEEP_CHECKOUT} onChange={vi.fn()} context="agent" />);
    expect(await screen.findByText("Same folder as your other agents. Nothing to set up.")).toBeTruthy();
    expect(screen.getByText("feature/login")).toBeTruthy();
    expect(screen.queryByRole("radio")).toBeNull();
  });

  it("offers an agent its own worktree but never an in-place branch switch", async () => {
    const onChange = vi.fn();
    render(<GitCheckoutPicker folder="/code/app" value={KEEP_CHECKOUT} onChange={onChange} context="agent" />);
    fireEvent.click(await screen.findByRole("button", { name: /Git options/ }));
    fireEvent.click(screen.getByRole("radio", { name: /New worktree/ }));
    expect(onChange).toHaveBeenCalledWith({ mode: "new_worktree", branch: "agent/brave-river-0001", base: "feature/login" });
    expect(screen.queryByRole("radio", { name: /New branch/ })).toBeNull();
    expect(screen.getByRole("radio", { name: /Existing worktree/ })).toBeTruthy();
  });

  it("completes a preset worktree plan once the repository is read", async () => {
    const onChange = vi.fn();
    render(<GitCheckoutPicker folder="/code/app" value={{ mode: "new_worktree", branch: "", base: "" }} onChange={onChange} context="workspace" />);
    await waitFor(() => expect(onChange).toHaveBeenCalledWith({ mode: "new_worktree", branch: "agent/brave-river-0001", base: "feature/login" }));
  });

  it("offers git init for a plain folder", async () => {
    git.inspectGit.mockResolvedValue({ ...info, is_repo: false });
    const onChange = vi.fn();
    render(<GitCheckoutPicker folder="/code/app" value={KEEP_CHECKOUT} onChange={onChange} context="workspace" />);
    fireEvent.click(await screen.findByRole("button", { name: /Git options/ }));
    fireEvent.click(screen.getByRole("radio", { name: /Initialize git/ }));
    expect(onChange).toHaveBeenCalledWith({ mode: "init", branch: "", base: "" });
  });
});
