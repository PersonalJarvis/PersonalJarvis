import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Composer, UserBubble } from "./AgentChatPanel";
import { pinnedMessageChoices, withoutChoiceTokens } from "./mentionChoices";
import type { SocietyAgent } from "../data";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
// Model controls have their own suite; this exercises selection and pin delivery.
vi.mock("./AgentModelPicker", () => ({ AgentModelPicker: () => null }));
vi.mock("../data", async (original) => ({
  ...(await original<typeof import("../data")>()),
  useSocietyCapabilities: () => ({
    isLoading: false,
    data: [
      {
        id: "plugin:gmail",
        kind: "plugin",
        label: "gmail",
        one_liner: "Read email",
        connected: true,
        tool_name: "gmail",
        risk_tier: "ask",
      },
    ],
  }),
}));
vi.mock("@/components/agentchat/useComposerDictation", () => ({
  useComposerDictation: () => ({ dictating: false, stop() {}, toggle() {} }),
}));
vi.mock("@/components/agentchat/DictationStatus", () => ({ DictationStatus: () => null }));
vi.mock("@/components/agentchat/useChatAttachments", () => ({
  useChatAttachments: () => ({
    attachments: [],
    analyzing: 0,
    dragging: false,
    dragHandlers: {},
    clear() {},
    remove() {},
    attachFiles() {},
  }),
}));
afterEach(cleanup);

it("uses Add to select a real branded chip and preserves the existing pin protocol", async () => {
  const send = vi.fn(async (_text: string) => {});
  render(
    <Composer
      agent={{ agentId: "test", name: "Test" } as SocietyAgent}
      mentionable={[]}
      busy={false}
      sessionId="society:test"
      cwd=""
      provider="openai"
      surface="society"
      onSend={send}
      onCancel={async () => {}}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "society.chat.more" }));
  fireEvent.click(screen.getByRole("button", { name: "chat_tools.all" }));
  fireEvent.click(await screen.findByRole("option", { name: /@\s*gmail/ }));
  const chips = screen.getByTestId("tool-choice-chips");
  expect(chips.querySelector('[data-brand="gmail"] img')).not.toBeNull();
  const input = screen.getByRole("textbox");
  expect((input as HTMLTextAreaElement).value).toBe("");
  fireEvent.change(input, { target: { value: "Check unread mail" } });
  fireEvent.click(screen.getByRole("button", { name: "society.chat.send" }));
  await waitFor(() => expect(send).toHaveBeenCalledOnce());
  const text = String(send.mock.calls[0][0]);
  expect(text).toContain("[tools: plugin:gmail]");
  expect(text).toContain("@gmail");
  expect(withoutChoiceTokens("Check unread mail @gmail", pinnedMessageChoices(text))).toBe(
    "Check unread mail",
  );
});

it("reconstructs branded tags from an existing saved agent-card message", () => {
  const text = "Read this @gmail\n\n[tools: plugin:gmail]";
  render(<UserBubble item={{ type: "user", id: "m1", tsMs: 1, text, attachments: [] }} />);
  expect(screen.getByText("Read this")).toBeTruthy();
  expect(screen.queryByText(/@gmail/)).toBeNull();
  expect(
    screen.getByTestId("tool-choice-chips").querySelector('[data-brand="gmail"]'),
  ).not.toBeNull();
});

it("collapses server pins but keeps specific MCP tool selections distinct", () => {
  const choices = pinnedMessageChoices("Use @github\n\n[tools: mcp:github/read, mcp:github/list]");
  expect(choices).toHaveLength(1);
  expect(choices[0].id).toBe("mcp-server:github");
  const specific = pinnedMessageChoices("Use @github/read\n\n[tools: mcp:github/read]");
  expect(specific[0].id).toBe("mcp:github/read");
});
