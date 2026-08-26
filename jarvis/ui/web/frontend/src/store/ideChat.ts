import { create } from "zustand";

import {
  rememberViewMode,
  storedViewMode,
  type WorkspaceView,
} from "@/components/agentic/workspaceView";

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
 * The sidebar's two faces while chat mode is on.
 *
 * Chat mode takes the sidebar over for the workspace's conversations, and a
 * takeover with no way out is a trap — so "sections" is the face the back
 * button returns to, and the sections are all still there.
 */
export type IdeSidebarFace = "chats" | "sections";

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

interface IdeChatStore {
  view: WorkspaceView;
  workspace: IdeWorkspace | null;
  sidebarFace: IdeSidebarFace;
  paneRequest: PaneRequest | null;
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
  setSidebarFace: (next: IdeSidebarFace) => void;
  /** Bring a pane to the front, switching workspace first when it lives elsewhere. */
  requestPane: (workspaceId: string, pane: string) => void;
  setStagedPane: (pane: string | null) => void;
}

export const useIdeChatStore = create<IdeChatStore>((set) => ({
  view: storedViewMode() ?? "grid",
  workspace: null,
  sidebarFace: "chats",
  paneRequest: null,
  stagedPane: null,

  setView: (next) => {
    rememberViewMode(next);
    // Entering chat always shows the chats: someone who just pressed "Chat"
    // asked for the conversations, and leaving the sidebar on the section
    // list they were reading a minute ago would hide what they came for.
    set(next === "chat" ? { view: next, sidebarFace: "chats" } : { view: next });
  },

  setWorkspace: (next) => set({ workspace: next }),
  setSidebarFace: (next) => set({ sidebarFace: next }),
  requestPane: (workspaceId, pane) =>
    set((state) => ({
      paneRequest: { workspaceId, pane, nonce: (state.paneRequest?.nonce ?? 0) + 1 },
    })),
  setStagedPane: (pane) =>
    // Guarded: the grid publishes this from an effect that runs on every poll,
    // and an unconditional set would wake every subscriber each time.
    set((state) => (state.stagedPane === pane ? state : { stagedPane: pane })),
}));
