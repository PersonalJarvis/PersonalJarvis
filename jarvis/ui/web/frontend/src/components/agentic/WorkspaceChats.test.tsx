/**
 * The sidebar's chat face: every open workspace as a band — number, folder,
 * sessions, a way to open one more — the active one marked, and every click
 * travelling through the store to the view that performs it.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { WorkspaceChats } from "@/components/agentic/WorkspaceChats";
import { useIdeChatStore } from "@/store/ideChat";
import { resetWorkspacePanesPoll, useWorkspacePanesStore } from "@/store/workspacePanes";
import type { WorkspacePaneRow } from "@/lib/agenticIdeApi";

const HERE = "C:\\Users\\dev\\Personal Jarvis";
const THERE = "C:\\Users\\dev\\Jarvis Web UI";

function pane(
  name: string,
  displayName: string,
  workspaceId: string,
  status: WorkspacePaneRow["status"] = "live",
  overrides: Partial<WorkspacePaneRow> = {},
): WorkspacePaneRow {
  return {
    workspace_id: workspaceId,
    workspace_name: workspaceId === "w1" ? "Personal Jarvis" : "Jarvis Web UI",
    folder: workspaceId === "w1" ? HERE : THERE,
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
    recap: "",
    has_resume: false,
    readable: true,
    account: null,
    account_label: null,
    ...overrides,
  };
}

const PANES = [
  pane("T1", "Claude Code", "w1"),
  pane("T2", "Codex", "w1", "exited"),
  pane("T1", "Claude Code", "w2"),
];

const CLAUDE = { name: "claude", displayName: "Claude Code", installed: true, kind: "cli" as const };
const CODEX = { name: "codex", displayName: "Codex", installed: true, kind: "cli" as const };

describe("the sidebar's chat face", () => {
  beforeEach(() => {
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
      terminalRequest: null,
      workspaceRequest: null,
      sessionRequest: null,
      addWorkspaceRequest: null,
      stagedPane: "T1",
      view: "chat",
      workspace: { id: "w1", name: "Personal Jarvis", path: HERE },
      workspaces: [
        { id: "w1", name: "Personal Jarvis", folder: HERE, active: true },
        { id: "w2", name: "Jarvis Web UI", folder: THERE, active: false },
      ],
      agents: [CLAUDE],
    });
  });

  afterEach(cleanup);

  it("draws every open workspace as a numbered band with its folder, the active one marked", () => {
    render(<WorkspaceChats />);

    const bands = screen.getAllByTestId("workspace-chats-band");
    expect(bands.map((b) => b.dataset.workspace)).toEqual(["w1", "w2"]);
    expect(bands[0].textContent).toContain("Workspace 1");
    expect(bands[1].textContent).toContain("Workspace 2");
    expect(bands[0].dataset.active).toBe("true");
    expect(bands[1].dataset.active).toBe("false");
    // The folder under the band, its full path within reach.
    const folders = screen.getAllByTestId("workspace-chats-folder");
    expect(folders[0].textContent).toContain("Personal Jarvis");
    expect(folders[0].getAttribute("title")).toBe(HERE);
    expect(folders[1].textContent).toContain("Jarvis Web UI");
    // One "Active" badge, on the tab at the front.
    expect(screen.getAllByTestId("workspace-chats-active")).toHaveLength(1);
  });

  it("lists each workspace's sessions under it, and marks the one on stage", () => {
    render(<WorkspaceChats />);

    const bands = screen.getAllByTestId("workspace-chats-band");
    const rowsOf = (band: HTMLElement) =>
      Array.from(band.querySelectorAll<HTMLElement>("[data-testid='workspace-session-row']"));
    expect(rowsOf(bands[0]).map((r) => r.dataset.pane)).toEqual(["T1", "T2"]);
    expect(rowsOf(bands[1]).map((r) => r.dataset.pane)).toEqual(["T1"]);
    // Every workspace numbers its panes from T1; only the ACTIVE workspace's
    // T1 is the pane on stage.
    expect(rowsOf(bands[0])[0].className).toContain("bg-card");
    expect(rowsOf(bands[1])[0].className).not.toContain("bg-card");
  });

  it("labels a session by what it is about, and names the CLI only when it was asked nothing", () => {
    useWorkspacePanesStore.setState({
      panes: [
        pane("T1", "Claude Code", "w1", "live", { recap: "Fixing the login test" }),
        pane("T2", "Claude Code", "w1", "live", {
          last_prompt: "Refactor the parser so it streams.\nStart with config.py.",
        }),
        pane("T3", "Claude Code", "w1"),
      ],
      activeId: "w1",
      loaded: true,
    });
    render(<WorkspaceChats />);

    const titles = screen.getAllByTestId("workspace-session-title").map((el) => el.textContent);
    expect(titles).toEqual([
      "Fixing the login test",
      "Refactor the parser so it streams.",
      "Claude Code",
    ]);
    // The CLI's name is still one hover away, with the pane it runs in.
    const row = screen.getAllByTestId("workspace-session-row")[0];
    expect(row.getAttribute("title")).toBe("Fixing the login test · Claude Code · T1");
  });

  it("asks the view to bring the clicked session to the front, workspace and all", () => {
    render(<WorkspaceChats />);
    const bands = screen.getAllByTestId("workspace-chats-band");
    const row = bands[1].querySelector<HTMLElement>("[data-testid='workspace-session-row']")!;
    fireEvent.click(row);
    expect(useIdeChatStore.getState().paneRequest).toMatchObject({ workspaceId: "w2", pane: "T1" });
  });

  it("brings a workspace to the front from its folder row", () => {
    render(<WorkspaceChats />);
    fireEvent.click(screen.getAllByTestId("workspace-chats-folder")[1]);
    expect(useIdeChatStore.getState().workspaceRequest).toMatchObject({ workspaceId: "w2" });
  });

  it("opens a terminal straight away when only one CLI is installed", () => {
    render(<WorkspaceChats />);
    fireEvent.click(screen.getByTestId("workspace-chats-new-terminal-w2"));
    expect(screen.queryByTestId("workspace-chats-agent-menu-w2")).toBeNull();
    expect(useIdeChatStore.getState().terminalRequest).toMatchObject({
      workspaceId: "w2",
      agent: undefined,
    });
  });

  it("asks which CLI first when the machine offers more than one", () => {
    useIdeChatStore.setState({ agents: [CLAUDE, CODEX] });
    render(<WorkspaceChats />);
    fireEvent.click(screen.getByTestId("workspace-chats-new-terminal-w1"));
    expect(useIdeChatStore.getState().terminalRequest).toBeNull();
    fireEvent.click(screen.getByTestId("workspace-chats-new-w1-codex"));
    expect(useIdeChatStore.getState().terminalRequest).toMatchObject({
      workspaceId: "w1",
      agent: "codex",
    });
  });

  /*
   * The menu must leave the column it was opened from.
   *
   * It used to hang INSIDE the list with a z-index of its own, which put the
   * picker's full-window dismiss layer on top of it: the entries were on
   * screen, every click on one hit that layer, and the menu closed without
   * opening anything. jsdom has no layout and so cannot see an overlap — what
   * it can check is the fact that rules it out, which is that the menu is
   * detached into a portal and owns its own stacking. The same detachment is
   * what keeps it from being clipped by the column's scroll box.
   */
  it("hangs the CLI menu outside the scrolling column", () => {
    useIdeChatStore.setState({ agents: [CLAUDE, CODEX] });
    render(<WorkspaceChats />);
    fireEvent.click(screen.getByTestId("workspace-chats-new-terminal-w1"));
    const menu = screen.getByTestId("workspace-chats-agent-menu-w1");
    expect(menu.dataset.detached).toBe("true");
    expect(screen.getByTestId("workspace-chats").contains(menu)).toBe(false);
  });

  it("opens the launcher for one more workspace, folder and all", () => {
    render(<WorkspaceChats />);
    fireEvent.click(screen.getByTestId("workspace-chats-new-workspace"));
    expect(useIdeChatStore.getState().addWorkspaceRequest).toMatchObject({ nonce: 1 });
  });

  it("offers a session with no folder, last", () => {
    render(<WorkspaceChats />);
    fireEvent.click(screen.getByTestId("workspace-chats-new-session"));
    expect(useIdeChatStore.getState().sessionRequest).toMatchObject({ nonce: 1 });
  });

  it("keeps no way out of its own, because it never took the column", () => {
    // The block used to REPLACE the sections and offer a "Sections" button
    // back — a swap that cost the sessions whenever a section was wanted
    // (maintainer report 2026-08-27). It leads the column now and the
    // sections follow underneath it, so there is nothing to go back from.
    render(<WorkspaceChats />);
    expect(screen.queryByTestId("workspace-chats-back")).toBeNull();
  });

  it("says so when nothing is open", () => {
    useIdeChatStore.setState({ workspaces: [] });
    render(<WorkspaceChats />);
    expect(screen.queryAllByTestId("workspace-chats-band")).toHaveLength(0);
    expect(screen.getByTestId("workspace-chats").textContent).toContain("No workspace is open");
  });

  it("tells a session still working from one that has finished, by shape", () => {
    // The row used to draw one amber dot for every live pane, pulsing for a
    // working one — the same six-pixel silhouette twelve times over. The
    // badge is now the grid's own pill: a spinner while the agent works, a
    // still dot once it stopped, a hollow ring for a pane never asked
    // anything, a beacon for one holding a question (maintainer, 2026-08-27).
    useWorkspacePanesStore.setState({
      panes: [
        pane("T1", "Claude Code", "w1", "live", { activity: "working", worked: true }),
        pane("T2", "Claude Code", "w1", "live", { activity: "waiting", worked: true }),
        pane("T3", "Claude Code", "w1", "live", { activity: "waiting", worked: false }),
        pane("T4", "Claude Code", "w1", "live", { activity: "asking", worked: true }),
        pane("T5", "Codex", "w1", "exited", { activity: "exited", worked: true }),
      ],
      activeId: "w1",
      loaded: true,
    });
    render(<WorkspaceChats />);

    const badges = screen.getAllByTestId("pane-activity");
    expect(badges.map((b) => b.getAttribute("data-icon"))).toEqual([
      "spinner",
      "dot",
      "ring",
      "beacon",
      "dot",
    ]);
    // The word is one hover away — "working", "done", "idle", "needs you".
    expect(badges.map((b) => b.getAttribute("aria-label")?.split(".")[0])).toEqual([
      "working",
      "done",
      "idle",
      "needs you",
      "exited",
    ]);
    // A finished job glows; a dead process does not.
    expect(badges[1].querySelector("[class*='shadow']")).not.toBeNull();
    expect(badges[4].querySelector("[class*='shadow']")).toBeNull();
  });
});
