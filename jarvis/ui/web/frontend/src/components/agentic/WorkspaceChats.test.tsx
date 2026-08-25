import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WorkspaceChats, groupByFolder } from "@/components/agentic/WorkspaceChats";
import { useAgentSessionStore } from "@/store/agentChat";
import { useIdeChatStore } from "@/store/ideChat";
import type { AgentChatSession } from "@/lib/agentChatApi";

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
    useIdeChatStore.setState({
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
