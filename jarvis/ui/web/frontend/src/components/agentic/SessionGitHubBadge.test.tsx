import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CI_MARKS, GitHubStatusBadge } from "./SessionGitHubBadge";
import type { SessionGitHubStatus } from "./useSessionGitHub";
import { themeFor } from "./terminalThemes";

afterEach(cleanup);

function status(extra: Partial<SessionGitHubStatus> = {}): SessionGitHubStatus {
  return {
    repo: "owner/repo", branch: "feature/test", url: "https://github.com/owner/repo/pull/7",
    owned: true, published: true, available: true, reason: "", fetched_at: Date.now() / 1000,
    state: "open", number: 7, ci_stale: false,
    ci: { state: "running", url: "https://github.com/owner/repo/actions/runs/1", commit: "1234567",
      total: 2, passed: 1, failed: 0, running: 1, pending: 0, names: ["tests"] },
    ...extra,
  };
}

describe("session GitHub badge", () => {
  it("adds nothing for local-only, detached or non-GitHub folders", () => {
    const { rerender, container } = render(<GitHubStatusBadge status={null} appearance="dark" />);
    expect(container.innerHTML).toBe("");
    rerender(<GitHubStatusBadge status={status({ published: false })} appearance="dark" />);
    expect(container.innerHTML).toBe("");
    rerender(<GitHubStatusBadge status={status({ owned: false, branch: "main" })} appearance="dark" />);
    expect(container.innerHTML).toBe("");
    const legacy = status();
    delete (legacy as Partial<SessionGitHubStatus>).owned;
    rerender(<GitHubStatusBadge status={legacy} appearance="dark" />);
    expect(container.innerHTML).toBe("");
  });

  it.each([
    ["open", "Open pull request"], ["draft", "Draft pull request"],
    ["queued", "In the merge queue"], ["merged", "Pull request merged"],
    ["closed", "Closed without merging"], ["branch", "Published branch"],
  ] as const)("describes %s separately from CI", (state, label) => {
    render(<GitHubStatusBadge status={status({ state })} appearance="dark" />);
    expect(screen.getByRole("link", { name: new RegExp(label) }).getAttribute("href")).toContain("/pull/7");
    expect(screen.getByRole("link", { name: /CI running/ }).getAttribute("href")).toContain("/actions/runs/1");
  });

  it.each(["light", "dark"] as const)("uses the pane's own %s palette", (appearance) => {
    render(<GitHubStatusBadge status={status({ state: "merged" })} appearance={appearance} />);
    const link = screen.getByRole("link", { name: /Pull request merged/ });
    const reference = document.createElement("a");
    reference.style.color = themeFor(appearance).magenta!;
    expect(link.style.color).toBe(reference.style.color);
  });

  it("shows GitHub CI colours even when local HEAD differs, without an ambiguous star", () => {
    const value = status({ ci_stale: true });
    value.ci.state = "success";
    render(<GitHubStatusBadge status={value} appearance="light" />);
    const link = screen.getByRole("link", { name: /CI passed/ });
    expect(link.title).toContain("GitHub commit 1234567");
    expect(screen.queryByText("*")).toBeNull();
    const reference = document.createElement("a");
    reference.style.color = themeFor("light").green!;
    expect(link.style.color).toBe(reference.style.color);
  });

  it.each(Object.entries(CI_MARKS).filter(([state]) => state !== "none"))("labels CI state %s and preserves full icon geometry", (state, mark) => {
    const value = status();
    value.ci.state = state as SessionGitHubStatus["ci"]["state"];
    render(<GitHubStatusBadge status={value} appearance="dark" />);
    const link = screen.getByRole("link", { name: new RegExp(`CI ${mark.label.toLowerCase()}:`) });
    const svg = link.querySelector("svg")!;
    expect(svg.getAttribute("viewBox")).toBe("0 0 16 16");
    expect(svg.getAttribute("width")).toBe("18");
    expect(svg.style.flex).toBe("0 0 auto");
    expect(svg.querySelector("path")).toBeTruthy();
    expect(link.textContent).toBe(mark.label);
  });

  it.each([
    [{ merge_status: "dirty" }, "Merge conflicts"],
    [{ merge_status: "behind" }, "Branch is behind its target"],
    [{ merge_status: "blocked" }, "Merge is blocked"],
    [{ review: "review_required" }, "Review required"],
    [{ review: "changes_requested" }, "Changes requested"],
    [{ review: "approved" }, "Review approved"],
  ])("exposes merge and review conditions", (extra, label) => {
    render(<GitHubStatusBadge status={status(extra as Partial<SessionGitHubStatus>)} appearance="dark" />);
    expect(screen.getByRole("link", { name: new RegExp(String(label)) })).toBeTruthy();
  });

  it("explains discussion locks without declaring the PR closed", () => {
    render(<GitHubStatusBadge status={status({ locked: true })} appearance="light" />);
    expect(screen.getByRole("link", { name: /Open pull request · discussion locked/ })).toBeTruthy();
  });

  it("does not invent a check symbol when no checks exist", () => {
    const value = status({ state: "branch", number: null });
    value.ci.state = "none";
    render(<GitHubStatusBadge status={value} appearance="dark" />);
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByText("feature/test")).toBeTruthy();
  });

  it.each([{ available: false }, { fetched_at: 1 }])("suppresses stale merge and CI verdicts", (extra) => {
    render(<GitHubStatusBadge status={status({ state: "merged", ...extra })} appearance="dark" />);
    expect(screen.getByRole("link", { name: /GitHub status unavailable/ })).toBeTruthy();
    expect(screen.queryByRole("link", { name: /CI running/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /Pull request merged/ })).toBeNull();
  });

  it("blocks unsafe links and does not trigger pane dragging", () => {
    const drag = vi.fn();
    render(<div onPointerDown={drag}><GitHubStatusBadge status={status({ url: "javascript:alert(1)" })} appearance="dark" /></div>);
    const badge = screen.getByTestId("session-github-status");
    fireEvent.pointerDown(badge);
    expect(drag).not.toHaveBeenCalled();
    expect(badge.querySelector("a")?.hasAttribute("href")).toBe(false);
  });
});
