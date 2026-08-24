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

interface IdeChatStore {
  view: WorkspaceView;
  workspace: IdeWorkspace | null;
  sidebarFace: IdeSidebarFace;

  setView: (next: WorkspaceView) => void;
  setWorkspace: (next: IdeWorkspace | null) => void;
  setSidebarFace: (next: IdeSidebarFace) => void;
}

export const useIdeChatStore = create<IdeChatStore>((set) => ({
  view: storedViewMode() ?? "grid",
  workspace: null,
  sidebarFace: "chats",

  setView: (next) => {
    rememberViewMode(next);
    // Entering chat always shows the chats: someone who just pressed "Chat"
    // asked for the conversations, and leaving the sidebar on the section
    // list they were reading a minute ago would hide what they came for.
    set(next === "chat" ? { view: next, sidebarFace: "chats" } : { view: next });
  },

  setWorkspace: (next) => set({ workspace: next }),
  setSidebarFace: (next) => set({ sidebarFace: next }),
}));
