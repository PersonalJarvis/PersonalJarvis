import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { openExternalUrl } from "@/lib/openExternal";
import { GitHubStatusBadge } from "./SessionGitHubBadge";
import type { SessionGitHubStatus } from "./useSessionGitHub";
import { PANE_BRAND } from "./terminalThemes";

vi.mock("@/lib/openExternal", () => ({ openExternalUrl: vi.fn().mockResolvedValue(true) }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

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

describe("session GitHub branch link", () => {
  it.each([null, status({ published: false }), status({ owned: false }),
    status({ owned: undefined as unknown as boolean })])("hides unconfirmed session branches", (value) => {
    const { container } = render(<GitHubStatusBadge status={value} appearance="dark" />);
    expect(container.innerHTML).toBe("");
  });

  it("renders just one quiet icon even with running CI and merge warnings", () => {
    render(<GitHubStatusBadge status={status({ merge_status: "dirty", review: "changes_requested" })} appearance="dark" />);
    const link = screen.getByRole("link");
    expect(link.textContent).toBe("");
    expect(link.querySelectorAll("svg")).toHaveLength(1);
    expect(link.querySelector("svg")?.getAttribute("width")).toBe("16");
    expect(link.style.background).toBe("");
    expect(link.style.border).toBe("");
    expect(link.className).not.toMatch(/(?:^|\s)(?:bg-|border(?:\s|-))/);
    expect(screen.queryByText(/Running|#7/)).toBeNull();
  });

  it("opens the actual branch through the desktop browser bridge", () => {
    const drag = vi.fn();
    render(<div onPointerDown={drag}><GitHubStatusBadge status={status()} appearance="dark" /></div>);
    const link = screen.getByRole("link", { name: /Open GitHub branch/ });
    expect(link.getAttribute("href")).toBe("https://github.com/owner/repo/tree/feature%2Ftest");
    fireEvent.pointerDown(link);
    fireEvent.click(link);
    expect(drag).not.toHaveBeenCalled();
    expect(openExternalUrl).toHaveBeenCalledExactlyOnceWith("https://github.com/owner/repo/tree/feature%2Ftest");
  });

  it.each(["light", "dark"] as const)("uses normal %s terminal chrome", (appearance) => {
    render(<GitHubStatusBadge status={status({ state: "merged" })} appearance={appearance} />);
    const reference = document.createElement("a");
    reference.style.color = PANE_BRAND[appearance].inkMuted;
    expect(screen.getByRole("link").style.color).toBe(reference.style.color);
  });

  it.each(["branch", "draft", "open", "queued", "merged", "closed"] as const)("keeps %s status in the single icon's tooltip", (state) => {
    render(<GitHubStatusBadge status={status({ state })} appearance="dark" />);
    const link = screen.getByRole("link");
    expect(link.title).toContain("owner/repo · feature/test\n");
    expect(link.querySelectorAll("svg")).toHaveLength(1);
    expect(link.textContent).toBe("");
  });

  it("keeps the branch link when refresh fails without claiming a fresh merge", () => {
    render(<GitHubStatusBadge status={status({ state: "merged", available: false })} appearance="dark" />);
    const link = screen.getByRole("link");
    expect(link.title).toContain("GitHub status unavailable");
    expect(link.title).not.toContain("merged");
    expect(link.getAttribute("href")).toContain("/tree/feature%2Ftest");
  });

  it("rejects invalid repository link metadata", () => {
    const { container } = render(<GitHubStatusBadge status={status({ repo: "evil.invalid/a/b" })} appearance="dark" />);
    expect(container.innerHTML).toBe("");
  });
});
