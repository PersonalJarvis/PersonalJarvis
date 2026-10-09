import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentChatApiError, type AgentChatCatalog, type AgentChatEvent, type AgentChatSession } from "@/lib/agentChatApi";
import type { IdeProject } from "@/lib/agenticIdeApi";
import { useIdeProjectsStore } from "@/store/ideProjects";
import { useIdeThreadsStore } from "@/store/ideThreads";

const api = vi.hoisted(() => ({
  fetchAgentChatCatalog: vi.fn(),
  fetchAgentConnections: vi.fn(),
  fetchProviderHealth: vi.fn(),
  fetchAgentChatSessions: vi.fn(),
  answerAgentChatQuestion: vi.fn(),
  resolveAgentChatPlan: vi.fn(),
  resolveAgentChatApproval: vi.fn(),
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
    id: "openai-codex", label: "OpenAI Codex", family: "openai", runner: "codex-cli", agent: "codex",
    models_source: "curated", curated_models: [], default_model: "",
    keyless: false, native_resume: true, effort_levels: ["high"], default_effort: "high",
    permission_modes: [{ id: "auto", label: "Auto", description: "" }, { id: "plan", label: "Plan", description: "" }],
    default_permission_mode: "auto", cli_installed: true, typeahead: [],
  }],
} as AgentChatCatalog;

const project = { id: "p1", path: "/repo", name: "App", color: null, pinned: false, archived: false, scratch: false,
  created_at: 0, last_opened_at: 0, exists: true, chats: 0, workspaces: [] } as IdeProject;

