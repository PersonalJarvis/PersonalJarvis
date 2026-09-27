import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TeamTasks } from "./TeamTasks";

const state = vi.hoisted(() => ({
  rows: [] as unknown[],
  post: vi.fn(),
  retry: vi.fn(),
}));
vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("@/components/agentchat/ChatMarkdown", () => ({ ChatMarkdown: ({ text }: { text: string }) => <p>{text}</p> }));
vi.mock("../world/questsData", () => ({
  useSocietyQuests: () => ({ data: state.rows, isError: false }),
  usePostQuest: () => ({ mutate: state.post, isPending: false, isError: false }),
  useRetryQuest: () => ({ mutate: state.retry, isPending: false, isError: false }),
  useCancelQuest: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
}));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); state.rows = []; state.post.mockClear(); state.retry.mockClear(); });

it("starts a plain-language task without asking for an agent or model", () => {
  render(<TeamTasks agents={[]} onOpenAgent={vi.fn()} />);
  fireEvent.change(screen.getByPlaceholderText("society.tasks.placeholder"), { target: { value: "Fasse die Mails zusammen." } });
  fireEvent.click(screen.getByRole("button", { name: "society.tasks.start" }));
  expect(state.post).toHaveBeenCalledWith(["Fasse die Mails zusammen.", ""], expect.any(Object));
});

it("shows a blocker and opens the owning agent for the required login", () => {
  state.rows = [{
    quest_id: "login", title: "Private research", state: "failed", agent_id: "scout",
    result: { status: "blocked", done: "Login required.", open: ["Sign in in the agent browser."] },
  }];
  const open = vi.fn();
  render(<TeamTasks agents={[{ agentId: "scout", name: "Scout" }] as any} onOpenAgent={open} />);
  fireEvent.click(screen.getByText("Private research"));
  expect(screen.getByText("Sign in in the agent browser.")).toBeTruthy();
  expect(screen.getByText("society.tasks.blocked")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "society.tasks.open_agent" }));
  expect(open).toHaveBeenCalledWith("scout");
  fireEvent.click(screen.getByRole("button", { name: "society.world.quest_retry" }));
  expect(state.retry).toHaveBeenCalledWith(["login"]);
});

it("opens a blocked web task at its exact URL in the owning browser profile", async () => {
  const fetcher = vi.fn(async () => ({ ok: true }));
  vi.stubGlobal("fetch", fetcher);
  state.rows = [{
    quest_id: "web-login", title: "Private page", text: "Read https://example.com/private.",
    state: "failed", agent_id: "scout", routing: { focus: ["core:browser"] },
    result: { status: "blocked", done: "Sign-in required.", open: ["Sign in."] },
  }];
  render(<TeamTasks agents={[{ agentId: "scout", name: "Scout" }] as any} onOpenAgent={vi.fn()} />);
  fireEvent.click(screen.getByText("Private page"));
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "society.tasks.sign_in" }));
  });
  expect(fetcher).toHaveBeenCalledWith("/api/society/agents/scout/browser/login", expect.objectContaining({
    method: "POST", body: JSON.stringify({ start_url: "https://example.com/private" }),
  }));
});

it("shows startup as an automatic wait without asking the user to retry", () => {
  state.rows = [{
    quest_id: "warming", title: "Research", state: "open", agent_id: "scout",
    result: { status: "waiting", reason: "brain_starting", blocker: "startup" },
  }];
  render(<TeamTasks agents={[{ agentId: "scout", name: "Scout" }] as any} onOpenAgent={vi.fn()} />);
  fireEvent.click(screen.getByText("Research"));
  expect(screen.getAllByText("society.tasks.starting").length).toBeGreaterThan(0);
  expect(screen.queryByRole("button", { name: "society.world.quest_retry" })).toBeNull();
});
