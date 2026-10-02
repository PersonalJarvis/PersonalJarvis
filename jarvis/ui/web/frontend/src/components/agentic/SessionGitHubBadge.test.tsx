import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GitHubStatusBadge } from "./SessionGitHubBadge";
import type { SessionGitHubStatus } from "./useSessionGitHub";
import { themeFor, PANE_BRAND } from "./terminalThemes";

afterEach(cleanup);

function status(extra: Partial<SessionGitHubStatus> = {}): SessionGitHubStatus {
  return {
    repo: "owner/repo", branch: "feature/test", url: "https://github.com/owner/repo/pull/7",
    published: true, available: true, reason: "", fetched_at: Date.now() / 1000,
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
  });

  it.each([
    ["open", "Open pull request"], ["draft", "Draft pull request"],
    ["queued", "In the merge queue"], ["merged", "Pull request merged"],
    ["closed", "Pull request closed without merging"], ["branch", "Branch on GitHub"],
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

  it("marks CI from another commit instead of claiming local success", () => {
    const value = status({ ci_stale: true });
    value.ci.state = "success";
    render(<GitHubStatusBadge status={value} appearance="light" />);
    const link = screen.getByRole("link", { name: /local commits not checked/ });
    expect(link.title).toContain("do not verify local commits");
    const reference = document.createElement("a");
    reference.style.color = PANE_BRAND.light.inkMuted;
    expect(link.style.color).toBe(reference.style.color);
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