function sessionRow(extra: Partial<AgentChatSession> = {}): AgentChatSession {
  return { session_id: "s1", title: "add a database", provider: "openai-codex", model: "", effort: "high", cwd: "/repo",
    permission_mode: "plan", surface: "agent", vendor_session: null, created_ms: 1, updated_ms: 2, message_count: 1, preview: "", ...extra };
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

async function openThread(): Promise<void> {
  useIdeThreadsStore.setState({ selection: { projectId: "p1", sessionId: "s1" } });
  api.fetchAgentChatSessions.mockResolvedValue([sessionRow()]);
  render(<ThreadView onScreen />);
  await waitFor(() => expect(useThreadChatStore.getState().activeSessionId).toBe("s1"));
  feed([
    ["user_message", { text: "add a database" }],
    ["turn_started", { turn_id: "t1", provider: "openai-codex", model: "", effort: "high", runner: "codex-cli" }],
  ]);
}

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("WebSocket", FakeSocket);
  api.fetchAgentChatCatalog.mockReset().mockResolvedValue(CATALOG);
  api.fetchAgentConnections.mockReset().mockResolvedValue([]);
  api.fetchProviderHealth.mockReset().mockResolvedValue([]);
  api.fetchAgentChatSessions.mockReset().mockResolvedValue([]);
  api.answerAgentChatQuestion.mockReset().mockResolvedValue(undefined);
  api.resolveAgentChatPlan.mockReset().mockResolvedValue(undefined);
  api.resolveAgentChatApproval.mockReset().mockResolvedValue(undefined);
  useIdeProjectsStore.setState({ projects: [project], activeWorkspaceId: null });
  useIdeThreadsStore.setState({ layout: "threads", selection: { projectId: "p1", sessionId: null }, projectOf: {}, seen: {}, archived: {}, order: {}, focusNonce: 0 });
  useThreadChatStore.getState().newChat();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("a coding agent's questions and plans in a thread", () => {
  it("asks an end-of-turn question on the composer's card and keeps the reply readable", async () => {
    await openThread();
    feed([
      ["assistant_text", { turn_id: "t1", message_id: "m1", text: "I need one decision.\n\n```jarvis-ask\n{\"questions\": []}\n```" }],
      ["turn_finished", { turn_id: "t1", status: "done", duration_ms: 1000 }],
      ["question_required", {
        turn_id: "t1", question_id: "q1", deferred: true,
        questions: [{ question: "Which database?", options: [{ label: "SQLite" }, { label: "Postgres" }] }],
      }],
    ]);
    expect(screen.getByText("I need one decision.")).toBeTruthy();
    expect(screen.queryByText(/jarvis-ask/)).toBeNull();
    expect(screen.getByTestId("thread-question").textContent).toContain("Which database?");
    await act(async () => { fireEvent.click(screen.getByText("Postgres")); });
    expect(api.answerAgentChatQuestion).toHaveBeenCalledWith("s1", "q1", 0, { optionIndex: 1 });
  });

  it("offers to build a finished plan in the agent's build mode", async () => {
    await openThread();
    feed([
      ["assistant_text", { turn_id: "t1", message_id: "m1", text: "1. Add the table\n2. Wire the route" }],
      ["turn_finished", { turn_id: "t1", status: "done", duration_ms: 1000 }],
      ["plan_ready", { turn_id: "t1", build_mode: "auto" }],
    ]);
    const card = await screen.findByTestId("thread-plan");
    expect(card.textContent).toContain("switches access to Auto");
    await act(async () => { fireEvent.click(screen.getByTestId("thread-plan-build")); });
    expect(api.resolveAgentChatPlan).toHaveBeenCalledWith("s1", "t1", "build");
    feed([["plan_resolved", { turn_id: "t1", decision: "build" }]]);
    expect(screen.queryByTestId("thread-plan")).toBeNull();
  });

  it("shows Claude's plan on its approval card, without an always-allow", async () => {
    await openThread();
    feed([
      ["approval_required", {
        turn_id: "t1", approval_id: "a1", call_id: "c1", name: "ExitPlanMode",
        input: { plan: "## Steps\n\n1. Add the table" }, summary: "Plan ready — approve to start building",
      }],
    ]);
    const card = screen.getByTestId("thread-approval");
    expect(card.textContent).toContain("Plan ready");
    expect(screen.getByTestId("thread-approval-plan").textContent).toContain("Add the table");
    expect(screen.queryByText("Always allow")).toBeNull();
    await act(async () => { fireEvent.click(screen.getByTestId("thread-approve")); });
    expect(api.resolveAgentChatApproval).toHaveBeenCalledWith("s1", "a1", "allow");
  });
});

describe("a thread's approval card", () => {
  function ask(approvalId: string, callId: string, command: string): [string, Record<string, unknown>] {
    return ["approval_required", { turn_id: "t1", approval_id: approvalId, call_id: callId, name: "Bash", input: { command }, summary: command }];
  }

  it("closes as soon as the backend took the answer, and stays closed for one click", async () => {
    await openThread();
    feed([ask("a1", "c1", "ls")]);
    let finish: () => void = () => undefined;
    api.resolveAgentChatApproval.mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
    fireEvent.click(screen.getByTestId("thread-approve"));
    // On its way: every button holds still, so a second click cannot answer twice.
    for (const button of screen.getByTestId("thread-approval").querySelectorAll("button")) {
      expect((button as HTMLButtonElement).disabled).toBe(true);
    }
    fireEvent.click(screen.getByTestId("thread-approve"));
    await act(async () => { finish(); });
    expect(api.resolveAgentChatApproval).toHaveBeenCalledTimes(1);
    // Gone before the socket's approval_resolved arrived.
    expect(screen.queryByTestId("thread-approval")).toBeNull();
  });

  it("drops a card the backend says expired and shows the live one behind it", async () => {
    await openThread();
    // 2026-10-06: the card from before a restart sat on top of the live one.
    feed([ask("old", "c1", "ls"), ask("live", "c2", "git log")]);
    expect(screen.getByTestId("thread-approval-count").textContent).toBe("1 of 2");
    api.resolveAgentChatApproval.mockRejectedValueOnce(new AgentChatApiError("expired", 410));
    await act(async () => { fireEvent.click(screen.getByTestId("thread-approve")); });
    const card = screen.getByTestId("thread-approval");
    expect(card.textContent).toContain("git log");
    expect(card.querySelector("[role=alert]")).toBeNull();
    expect(screen.queryByTestId("thread-approval-count")).toBeNull();
    await act(async () => { fireEvent.click(screen.getByTestId("thread-approve")); });
    expect(api.resolveAgentChatApproval).toHaveBeenLastCalledWith("s1", "live", "allow");
  });

  it("keeps the card and says why when the answer did not go through", async () => {
    await openThread();
    feed([ask("a1", "c1", "ls")]);
    api.resolveAgentChatApproval.mockRejectedValueOnce(new AgentChatApiError("server error", 500));
    await act(async () => { fireEvent.click(screen.getByTestId("thread-approve")); });
    const card = screen.getByTestId("thread-approval");
    expect(card.querySelector("[role=alert]")?.textContent).toBe("server error");
    expect((screen.getByTestId("thread-approve") as HTMLButtonElement).disabled).toBe(false);
  });
});
