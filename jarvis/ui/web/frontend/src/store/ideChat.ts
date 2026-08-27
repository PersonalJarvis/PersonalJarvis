import { create } from "zustand";

import {
  rememberViewMode,
  storedViewMode,
  type WorkspaceView,
} from "@/components/agentic/workspaceView";
import type { SplitAgentChoice } from "@/components/agentic/AgentPicker";

/**
 * Which face the Agentic IDE is wearing, for everyone who has to know.
 *
 * Three parts of the app read this and none of them can be reached from the
 * others by props: the IDE view itself (grid or chat surface), the workspace
 * bar's switch (inside the grid's header row), and the APP SIDEBAR — which is
 * mounted next to the whole page in `App.tsx` and swaps its section list for
 * the workspace's chats while chat mode is on. A store is the seam that
 * carries the answer across all three without threading it through the layout.
 *
 * `workspace` is the open one, republished by the IDE view on every switch.
 * The chat surface uses its PATH as the working directory of any chat started
 * there, which is what "you are in the workspace you opened" means in
 * practice: the agent runs in that folder, on that folder's files.
 */
export interface IdeWorkspace {
  id: string;
  name: string;
  /** Absolute path of the project folder; "" for a workspace with no folder. */
  path: string;
}

/**
 * A session list asking for one pane to be brought to the front.
 *
 * The list lives in the app sidebar, the panes live inside the IDE view, and
 * neither can reach the other by props — so the ask travels through here and
 * the view performs it. The `nonce` is what makes asking for the SAME pane
 * twice work: without it a second click on the pane already staged changes
 * nothing observable, and the click reads as broken.
 */
export interface PaneRequest {
  workspaceId: string;
  pane: string;
  nonce: number;
}

/**
 * One open workspace, as the sidebar's session list draws it: a numbered
 * band, its folder, and whether it is the tab at the front. Published by the
 * IDE view from the same list the workspace bar draws, so the two agree.
 */
export interface IdeWorkspaceRow {
  id: string;
  name: string;
  /** Absolute project folder; "" for a workspace with no folder. */
  folder: string;
  active: boolean;
}

/**
 * The sidebar asking for a new terminal in one workspace.
 *
 * `agent` is the CLI picked from the menu, or undefined when the machine
 * offers no choice; the IDE view opens it — switching workspace first when
 * the ask names another tab — because the grid's own "open a pane" lives
 * inside the view and the sidebar cannot reach it by props.
 */
export interface TerminalRequest {
  workspaceId: string;
  agent?: string;
  nonce: number;
}

interface IdeChatStore {
  view: WorkspaceView;
  workspace: IdeWorkspace | null;
  paneRequest: PaneRequest | null;
  /** Every open workspace, in the bar's order — the sidebar's bands. */
  workspaces: IdeWorkspaceRow[];
  /** The CLIs a new terminal can run, as the grid's split menus offer them. */
  agents: SplitAgentChoice[];
  terminalRequest: TerminalRequest | null;
  /** A workspace the sidebar asked to bring to the front (its folder row). */
  workspaceRequest: { workspaceId: string; nonce: number } | null;
  /** The sidebar asking for a workspace with no project folder (a scratch session). */
  sessionRequest: { nonce: number } | null;
  /**
   * The sidebar asking for a NEW CHAT in one workspace — an empty chat window
   * rather than a spawned pane.
   *
   * The distinction chat mode is built on (maintainer report, 2026-08-27): a
   * new conversation starts with nothing running, and the coding agent, its
   * model, its effort and its permission stance are chosen in the composer
   * before the first message opens the pane on them. `terminalRequest` stays
   * what it always was — "open a pane of this CLI now" — and is what the grid
   * mode's own menus keep using.
   */
  newChatRequest: { workspaceId: string; nonce: number } | null;
  /**
   * The sidebar asking to open ANOTHER workspace — folder and all.
   *
   * The session list is the whole navigation while chat mode is on, and it
   * offered exactly two ways forward: another terminal inside a workspace
   * that already exists, or a folderless scratch session. "Open a second
   * project" was reachable only from the workspace bar above the grid, which
   * is the surface chat mode replaces — so from where the user sits a new
   * workspace could not be started at all (maintainer report 2026-08-27).
   * The view answers this with the same launcher its own "+" opens.
   */
  addWorkspaceRequest: { nonce: number } | null;
  /**
   * The pane the chat view currently has on its stage, or null in grid view.
   *
   * Published by the grid so a session list somewhere else can mark the row
   * the user is reading. A list that cannot say which of eleven sessions is
   * open is a list you have to click through to find out.
   */
  stagedPane: string | null;

