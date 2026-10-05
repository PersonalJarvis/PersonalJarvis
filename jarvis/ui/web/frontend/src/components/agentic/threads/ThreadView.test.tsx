import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentChatCatalog, AgentChatSession } from "@/lib/agentChatApi";
import type { IdeProject } from "@/lib/agenticIdeApi";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeThreadsStore } from "@/store/ideThreads";

const api = vi.hoisted(() => ({
  fetchAgentChatCatalog: vi.fn(),
  fetchAgentConnections: vi.fn(),
  fetchProviderHealth: vi.fn(),
  fetchAgentChatSessions: vi.fn(),
  createAgentChatSession: vi.fn(),
  sendAgentChatMessage: vi.fn(),
  patchAgentChatSession: vi.fn(),
}));
vi.mock("@/lib/agentChatApi", async (importOriginal) => ({ ...(await importOriginal<typeof import("@/lib/agentChatApi")>()), ...api }));
vi.mock("@/lib/gitApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/gitApi")>()),
  inspectGit: vi.fn().mockResolvedValue({ is_repo: false, branches: [], changes: [], remotes: [] }),
  prepareGit: vi.fn(),
}));
vi.mock("@/lib/chatLibraryApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/chatLibraryApi")>()),
  fetchProjectLaunchers: vi.fn().mockResolvedValue({ file_manager: true, editors: [{ id: "code", label: "VS Code" }], remote_url: null, remote_label: null }),
}));

import { ThreadView } from "./ThreadView";
import { ThreadTree } from "./ThreadTree";
import { useThreadChatStore } from "./threadModel";

class FakeSocket {
  static instances: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((msg: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly url: string) { FakeSocket.instances.push(this); }
  close() { /* the test drives frames by hand */ }
}

const CATALOG: AgentChatCatalog = {
  default_cwd: "/home",
  shell: "bash",
  selection: null,
  providers: [{
    id: "claude-api", label: "Anthropic Claude", family: "anthropic", runner: "claude-cli", agent: "claude",
    models_source: "curated", curated_models: [{ id: "claude-opus-5-5", label: "Opus 5.5" }], default_model: "",
    keyless: false, native_resume: true, effort_levels: ["low", "high"], default_effort: "high",
    permission_modes: [{ id: "default", label: "Ask first", description: "" }], default_permission_mode: "default",
    cli_installed: true, typeahead: [],
  }],
} as AgentChatCatalog;

const project = { id: "p1", path: "/repo", name: "App", color: null, pinned: false, archived: false, scratch: false,
  created_at: 0, last_opened_at: 0, exists: true, chats: 0, workspaces: [] } as IdeProject;

function sessionRow(extra: Partial<AgentChatSession> = {}): AgentChatSession {
  return { session_id: "s1", title: "fix the login test", provider: "claude-api", model: "", effort: "high", cwd: "/repo",
    permission_mode: "default", surface: "agent", vendor_session: null, created_ms: 1, updated_ms: 2, message_count: 1, preview: "", ...extra };
}

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("WebSocket", FakeSocket);
  FakeSocket.instances = [];
  api.fetchAgentChatCatalog.mockReset().mockResolvedValue(CATALOG);
  api.fetchAgentConnections.mockReset().mockResolvedValue([]);
  api.fetchProviderHealth.mockReset().mockResolvedValue([]);
  api.fetchAgentChatSessions.mockReset().mockResolvedValue([]);
  api.createAgentChatSession.mockReset().mockResolvedValue(sessionRow());
  api.sendAgentChatMessage.mockReset().mockResolvedValue({ turn_id: "t1" });
  api.patchAgentChatSession.mockReset().mockResolvedValue(sessionRow());
  useIdeProjectsStore.setState({ projects: [project], activeWorkspaceId: null });
  useIdeThreadsStore.setState({ layout: "threads", selection: { projectId: "p1", sessionId: null }, projectOf: {}, seen: {}, focusNonce: 0 });
  useThreadChatStore.getState().newChat();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ThreadView", () => {
  it("starts a new thread in the project's folder with its first message", async () => {
    render(<ThreadView onScreen />);
    expect(screen.getByText(/What should we build in/)).toBeTruthy();
    expect(screen.getByTestId("thread-project-picker").textContent).toContain("App");
    await waitFor(() => expect(useThreadChatStore.getState().draft.provider).toBe("claude-api"));
    await waitFor(() => expect(useThreadChatStore.getState().draft.cwd).toBe("/repo"));

    const box = screen.getByTestId("thread-composer-input");
    fireEvent.change(box, { target: { value: "fix the login test" } });
    await act(async () => { fireEvent.keyDown(box, { key: "Enter" }); });

    await waitFor(() => expect(api.sendAgentChatMessage).toHaveBeenCalled());
    expect(api.createAgentChatSession).toHaveBeenCalledWith(expect.objectContaining({ provider: "claude-api", cwd: "/repo", surface: "agent" }));
    expect(api.sendAgentChatMessage.mock.calls[0][1]).toBe("fix the login test");
    await waitFor(() => expect(useIdeThreadsStore.getState().selection).toEqual({ projectId: "p1", sessionId: "s1" }));
    expect(useIdeThreadsStore.getState().projectOf).toEqual({ s1: "p1" });
  });

