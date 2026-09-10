import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Composer, UserBubble } from "./AgentChatPanel";
import { pinnedMessageChoices, withoutChoiceTokens } from "./mentionChoices";
import type { SocietyAgent } from "../data";

vi.mock("@/i18n", async () => {
  const { default: en } = await import("@/i18n/locales/en.json");
  return { useT: () => (key: string) => key === "society.chat.delegate_line" ? en.society.chat.delegate_line : key };
});
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
  const input = screen.getByRole("textbox");
  expect(input.querySelector('[data-brand="gmail"] img')).not.toBeNull();
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
  expect(screen.getByTestId("tool-choice-chips").textContent).toContain("Read this");
  expect(screen.queryByText("@gmail")).toBeNull();
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

const gmailAgent = { agentId: "gmail-agent", name: "Gmail-Agent", tier: "specialist" } as SocietyAgent;
const teammates = [
  { agentId: "linear", name: "LinearAgent", tier: "specialist", title: "Tickets", description: "Read tickets", palette: { primary: "#333", secondary: "#555", accent: "#777" } },
  { agentId: "drive", name: "DriveAgent", tier: "specialist", title: "Files", description: "Manage files", palette: { primary: "#333", secondary: "#555", accent: "#777" } },
] as SocietyAgent[];

async function sendDraft(text: string, surface: "society" | "jarvis" = "society") {
  const send = vi.fn(async (_text: string) => {});
  render(
    <Composer
      agent={surface === "society" ? gmailAgent : { ...gmailAgent, agentId: "jarvis", name: "Jarvis", tier: "lead" }}
      mentionable={teammates}
      busy={false}
      sessionId={surface === "society" ? "society:gmail-agent" : "jarvis-chat"}
      cwd=""
      provider="openai"
      surface={surface}
      onSend={send}
      onCancel={async () => {}}
    />,
  );
  const input = screen.getByRole("textbox");
  input.textContent = text;
  fireEvent.input(input);
  fireEvent.click(screen.getByRole("button", { name: "society.chat.send" }));
  await waitFor(() => expect(send).toHaveBeenCalledOnce());
  return String(send.mock.calls[0][0]);
}

it("keeps a user's teammate request in the specialist chat and uses internal messaging", async () => {
  const draft = "Ask @LinearAgent about current tickets and coordinate with @DriveAgent @gmail";
  const sent = await sendDraft(draft);
  expect(sent.startsWith(`${draft}\n\n`)).toBe(true);
  expect(sent).not.toContain("[to jarvis]");
  expect(sent).not.toContain("delegate-to-agent");
  expect(sent).toContain("Current sender: the user.");
  expect(sent).toContain('Current recipient: "Gmail-Agent" (id "gmail-agent")');
  expect(sent).toContain('"LinearAgent" (id "linear")');
  expect(sent).toContain('"DriveAgent" (id "drive")');
  expect(sent).toContain("society_message_agent");
  expect(sent).toContain("Reply to the user here.");
  expect(sent).toContain("does not deliver a message");
  expect(sent).toContain("[tools: plugin:gmail]");
  cleanup();
  render(<UserBubble item={{ type: "user", id: "new", tsMs: 1, text: sent, attachments: [] }} />);
  expect(screen.getByText(/Ask @LinearAgent about current tickets and coordinate with @DriveAgent/)).toBeTruthy();
  expect(screen.queryByText(/Current sender:/)).toBeNull();
});

it("keeps delegation available when the same mentions are sent to Jarvis", async () => {
  const sent = await sendDraft("Ask @LinearAgent and @DriveAgent", "jarvis");
  expect(sent.match(/\[to jarvis\]/g)).toHaveLength(2);
  expect(sent).toContain("agent 'LinearAgent' (id linear) with delegate-to-agent");
  expect(sent).toContain("agent 'DriveAgent' (id drive) with delegate-to-agent");
  expect(sent).not.toContain("[agent mentions]");
});

it("leaves ordinary user text and unknown mentions unchanged", async () => {
  const draft = "Explain this @UnknownAgent reference";
  expect(await sendDraft(draft)).toBe(draft);
});

it("keeps existing saved delegation hints out of the user bubble", () => {
  render(<UserBubble item={{ type: "user", id: "old", tsMs: 1, text: "Ask @LinearAgent\n\n[to jarvis] Hand this to LinearAgent", attachments: [] }} />);
  expect(screen.getByText("Ask @LinearAgent")).toBeTruthy();
  expect(screen.queryByText(/Hand this to/)).toBeNull();
});
