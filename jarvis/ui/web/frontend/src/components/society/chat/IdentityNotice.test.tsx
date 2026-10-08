import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { NoticeItem } from "@/components/agentchat/reduce";
import type { SocietyAgent } from "../data";
import { Transcript } from "./AgentChatPanel";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key, fill: (text: string) => text }));
vi.mock("../data", async (original) => ({
  ...(await original<typeof import("../data")>()),
  useResolveProposal: () => async () => undefined,
}));
afterEach(cleanup);

const agent = { agentId: "agent-1a2b3c4d", name: "Mail Desk", tier: "worker" } as unknown as SocietyAgent;

function notice(overrides: Partial<NoticeItem> = {}): NoticeItem {
  return {
    type: "notice", id: "n1", kind: "proposal_resolved", text: "I am now Mail Desk - Gmail assistant.",
    agentName: agent.name, agentId: agent.agentId, status: "applied", tsMs: 1, resolved: "",
    data: { proposal_kind: "identity", status: "applied", previous: { name: "Nova" } },
    ...overrides,
  };
}

it.each([
  {},
  { status: "" },
  { kind: "proposal", resolved: "applied" },
])("hides applied identity receipts without leaving timeline rows: %j", (overrides) => {
  const { container } = render(<Transcript items={[notice(overrides)]} agent={agent} roster={[]} onDecide={async () => {}} />);
  expect(screen.queryByTestId("identity-notice")).toBeNull();
  expect(screen.queryByText(/I am now/)).toBeNull();
  expect(container.querySelector("[data-chat-item]")).toBeNull();
  expect(screen.getByText("society.chat.empty_title")).toBeTruthy();
});

it("keeps an identity proposal visible while it needs approval, then removes its resolved row", () => {
  const pending = notice({ kind: "proposal", status: "", resolved: "" });
  const { container, rerender } = render(<Transcript items={[pending]} agent={agent} roster={[]} onDecide={async () => {}} />);
  expect(screen.getByText("society.chat.proposal_title")).toBeTruthy();
  expect(container.querySelector('[data-chat-item="n1"]')).not.toBeNull();
  rerender(<Transcript items={[{ ...pending, resolved: "applied" }]} agent={agent} roster={[]} onDecide={async () => {}} />);
  expect(container.querySelector('[data-chat-item="n1"]')).toBeNull();
});

it("preserves identity failures and other proposal results", () => {
  const items = [
    notice({ id: "failed", status: "failed", text: "Could not change the name." }),
    notice({ id: "rule", data: { proposal_kind: "rule" }, text: "Rule saved." }),
  ];
  const { container } = render(<Transcript items={items} agent={agent} roster={[]} onDecide={async () => {}} />);
  expect(container.querySelector('[data-chat-item="failed"]')).not.toBeNull();
  expect(container.querySelector('[data-chat-item="rule"]')).not.toBeNull();
});
