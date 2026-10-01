import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ForkPaneDialog } from "./ForkPaneDialog";

const SOURCE = { name: "T3", agent: "claude", displayName: "Claude Code", workspaceId: "w1" };

function suggest(body: Record<string, unknown>) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({
    ok: true, name: "t3-fix-the-login-test", in_repo: true, can_fork: true, has_conversation: true, ...body,
  }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("forks the chat by default, without asking for a name", async () => {
  const fetchMock = suggest({});
  const onConfirm = vi.fn();
  render(<ForkPaneDialog source={SOURCE} busy={false} onCancel={vi.fn()} onConfirm={onConfirm} />);
  expect(screen.getByRole("dialog", { name: "Fork T3" })).toBeTruthy();
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/agentic-ide/terminals/T3/fork?workspace_id=w1"));
  expect(screen.queryByTestId("fork-branch-name")).toBeNull();
  fireEvent.click(screen.getByTestId("fork-confirm"));
  expect(onConfirm).toHaveBeenCalledWith({ mode: "chat", branch: "t3-fix-the-login-test" });
});

it("pre-fills a worktree name that matches the pane, and lets it be changed", async () => {
  suggest({});
  const onConfirm = vi.fn();
  render(<ForkPaneDialog source={SOURCE} busy={false} onCancel={vi.fn()} onConfirm={onConfirm} />);
  fireEvent.click(screen.getByTestId("fork-mode-worktree"));
  const input = await screen.findByTestId("fork-branch-name") as HTMLInputElement;
  await waitFor(() => expect(input.value).toBe("t3-fix-the-login-test"));
  fireEvent.change(input, { target: { value: "  t3-other-idea " } });
  fireEvent.click(screen.getByRole("button", { name: "Fork into worktree" }));
  expect(onConfirm).toHaveBeenCalledWith({ mode: "worktree", branch: "t3-other-idea" });
});

it("offers no worktree outside a git repository and says why", async () => {
  suggest({ in_repo: false });
  render(<ForkPaneDialog source={SOURCE} busy={false} onCancel={vi.fn()} onConfirm={vi.fn()} />);
  await screen.findByText(/Needs a git repository/);
  expect((screen.getByTestId("fork-mode-worktree") as HTMLButtonElement).disabled).toBe(true);
});

it("says so when the CLI cannot copy its conversation", async () => {
  suggest({ can_fork: false });
  render(<ForkPaneDialog source={{ ...SOURCE, displayName: "Kimi Code" }} busy={false} onCancel={vi.fn()} onConfirm={vi.fn()} />);
  await screen.findByText(/Kimi Code cannot copy its conversation/);
});