  setView: (next: WorkspaceView) => void;
  setWorkspace: (next: IdeWorkspace | null) => void;
  /** Bring a pane to the front, switching workspace first when it lives elsewhere. */
  requestPane: (workspaceId: string, pane: string) => void;
  setStagedPane: (pane: string | null) => void;
  setWorkspaces: (rows: IdeWorkspaceRow[]) => void;
  setAgents: (agents: SplitAgentChoice[]) => void;
  /** Open a new terminal in `workspaceId`, running `agent` when one was picked. */
  requestTerminal: (workspaceId: string, agent?: string) => void;
  /** Open an empty chat in `workspaceId`; its first message starts the pane. */
  requestNewChat: (workspaceId: string) => void;
  /** Bring `workspaceId`'s tab to the front. */
  requestWorkspace: (workspaceId: string) => void;
  requestSession: () => void;
  /** Open the launcher for one more workspace — the bar's "+", from the sidebar. */
  requestAddWorkspace: () => void;
}

export const useIdeChatStore = create<IdeChatStore>((set) => ({
  view: storedViewMode() ?? "grid",
  workspace: null,
  paneRequest: null,
  stagedPane: null,
  workspaces: [],
  agents: [],
  terminalRequest: null,
  newChatRequest: null,
  workspaceRequest: null,
  sessionRequest: null,
  addWorkspaceRequest: null,

  setView: (next) => {
    rememberViewMode(next);
    set({ view: next });
  },

  setWorkspace: (next) => set({ workspace: next }),
  requestPane: (workspaceId, pane) =>
    set((state) => ({
      paneRequest: { workspaceId, pane, nonce: (state.paneRequest?.nonce ?? 0) + 1 },
    })),
  setStagedPane: (pane) =>
    // Guarded: the grid publishes this from an effect that runs on every poll,
    // and an unconditional set would wake every subscriber each time.
    set((state) => (state.stagedPane === pane ? state : { stagedPane: pane })),
  setWorkspaces: (rows) =>
    set((state) => (sameRows(state.workspaces, rows) ? state : { workspaces: rows })),
  setAgents: (agents) => set({ agents }),
  requestTerminal: (workspaceId, agent) =>
    set((state) => ({
      terminalRequest: {
        workspaceId,
        agent,
        nonce: (state.terminalRequest?.nonce ?? 0) + 1,
      },
    })),
  requestNewChat: (workspaceId) =>
    set((state) => ({
      newChatRequest: { workspaceId, nonce: (state.newChatRequest?.nonce ?? 0) + 1 },
    })),
  requestWorkspace: (workspaceId) =>
    set((state) => ({
      workspaceRequest: { workspaceId, nonce: (state.workspaceRequest?.nonce ?? 0) + 1 },
    })),
  requestSession: () =>
    set((state) => ({ sessionRequest: { nonce: (state.sessionRequest?.nonce ?? 0) + 1 } })),
  requestAddWorkspace: () =>
    set((state) => ({
      addWorkspaceRequest: { nonce: (state.addWorkspaceRequest?.nonce ?? 0) + 1 },
    })),
}));

/** The IDE view republishes on every render of its list; equal rows are no change. */
function sameRows(left: IdeWorkspaceRow[], right: IdeWorkspaceRow[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((row, i) => {
    const other = right[i];
    return (
      row.id === other.id &&
      row.name === other.name &&
      row.folder === other.folder &&
      row.active === other.active
    );
  });
}
