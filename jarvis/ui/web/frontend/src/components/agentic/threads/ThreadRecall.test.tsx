import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentChatCatalog, AgentChatEvent, AgentChatSession } from "@/lib/agentChatApi";
import type { IdeProject } from "@/lib/agenticIdeApi";
import { forgetHeldFiles } from "@/components/agentchat/useChatAttachments";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeThreadsStore } from "@/store/ideThreads";

const api = vi.hoisted(() => ({
  fetchAgentChatCatalog: vi.fn(),
  fetchAgentConnections: vi.fn(),
  fetchProviderHealth: vi.fn(),
  fetchAgentChatSessions: vi.fn(),
  sendAgentChatMessage: vi.fn(),
  cancelAgentChatTurn: vi.fn(),
  attachChatFilesIn: vi.fn(),
}));
vi.mock("@/lib/agentChatApi", async (importOriginal) => ({ ...(await importOriginal<typeof import("@/lib/agentChatApi")>()), ...api }));
vi.mock("@/lib/gitApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/gitApi")>()),
  inspectGit: vi.fn().mockResolvedValue({ is_repo: false, branches: [], changes: [], remotes: [] }),
  prepareGit: vi.fn(),
}));
vi.mock("@/lib/chatLibraryApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/chatLibraryApi")>()),
  fetchProjectLaunchers: vi.fn().mockResolvedValue({ file_manager: true, editors: [], remote_url: null, remote_label: null }),
}));

import { ThreadView } from "./ThreadView";
import { useThreadChatStore } from "./threadModel";

/**
 * Escape takes a message back while the agent has not started on it: the turn
 * stops and the words and files return to the composer.
 */

class FakeSocket {
  onopen: (() => void) | null = null;
  onmessage: ((msg: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly url: string) {}
  close() { /* frames are driven by hand */ }
}

const CATALOG: AgentChatCatalog = {
  default_cwd: "/home",
  shell: "bash",
  selection: null,
  providers: [{
    id: "claude-api", label: "Anthropic Claude", family: "anthropic", runner: "claude-cli", agent: "claude",
    models_source: "curated", curated_models: [], default_model: "",
    keyless: false, native_resume: true, effort_levels: ["high"], default_effort: "high",
    permission_modes: [{ id: "default", label: "Ask first", description: "" }], default_permission_mode: "default",
    cli_installed: true, typeahead: [],
  }],
} as AgentChatCatalog;

const project = { id: "p1", path: "/repo", name: "App", color: null, pinned: false, archived: false, scratch: false,
  created_at: 0, last_opened_at: 0, exists: true, chats: 0, workspaces: [] } as IdeProject;

const SHOT = { name: "shot.png", reference: '".jarvis/drops/shot.png"', kind: "image" as const,
  detail: "A screenshot.", described_by: "vision" as const, note: "" };

function sessionRow(extra: Partial<AgentChatSession> = {}): AgentChatSession {
  return { session_id: "s1", title: "fix it", provider: "claude-api", model: "", effort: "high", cwd: "/repo",
    permission_mode: "default", surface: "agent", vendor_session: null, created_ms: 1, updated_ms: 2, message_count: 1, preview: "", ...extra };
}

let seq = 0;
function feed(events: [string, Record<string, unknown>][]): void {
  const store = useThreadChatStore.getState();
  act(() => {
    for (const [kind, payload] of events) {
      seq += 1;
      store.ingest({ seq, ts_ms: 1000 + seq, kind, payload } as AgentChatEvent);
    }
  });
}

function transfer(files: File[]) {
  return {
    files,
    items: files.map((file) => ({ kind: "file", type: file.type, getAsFile: () => file })),
    types: files.length ? ["Files"] : [],
    getData: () => "",
    dropEffect: "none",
  };
}

/** Open thread s1, attach a screenshot, type and send. */
async function sendWithShot(text: string): Promise<HTMLTextAreaElement> {
  useIdeThreadsStore.setState({ selection: { projectId: "p1", sessionId: "s1" } });
  api.fetchAgentChatSessions.mockResolvedValue([sessionRow()]);
  render(<ThreadView onScreen />);
  await waitFor(() => expect(useThreadChatStore.getState().activeSessionId).toBe("s1"));
  feed([
    ["user_message", { text: "earlier" }],
    ["turn_started", { turn_id: "t0", provider: "claude-api", model: "", effort: "high", runner: "claude-cli" }],
    ["turn_finished", { turn_id: "t0", status: "done", duration_ms: 10 }],
  ]);
  const box = screen.getByTestId("thread-composer-input") as HTMLTextAreaElement;
  const png = new File([new Uint8Array([1, 2, 3])], "image.png", { type: "image/png" });
  await act(async () => { fireEvent.paste(box, { clipboardData: transfer([png]) }); });
  await waitFor(() => expect(screen.getByTestId("chat-attachment-shot.png")).toBeTruthy());
  fireEvent.change(box, { target: { value: text } });
  await act(async () => { fireEvent.keyDown(box, { key: "Enter" }); });
  await waitFor(() => expect(api.sendAgentChatMessage).toHaveBeenCalled());
  expect(box.value).toBe("");
  expect(screen.queryByTestId("chat-attachment-shot.png")).toBeNull();
  feed([
    ["user_message", { text: `${text}\n\n[file]`, typed: text, attachments: [{ name: "shot.png", kind: "image", described_by: "vision" }] }],
    ["turn_started", { turn_id: "t1", provider: "claude-api", model: "", effort: "high", runner: "claude-cli" }],
  ]);
  return box;
}

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("WebSocket", FakeSocket);
  api.fetchAgentChatCatalog.mockReset().mockResolvedValue(CATALOG);
  api.fetchAgentConnections.mockReset().mockResolvedValue([{ jarvis: "claude-api", key_set: true, is_active_brain: true }]);
  api.fetchProviderHealth.mockReset().mockResolvedValue([]);
  api.fetchAgentChatSessions.mockReset().mockResolvedValue([]);
  api.sendAgentChatMessage.mockReset().mockResolvedValue({ turn_id: "t1" });
  api.cancelAgentChatTurn.mockReset().mockResolvedValue(undefined);
  api.attachChatFilesIn.mockReset().mockResolvedValue({ attachments: [SHOT], cwd: "" });
  useIdeProjectsStore.setState({ projects: [project], activeWorkspaceId: null });
  useIdeThreadsStore.setState({ layout: "threads", selection: { projectId: "p1", sessionId: null }, projectOf: {}, seen: {}, archived: {}, order: {}, focusNonce: 0 });
  useThreadChatStore.getState().newChat();
});

