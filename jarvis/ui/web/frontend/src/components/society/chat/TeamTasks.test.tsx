import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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

afterEach(() => { cleanup(); state.rows = []; state.post.mockClear(); state.retry.mockClear(); });

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
