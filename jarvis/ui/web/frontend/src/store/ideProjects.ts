import { create } from "zustand";
import type { IdeProject } from "@/lib/agenticIdeApi";

type IdeAction =
  | { kind: "connect-project"; nonce: number }
  // `worktree`: preset the dialog to give the new workspace a git worktree of its own.
  | { kind: "new-workspace"; projectId: string; worktree?: boolean; nonce: number }
  | { kind: "activate-workspace"; workspaceId: string; nonce: number }
  | { kind: "workspace-options"; workspaceId: string; nonce: number }
  | { kind: "git-panel"; workspaceId: string; nonce: number }
  | { kind: "toggle-voice"; nonce: number };

interface IdeProjectsStore {
  projects: IdeProject[];
  activeWorkspaceId: string | null;
  pendingWorkspaceId: string | null;
  refreshRequest: { nonce: number } | null;
  action: IdeAction | null;
  publish: (projects: IdeProject[], activeWorkspaceId: string | null) => void;
  setPendingWorkspaceId: (workspaceId: string | null) => void;
  requestRefresh: () => void;
  connectProject: () => void;
  newWorkspace: (projectId: string, options?: { worktree?: boolean }) => void;
  activateWorkspace: (workspaceId: string) => void;
  openWorkspaceOptions: (workspaceId: string) => void;
  openGitPanel: (workspaceId: string) => void;
  toggleVoice: () => void;
}

export const useIdeProjectsStore = create<IdeProjectsStore>((set) => ({
  projects: [],
  activeWorkspaceId: null,
  pendingWorkspaceId: null,
  refreshRequest: null,
  action: null,
  publish: (projects, activeWorkspaceId) => set({ projects, activeWorkspaceId }),
  setPendingWorkspaceId: (pendingWorkspaceId) => set({ pendingWorkspaceId }),
  requestRefresh: () => set((state) => ({ refreshRequest: { nonce: (state.refreshRequest?.nonce ?? 0) + 1 } })),
  connectProject: () => set((state) => ({ action: { kind: "connect-project", nonce: (state.action?.nonce ?? 0) + 1 } })),
  newWorkspace: (projectId, options) => set((state) => ({ action: { kind: "new-workspace", projectId, worktree: options?.worktree, nonce: (state.action?.nonce ?? 0) + 1 } })),
  activateWorkspace: (workspaceId) => set((state) => ({ action: { kind: "activate-workspace", workspaceId, nonce: (state.action?.nonce ?? 0) + 1 } })),
  openWorkspaceOptions: (workspaceId) => set((state) => ({ action: { kind: "workspace-options", workspaceId, nonce: (state.action?.nonce ?? 0) + 1 } })),
  openGitPanel: (workspaceId) => set((state) => ({ action: { kind: "git-panel", workspaceId, nonce: (state.action?.nonce ?? 0) + 1 } })),
  toggleVoice: () => set((state) => ({ action: { kind: "toggle-voice", nonce: (state.action?.nonce ?? 0) + 1 } })),
}));
