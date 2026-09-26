import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { SocietyAgent } from "../data";
import { ChatGroupPanel } from "./ChatGroupPanel";

const mocks = vi.hoisted(() => ({ send: vi.fn(async () => undefined) }));
vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
vi.mock("@/lib/societyChatGroups", () => ({
  useSocietyChatGroupMessages: () => ({
    data: [
      { id: "post", from_agent: "user", text: "Status?", ts_ms: 1 },
      { id: "answer", from_agent: "scout", text: "Ready.", ts_ms: 2 },
    ],
    error: null,
    refetch: async () => undefined,
  }),
  sendSocietyChatGroupMessage: mocks.send,
  deleteSocietyChatGroup: vi.fn(),
}));
vi.mock("../roster/RosterRail", () => ({ RosterRail: () => <aside /> }));
vi.mock("../AgentSwatch", () => ({ AgentSwatch: () => <span /> }));

afterEach(() => { cleanup(); mocks.send.mockClear(); });

it("shows named agent replies and sends to the whole group by default", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const scout = { agentId: "scout", name: "Scout", tier: "specialist" } as SocietyAgent;
  const writer = { agentId: "writer", name: "Writer", tier: "specialist" } as SocietyAgent;
  render(<QueryClientProvider client={client}><ChatGroupPanel
    group={{ group_id: "team", name: "Launch team", members: ["scout", "writer"], created_ms: 1, updated_ms: 1, last_text: "", last_ms: null, last_from_agent: null }}
    groups={[]} roster={[scout, writer]} onOpenAgent={() => undefined} onOpenGroup={() => undefined}
    onCreateAgent={() => undefined} onDeleted={() => undefined}
  /></QueryClientProvider>);
  expect(screen.getByText("Ready.")).toBeTruthy();
  expect(screen.getByText("Status?")).toBeTruthy();
  fireEvent.change(screen.getByRole("textbox", { name: "society.groups.placeholder" }), { target: { value: "Next step?" } });
  fireEvent.click(screen.getByRole("button", { name: "society.groups.send" }));
  await waitFor(() => expect(mocks.send).toHaveBeenCalledWith("team", "Next step?", undefined));
});
