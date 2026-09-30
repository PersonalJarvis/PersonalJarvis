import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { SocietyAgent } from "../data";
import { ChatGroupPanel } from "./ChatGroupPanel";

const groupApi = vi.hoisted(() => ({ remove: vi.fn(async () => undefined) }));
vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("@/lib/societyChatGroups", () => ({ deleteSocietyChatGroup: groupApi.remove }));
vi.mock("../roster/RosterRail", () => ({ RosterRail: ({ footer }: { footer?: ReactNode }) => <aside>{footer}</aside> }));
vi.mock("./AgentChatPanel", () => ({
  AgentChatPanel: ({ agent, chatStore }: { agent: SocietyAgent; chatStore: any }) => {
    const session = chatStore((state: { activeSessionId: string | null }) => state.activeSessionId);
    return <div data-testid={`chat-${agent.agentId}`} data-session={session ?? ""}>
      <button onClick={() => chatStore.setState({ activeSessionId: agent.chatSessionId })}>
        Open {agent.name}
      </button>
    </div>;
  },
}));

afterEach(() => { cleanup(); groupApi.remove.mockClear(); });

it("opens two existing agent chats in separate stores and keeps each pane independent", () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const other = { agentId: "other", name: "hdckjashx", tier: "specialist", chatSessionId: "society:other" } as SocietyAgent;
  const test = { agentId: "test", name: "Test", tier: "specialist", chatSessionId: "society:test" } as SocietyAgent;
  render(<QueryClientProvider client={client}><ChatGroupPanel
    group={{ group_id: "team", name: "hdckjashx + Test", members: ["other", "test"], created_ms: 1, updated_ms: 1 }}
    groups={[]} roster={[other, test]} onOpenAgent={() => undefined} onOpenGroup={() => undefined}
    onCreateAgent={() => undefined} onDeleted={() => undefined}
    onGroupAgents={() => undefined} onAddAgentToGroup={() => undefined}
  /></QueryClientProvider>);

  const left = screen.getByTestId("society-group-pane-left");
  const right = screen.getByTestId("society-group-pane-right");
  expect(within(left).getByTestId("society-group-avatar-left")).toBeTruthy();
  expect(within(right).getByTestId("society-group-avatar-right")).toBeTruthy();
  expect(within(left).getByRole("heading", { name: "hdckjashx" })).toBeTruthy();
  expect(within(right).getByRole("heading", { name: "Test" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "hdckjashx + Test" })).toBeNull();
  expect(screen.getByTestId("society-group-split").className).toContain("divide-x-2");
  expect(within(left).getByTestId("chat-other")).toBeTruthy();
  expect(within(right).getByTestId("chat-test")).toBeTruthy();
  fireEvent.click(within(left).getByRole("button", { name: "Open hdckjashx" }));
  expect(within(left).getByTestId("chat-other").getAttribute("data-session")).toBe("society:other");
  expect(within(right).getByTestId("chat-test").getAttribute("data-session")).toBe("");
  fireEvent.click(within(right).getByRole("button", { name: "Open Test" }));
  expect(within(right).getByTestId("chat-test").getAttribute("data-session")).toBe("society:test");
  expect(within(left).getByTestId("chat-other").getAttribute("data-session")).toBe("society:other");
});

it("shows an explicit ungroup confirmation and restores individual chats on confirmation", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onDeleted = vi.fn();
  const scout = { agentId: "scout", name: "Scout", tier: "specialist", chatSessionId: "society:scout" } as SocietyAgent;
  const writer = { agentId: "writer", name: "Writer", tier: "specialist", chatSessionId: "society:writer" } as SocietyAgent;
  render(<QueryClientProvider client={client}><ChatGroupPanel
    group={{ group_id: "team", name: "Team", members: ["scout", "writer"], created_ms: 1, updated_ms: 1 }}
    groups={[]} roster={[scout, writer]} onOpenAgent={() => undefined} onOpenGroup={() => undefined}
    onCreateAgent={() => undefined} onDeleted={onDeleted}
    onGroupAgents={() => undefined} onAddAgentToGroup={() => undefined}
  /></QueryClientProvider>);

  fireEvent.click(screen.getByRole("button", { name: "society.groups.delete" }));
  expect(groupApi.remove).not.toHaveBeenCalled();
  const confirmation = screen.getByRole("alertdialog", { name: "society.groups.delete" });
  expect(within(confirmation).getByText("society.groups.delete_confirm")).toBeTruthy();
  fireEvent.click(within(confirmation).getByRole("button", { name: "society.groups.cancel" }));
  expect(screen.queryByRole("alertdialog")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "society.groups.delete" }));
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "society.groups.delete" }));
  await waitFor(() => expect(groupApi.remove).toHaveBeenCalledWith("team"));
  await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce());
});