afterEach(() => {
  cleanup();
  forgetHeldFiles(useThreadChatStore);
  vi.unstubAllGlobals();
});

describe("Escape takes back a message the agent has not started on", () => {
  it("stops the turn and returns the words and the screenshot to the composer", async () => {
    const box = await sendWithShot("fix the login");
    expect(api.sendAgentChatMessage.mock.calls[0][2]).toEqual([SHOT]);

    await act(async () => { fireEvent.keyDown(window, { key: "Escape" }); });

    expect(api.cancelAgentChatTurn).toHaveBeenCalledWith("s1", "thread-recall");
    expect(box.value).toBe("fix the login");
    expect(screen.getByTestId("chat-attachment-shot.png")).toBeTruthy();
  });

  it("takes the message out of the thread, and it stays out once the stopped turn closes", async () => {
    await sendWithShot("fix the login");
    expect(screen.getAllByTestId("thread-user-message")).toHaveLength(2);

    await act(async () => { fireEvent.keyDown(window, { key: "Escape" }); });
    feed([["turn_finished", { turn_id: "t1", status: "cancelled", duration_ms: 10 }]]);

    expect(screen.getAllByTestId("thread-user-message")).toHaveLength(1);
    expect(screen.getAllByTestId("thread-turn")).toHaveLength(1);
  });

  it("still takes it back while the agent thinks inside the first seconds", async () => {
    const box = await sendWithShot("fix the login");
    feed([["reasoning_started", { turn_id: "t1" }], ["text_delta", { turn_id: "t1", text: "Looking" }]]);

    await act(async () => { fireEvent.keyDown(window, { key: "Escape" }); });

    expect(api.cancelAgentChatTurn).toHaveBeenCalledWith("s1", "thread-recall");
    expect(box.value).toBe("fix the login");
    expect(screen.getByTestId("chat-attachment-shot.png")).toBeTruthy();
  });

  it("takes it back before the agent's turn even started", async () => {
    useIdeThreadsStore.setState({ selection: { projectId: "p1", sessionId: "s1" } });
    api.fetchAgentChatSessions.mockResolvedValue([sessionRow()]);
    render(<ThreadView onScreen />);
    await waitFor(() => expect(useThreadChatStore.getState().activeSessionId).toBe("s1"));
    feed([["user_message", { text: "earlier" }], ["turn_started", { turn_id: "t0", provider: "claude-api", model: "", effort: "high", runner: "claude-cli" }],
      ["turn_finished", { turn_id: "t0", status: "done", duration_ms: 10 }]]);
    const box = screen.getByTestId("thread-composer-input") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "rename it" } });
    await act(async () => { fireEvent.keyDown(box, { key: "Enter" }); });
    await waitFor(() => expect(api.sendAgentChatMessage).toHaveBeenCalled());
    feed([["user_message", { text: "rename it" }]]);

    await act(async () => { fireEvent.keyDown(window, { key: "Escape" }); });

    expect(box.value).toBe("rename it");
    expect(screen.getAllByTestId("thread-user-message")).toHaveLength(1);
  });

  it("takes it back when the stored text differs from the box, as a dictated one can", async () => {
    useIdeThreadsStore.setState({ selection: { projectId: "p1", sessionId: "s1" } });
    api.fetchAgentChatSessions.mockResolvedValue([sessionRow()]);
    render(<ThreadView onScreen />);
    await waitFor(() => expect(useThreadChatStore.getState().activeSessionId).toBe("s1"));
    feed([["user_message", { text: "earlier" }], ["turn_started", { turn_id: "t0", provider: "claude-api", model: "", effort: "high", runner: "claude-cli" }],
      ["turn_finished", { turn_id: "t0", status: "done", duration_ms: 10 }]]);
    const box = screen.getByTestId("thread-composer-input") as HTMLTextAreaElement;
    const typed = "Ich möchte Favoriten";
    fireEvent.change(box, { target: { value: typed } });
    await act(async () => { fireEvent.keyDown(box, { key: "Enter" }); });
    await waitFor(() => expect(api.sendAgentChatMessage).toHaveBeenCalled());
    feed([["user_message", { text: typed.normalize("NFC") }],
      ["turn_started", { turn_id: "t1", provider: "claude-api", model: "", effort: "high", runner: "claude-cli" }]]);

    await act(async () => { fireEvent.keyDown(box, { key: "Escape" }); });

    expect(api.cancelAgentChatTurn).toHaveBeenCalledWith("s1", "thread-recall");
    expect(box.value).toBe(typed);
    expect(screen.getAllByTestId("thread-user-message")).toHaveLength(1);
  });

  it("gets the Escape before another handler can swallow it", async () => {
    const swallow = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); } };
    document.addEventListener("keydown", swallow, true);
    try {
      const box = await sendWithShot("fix the login");
      await act(async () => { fireEvent.keyDown(box, { key: "Escape" }); });
      expect(box.value).toBe("fix the login");
    } finally {
      document.removeEventListener("keydown", swallow, true);
    }
  });

  it("never hands back an older message once a newer one went out", async () => {
    const box = await sendWithShot("fix the login");
    feed([["turn_finished", { turn_id: "t1", status: "done", duration_ms: 10 }],
      ["user_message", { text: "from the voice mirror" }],
      ["turn_started", { turn_id: "t2", provider: "claude-api", model: "", effort: "high", runner: "claude-cli" }]]);

    await act(async () => { fireEvent.keyDown(window, { key: "Escape" }); });

    expect(api.cancelAgentChatTurn).not.toHaveBeenCalled();
    expect(box.value).toBe("");
  });

  it("leaves the message alone once the agent thought past the window", async () => {
    const now = Date.now();
    const clock = vi.spyOn(Date, "now").mockReturnValue(now);
    const box = await sendWithShot("fix the login");
    feed([["reasoning_started", { turn_id: "t1" }]]);
    clock.mockReturnValue(now + 16_000);

    await act(async () => { fireEvent.keyDown(window, { key: "Escape" }); });
    clock.mockRestore();

    expect(api.cancelAgentChatTurn).not.toHaveBeenCalled();
    expect(box.value).toBe("");
    expect(screen.getAllByTestId("thread-user-message")).toHaveLength(2);
  });

  it("leaves the message alone once the agent ran a tool", async () => {
    const box = await sendWithShot("fix the login");
    feed([["tool_call", { turn_id: "t1", call_id: "c1", name: "Bash", input: { command: "ls" } }]]);

    await act(async () => { fireEvent.keyDown(window, { key: "Escape" }); });

    expect(api.cancelAgentChatTurn).not.toHaveBeenCalled();
    expect(box.value).toBe("");
    expect(screen.queryByTestId("chat-attachment-shot.png")).toBeNull();
  });
});