  it("starts a new thread on the agent and model the person picked last", async () => {
    localStorage.setItem("jarvis.ide.threadSeat.v1", JSON.stringify({ provider: "claude-api", model: "claude-opus-5-5", effort: "low", permissionMode: "default" }));
    render(<ThreadView onScreen />);
    await waitFor(() => expect(useThreadChatStore.getState().draft).toEqual(expect.objectContaining({ provider: "claude-api", model: "claude-opus-5-5", effort: "low" })));
    await waitFor(() => expect(screen.getByTestId("thread-model-picker").textContent).toContain("Opus 5.5"));
  });

  it("files a thread started in a draft under its project when the person moved on meanwhile", async () => {
    let finishCreate: (row: AgentChatSession) => void = () => {};
    api.createAgentChatSession.mockReturnValue(new Promise<AgentChatSession>((resolve) => { finishCreate = resolve; }));
    render(<ThreadView onScreen />);
    await waitFor(() => expect(useThreadChatStore.getState().draft.cwd).toBe("/repo"));
    const box = screen.getByTestId("thread-composer-input");
    fireEvent.change(box, { target: { value: "start something" } });
    await act(async () => { fireEvent.keyDown(box, { key: "Enter" }); });
    await waitFor(() => expect(api.createAgentChatSession).toHaveBeenCalled());

    // The person opens another thread before the first one exists.
    act(() => useIdeThreadsStore.getState().openThread("other", "p1"));
    await act(async () => { finishCreate(sessionRow({ session_id: "s-new" })); });

    await waitFor(() => expect(useIdeThreadsStore.getState().projectOf).toEqual({ "s-new": "p1" }));
    expect(useIdeThreadsStore.getState().selection).toEqual({ projectId: "p1", sessionId: "other" });
    await waitFor(() => expect(useThreadChatStore.getState().activeSessionId).toBe("other"));
  });

  it("draws the turn: the message, the work as one line, the answer and how long it took", async () => {
    useIdeThreadsStore.setState({ selection: { projectId: "p1", sessionId: "s1" } });
    api.fetchAgentChatSessions.mockResolvedValue([sessionRow()]);
    render(<ThreadView onScreen />);
    const store = useThreadChatStore.getState();
    act(() => {
      store.ingest({ seq: 1, ts_ms: 1000, kind: "user_message", payload: { text: "fix the login test" } });
      store.ingest({ seq: 2, ts_ms: 1000, kind: "turn_started", payload: { turn_id: "t1", provider: "claude-api", model: "", effort: "high", runner: "claude-cli" } });
      store.ingest({ seq: 3, ts_ms: 1100, kind: "tool_call", payload: { turn_id: "t1", call_id: "c1", name: "Bash", input: { command: "npm test" } } });
      store.ingest({ seq: 4, ts_ms: 1200, kind: "tool_result", payload: { turn_id: "t1", call_id: "c1", output: "ok", is_error: false, duration_ms: 100 } });
      store.ingest({ seq: 5, ts_ms: 1300, kind: "tool_call", payload: { turn_id: "t1", call_id: "c2", name: "Read", input: { file_path: "/repo/src/login.ts" } } });
      store.ingest({ seq: 6, ts_ms: 1400, kind: "tool_result", payload: { turn_id: "t1", call_id: "c2", output: "code", is_error: false, duration_ms: 50 } });
      store.ingest({ seq: 7, ts_ms: 1500, kind: "assistant_text", payload: { turn_id: "t1", message_id: "m1", text: "The test passes now." } });
      store.ingest({ seq: 8, ts_ms: 4000, kind: "turn_finished", payload: { turn_id: "t1", status: "done", duration_ms: 3000, usage: null, error: null } });
    });
    expect(screen.getByTestId("thread-user-message").textContent).toContain("fix the login test");
    expect(screen.getByText("The test passes now.")).toBeTruthy();
    expect(screen.getByTestId("thread-turn").getAttribute("data-status")).toBe("done");
    expect(screen.getByText(/Worked for/)).toBeTruthy();
    // Two calls fold to one line until opened.
    expect(screen.queryByText("npm test")).toBeNull();
  });
});

describe("ThreadTree", () => {
  it("lists a project's threads by the CLI's own title with the agent's mark", async () => {
    api.fetchAgentChatSessions.mockResolvedValue([sessionRow({ cli_title: "Fix login test" }), sessionRow({ session_id: "s2", cwd: "/elsewhere", title: "stray" })]);
    render(<ThreadTree />);
    await waitFor(() => expect(screen.getByText("Fix login test")).toBeTruthy());
    expect(screen.queryByText("stray")).toBeNull();
    fireEvent.click(screen.getByText("Fix login test"));
    expect(useIdeThreadsStore.getState().selection).toEqual({ projectId: "p1", sessionId: "s1" });
    fireEvent.click(screen.getByTestId("thread-new-p1"));
    expect(useIdeThreadsStore.getState().selection).toEqual({ projectId: "p1", sessionId: null });
  });
});
