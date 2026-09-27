import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { SocietyAgent } from "../data";
import { ChatGroupPanel } from "./ChatGroupPanel";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("../roster/RosterRail", () => ({ RosterRail: () => <aside /> }));
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

afterEach(cleanup);

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
  expect((within(left).getByRole("combobox") as HTMLSelectElement).value).toBe("other");
  expect((within(right).getByRole("combobox") as HTMLSelectElement).value).toBe("test");
  expect(within(left).getByTestId("chat-other")).toBeTruthy();
  expect(within(right).getByTestId("chat-test")).toBeTruthy();
  fireEvent.click(within(left).getByRole("button", { name: "Open hdckjashx" }));
  expect(within(left).getByTestId("chat-other").getAttribute("data-session")).toBe("society:other");
  expect(within(right).getByTestId("chat-test").getAttribute("data-session")).toBe("");
  fireEvent.click(within(right).getByRole("button", { name: "Open Test" }));
  expect(within(right).getByTestId("chat-test").getAttribute("data-session")).toBe("society:test");
  expect(within(left).getByTestId("chat-other").getAttribute("data-session")).toBe("society:other");
});
