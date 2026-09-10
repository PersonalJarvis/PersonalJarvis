import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { EMPTY_TIMELINE, type Timeline, type UserItem } from "@/components/agentchat/reduce";
import type { SocietyAgent } from "../data";
import {
  AgentChatPanel,
  itemsForOpenSession,
  useSocietyChatStore,
  setJarvisCardMode,
} from "./AgentChatPanel";
import { useAgentChatStore } from "@/store/agentChat";
import { useEventStore } from "@/store/events";
import type { AgentChatSession } from "@/lib/agentChatApi";
import { useHomeStore } from "@/store/home";
import { JarvisHistoryRail } from "./JarvisHistoryRail";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key, fill: (text: string) => text }));
vi.mock("./AgentModelPicker", () => ({ AgentModelPicker: () => null }));
vi.mock("@/components/home/JarvisBar", () => ({ JarvisBar: () => <div data-testid="jarvis-bar" /> }));
vi.mock("@/components/home/Greeting", () => ({ Greeting: () => <div>Greeting</div> }));
vi.mock("@/components/agentic/useVoiceCall", () => ({ useVoiceCall: () => ({ connecting: false }) }));
vi.mock("@/hooks/useVoiceReadiness", () => ({ useVoiceReadiness: () => ({ connected: true, warming: false }) }));
vi.mock("@/hooks/useWakeWord", () => ({ useWakeWord: () => ({ config: { phrase: "Hey Jarvis" } }) }));
vi.mock("../data", async (original) => ({
  ...(await original<typeof import("../data")>()),
  useSocietyCapabilities: () => ({ isLoading: false, data: [] }),
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

class FakeSocket {
  onopen: (() => void) | null = null;
  onmessage: ((msg: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  close() {}
}

function agent(over: Partial<SocietyAgent> & Pick<SocietyAgent, "agentId" | "name">): SocietyAgent {
  return {
    title: "",
    description: "",
    tier: "specialist",
    provider: "openai",
    providerLabel: "OpenAI",
    model: "gpt",
    effort: "medium",
    figure: null,
    palette: { primary: "#000", secondary: "#000", accent: "#000" },
    grantMode: "all",
    toolGrants: [],
    focus: [],
    denies: [],
    approvalRules: { requireApproval: [], alwaysAllow: [] },
    permissionCeiling: "ask",
    dailyBudgetUsd: 0,
    checkpoint: "idle",
    state: "idle",
    lifecycle: "active",
    createdMs: 0,
    maxConcurrentRuns: 1,
    workspaceDir: "",
    wikiNamespace: "",
    chatSessionId: `society:${over.agentId}`,
    routines: [],
    stats: { runs: 0, totalCostUsd: 0, spentTodayUsd: 0, lastActiveMs: null },
    ...over,
  };
}

function timelineWith(text: string): Timeline {
  const item: UserItem = { type: "user", id: text, tsMs: 1, text, attachments: [] };
  return { ...EMPTY_TIMELINE, items: [item], lastSeq: 1 };
}

const visual = agent({ agentId: "visual-qa", name: "Visual QA" });
const gmail = agent({ agentId: "gmail-agent", name: "Gmail Agent" });

beforeEach(() => {
  useHomeStore.setState({ transcript: [], liveReply: "", jarvisCardMode: "voice", freshVoicePending: false });
  useEventStore.setState({ activeKind: "text", activeThreadId: null, messages: [], voiceState: "idle" });
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("WebSocket", FakeSocket);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ providers: [], sessions: [], mapping: [] }), { status: 200 })),
  );
  useSocietyChatStore.getState().disconnect();
  useSocietyChatStore.setState({
    activeSessionId: null,
    activeSession: null,
    timeline: EMPTY_TIMELINE,
    busy: false,
    lastError: null,
    sessions: [],
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("hides another session's items until that session is the open one", () => {
  const visualItems = timelineWith("visual-qa-flowchart").items;
  expect(itemsForOpenSession("society:gmail-agent", "society:visual-qa", visualItems)).toEqual([]);
  expect(itemsForOpenSession("society:gmail-agent", "society:gmail-agent", visualItems)).toEqual(visualItems);
  expect(itemsForOpenSession(null, "society:visual-qa", visualItems)).toEqual([]);
});

it("does not paint the previous specialist's transcript after a click onto another agent", () => {
  useSocietyChatStore.setState({
    activeSessionId: visual.chatSessionId,
    timeline: timelineWith("UNIQUE_VISUAL_QA_FLOWCHART"),
  });
  render(<AgentChatPanel agent={gmail} roster={[visual, gmail]} />);
  expect(screen.queryByText("UNIQUE_VISUAL_QA_FLOWCHART")).toBeNull();
  expect(screen.getByTestId("society-chat").getAttribute("data-session-id")).toBe(gmail.chatSessionId);
  expect(screen.getByTestId("society-chat").getAttribute("data-session-ready")).toBe("true");
});

it("shows the previous specialist's own chat again the moment you switch back", () => {
  useSocietyChatStore.setState({
    activeSessionId: visual.chatSessionId,
    timeline: timelineWith("UNIQUE_VISUAL_QA_FLOWCHART"),
  });
  const view = render(<AgentChatPanel agent={gmail} roster={[visual, gmail]} />);
  act(() => {
    useSocietyChatStore.setState({ timeline: timelineWith("UNIQUE_GMAIL_INBOX") });
  });
  view.rerender(<AgentChatPanel agent={visual} roster={[visual, gmail]} />);
  expect(screen.queryByText("UNIQUE_GMAIL_INBOX")).toBeNull();
  expect(screen.getByText("UNIQUE_VISUAL_QA_FLOWCHART")).toBeTruthy();
  expect(screen.getByTestId("society-chat").getAttribute("data-session-id")).toBe(visual.chatSessionId);
});

it("keeps Jarvis on a fresh chat when history refreshes or the card is reopened", () => {
  const jarvis = agent({ agentId: "jarvis", name: "Jarvis", tier: "lead" });
  const session: AgentChatSession = {
    session_id: "previous-chat", title: "Previous chat", provider: "openai", model: "m",
    effort: "low", cwd: "", permission_mode: "ask", surface: "jarvis", vendor_session: null,
    created_ms: 1, updated_ms: 2, message_count: 1, preview: "Previous chat",
  };
  useAgentChatStore.getState().disconnect();
  useAgentChatStore.setState({ activeSessionId: session.session_id, activeSession: session, sessions: [session], timeline: EMPTY_TIMELINE, busy: false });
  useEventStore.setState({ activeKind: "text", activeThreadId: null });
  setJarvisCardMode("chat");
  const view = render(<AgentChatPanel agent={jarvis} roster={[jarvis]} />);
  fireEvent.click(screen.getByRole("button", { name: "society.chat.new_chat" }));
  expect(useAgentChatStore.getState().activeSessionId).toBeNull();
  act(() => useAgentChatStore.setState({ sessions: [{ ...session }] }));
  expect(useAgentChatStore.getState().activeSessionId).toBeNull();
  view.unmount();
  render(<AgentChatPanel agent={jarvis} roster={[jarvis]} />);
  expect(useAgentChatStore.getState().activeSessionId).toBeNull();
});

it("reads archived calls on the actual voice stage and returns there fresh after hangup while closed", async () => {
  const jarvis = agent({ agentId: "jarvis", name: "Jarvis", tier: "lead" });
  const voice = { kind: "voice", id: "voice-archive", title: "Archived call", preview: "", created_ms: 1, updated_ms: 2, message_count: 2 };
  vi.mocked(fetch).mockImplementation(async (url) => {
    const path = String(url);
    if (path.includes("/api/chats?")) return new Response(JSON.stringify([voice]));
    if (path.includes("/voice/voice-archive/resume")) return new Response(JSON.stringify({ ...voice, messages: [
      { role: "user", text: "Archived question", ts_ms: 1 },
      { role: "assistant", text: "Archived answer", ts_ms: 2 },
    ] }));
    return new Response(JSON.stringify({ providers: [], sessions: [], mapping: [] }));
  });
  const view = render(<><AgentChatPanel agent={jarvis} roster={[jarvis]} /><JarvisHistoryRail /></>);
  fireEvent.click(await screen.findByTestId("jarvis-history-voice-row"));
  expect(await screen.findByText("Archived question")).toBeTruthy();
  expect(screen.getByText("Archived answer")).toBeTruthy();
  expect(screen.getByTestId("society-chat").getAttribute("data-mode")).toBe("voice");
  expect(screen.getByTestId("jarvis-bar")).toBeTruthy();
  expect(screen.queryByTestId("voice-thread-stage")).toBeNull();
  fireEvent.click(screen.getByTestId("society-jarvis-mode-chat"));
  fireEvent.click(screen.getByTestId("society-jarvis-mode-voice"));
  expect(screen.getByText("Archived question")).toBeTruthy();
  view.unmount();
  act(() => {
    setJarvisCardMode("chat");
    useHomeStore.getState().ingest("VoiceSessionEnded", { hangup_reason: "hotkey" }, 3);
  });
  render(<AgentChatPanel agent={jarvis} roster={[jarvis]} />);
  expect(screen.getByTestId("society-chat").getAttribute("data-mode")).toBe("voice");
  expect(screen.getByTestId("voice-stage").getAttribute("data-empty")).toBe("true");
  expect(screen.queryByText("Archived question")).toBeNull();
  await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/chats/voice/new", { method: "POST" }));
});
