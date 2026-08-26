import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WorkspaceChats, groupByFolder } from "@/components/agentic/WorkspaceChats";
import { useAgentSessionStore } from "@/store/agentChat";
import { useIdeChatStore } from "@/store/ideChat";
import {
  resetWorkspacePanesPoll,
  useWorkspacePanesStore,
} from "@/store/workspacePanes";
import type { AgentChatSession } from "@/lib/agentChatApi";
import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";

const HERE = "C:\\Users\\dev\\Personal Jarvis";
const THERE = "C:\\Users\\dev\\AiGrokAgents";

function chat(id: string, title: string, cwd: string, updated: number): AgentChatSession {
  return {
    session_id: id,
    title,
    provider: "claude-cli",
    model: "",
    effort: "",
    cwd,
    permission_mode: "acceptEdits",
    vendor_session: null,
    created_ms: updated - 100,
    updated_ms: updated,
    message_count: 2,
    preview: title,
  };
}

function pane(
  name: string,
  displayName: string,
  workspaceId: string,
  status: WorkspacePaneRow["status"] = "live",
): WorkspacePaneRow {
  return {
    workspace_id: workspaceId,
    workspace_name: "Personal Jarvis",
    folder: HERE,
    workspace_active: workspaceId === "w1",
    key: name,
    history_id: `${name}@${workspaceId}`,
    name,
    agent: "claude",
    display_name: displayName,
    accepts_prompts: true,
    status,
    exit_code: null,
    activity: "",
    activity_since: 0,
    worked: true,
    started_at: 1,
    last_output_at: 2,
    last_prompt: "",
    last_prompt_at: null,
    has_resume: false,
    readable: true,
    account: null,
    account_label: null,
  };
}

const PANES = [
  pane("T1", "Claude Code", "w1"),
  pane("T2", "Codex", "w1", "exited"),
  // Another workspace's pane: it belongs to that tab's list, not this one.
  pane("T9", "Elsewhere", "w2"),
];

const SESSIONS = [
  chat("a", "Restore Command Deck", HERE, 9_000),
  chat("b", "Plan the marketing", HERE, 8_000),
  chat("c", "Find five leads", THERE, 7_000),
];

const openSession = vi.fn();
const removeSession = vi.fn();
const setDraft = vi.fn();
const newChat = vi.fn();

describe("the sidebar's chat face", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetWorkspacePanesPoll();
    useWorkspacePanesStore.setState({
      panes: PANES,
      activeId: "w1",
      loaded: true,
      // The component subscribes to the shared poll; a real fetch in jsdom
      // would only be caught and discarded, so the seed stands in for it.
      load: async () => {},
    });
    useIdeChatStore.setState({
      paneRequest: null,
      stagedPane: null,
      view: "chat",
      sidebarFace: "chats",
      workspace: { id: "w1", name: "Personal Jarvis", path: HERE },
    });
    useAgentSessionStore.setState({
      sessions: SESSIONS,
      activeSessionId: "a",
      loadSessions: async () => {},
      openSession,
      removeSession: async (id: string) => {
        removeSession(id);
      },
      newChat,
      setDraft: async (patch) => {
        setDraft(patch);
      },
    });
  });

  afterEach(cleanup);

  it("lists the coding sessions running in the open workspace", () => {
    // The complaint this guards (maintainer, 2026-08-25): switching from the
    // terminal grid to chat left the workspace's sessions behind, because this
    // column knew only about chat sessions.
    render(<WorkspaceChats />);

    const rows = screen.getAllByTestId("workspace-session-row");
    expect(rows.map((row) => row.dataset.pane)).toEqual(["T1", "T2"]);
    expect(rows[0].textContent).toContain("Claude Code");
    // Another workspace's pane belongs to that tab, not to this column.
    expect(rows.map((row) => row.dataset.pane)).not.toContain("T9");
  });

  it("asks the view to bring the session that was clicked to the front", () => {
    render(<WorkspaceChats />);

    fireEvent.click(screen.getByText("Codex"));

    const asked = useIdeChatStore.getState().paneRequest;
    expect(asked?.workspaceId).toBe("w1");
    expect(asked?.pane).toBe("T2");
  });

  it("marks the session the chat view has on stage", () => {
    useIdeChatStore.setState({ stagedPane: "T2" });
    render(<WorkspaceChats />);

    const rows = screen.getAllByTestId("workspace-session-row");
    const staged = rows.find((row) => row.dataset.pane === "T2");
    expect(staged?.className).toContain("bg-card");
    expect(rows.find((row) => row.dataset.pane === "T1")?.className).not.toContain("bg-card");
  });

  it("puts the open workspace's folder first, its chats under it", () => {
    render(<WorkspaceChats />);

    const folders = screen
      .getAllByTestId("workspace-chats-folder")
      .map((node) => node.dataset.folder);
    expect(folders[0]).toBe(HERE);
    expect(folders).toContain(THERE);
    expect(screen.getByText("Restore Command Deck")).toBeTruthy();
    expect(screen.getByText("Find five leads")).toBeTruthy();
  });

  it("opens the chat that was clicked", () => {
    render(<WorkspaceChats />);

    fireEvent.click(screen.getByText("Plan the marketing"));

    expect(openSession).toHaveBeenCalledWith("b");
  });

  it("hands the navigation back through a button that says so", () => {
    render(<WorkspaceChats />);

    fireEvent.click(screen.getByTestId("workspace-chats-back"));

    expect(useIdeChatStore.getState().sidebarFace).toBe("sections");
  });

  it("starts a new chat in the folder it was asked from", () => {
    render(<WorkspaceChats />);

    fireEvent.click(screen.getByTestId(`workspace-chats-new-in-${THERE}`));

    expect(newChat).toHaveBeenCalled();
    expect(setDraft).toHaveBeenCalledWith({ cwd: THERE });
  });
});

describe("grouping chats by folder", () => {
  it("names each group after the folder's last segment, on either platform", () => {
    const groups = groupByFolder(SESSIONS, HERE);

    expect(groups[0].label).toBe("Personal Jarvis");
    expect(groups[1].label).toBe("AiGrokAgents");
    expect(groupByFolder([chat("x", "t", "/home/dev/site", 1)], "")[0].label).toBe("site");
  });

  it("shows the open workspace even before it has a single chat", () => {
    // The column has to say where a new chat would land. A folder that appears
    // only once it has history leaves a fresh workspace looking folderless.
    const groups = groupByFolder([chat("c", "elsewhere", THERE, 5)], HERE);

    expect(groups[0].folder).toBe(HERE);
    expect(groups[0].chats).toEqual([]);
  });

  it("orders the other folders by the chat touched last", () => {
    const older = chat("old", "older", "/a", 10);
    const newer = chat("new", "newer", "/b", 900);
    const groups = groupByFolder([older, newer], "");

    expect(groups.map((g) => g.folder)).toEqual(["/b", "/a"]);
  });
});
