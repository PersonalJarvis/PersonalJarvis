import { create } from "zustand";
import type { IdeProject } from "@/lib/agenticIdeApi";

type IdeAction =
  | { kind: "connect-project"; nonce: number }
  | { kind: "new-workspace"; projectId: string; nonce: number }
  | { kind: "activate-workspace"; workspaceId: string; nonce: number };

interface IdeProjectsStore {
  projects: IdeProject[];
  activeWorkspaceId: string | null;
  action: IdeAction | null;
  publish: (projects: IdeProject[], activeWorkspaceId: string | null) => void;
  connectProject: () => void;
  newWorkspace: (projectId: string) => void;
  activateWorkspace: (workspaceId: string) => void;
}

export const useIdeProjectsStore = create<IdeProjectsStore>((set) => ({
  projects: [],
  activeWorkspaceId: null,
  action: null,
  publish: (projects, activeWorkspaceId) => set({ projects, activeWorkspaceId }),
  connectProject: () => set((state) => ({ action: { kind: "connect-project", nonce: (state.action?.nonce ?? 0) + 1 } })),
  newWorkspace: (projectId) => set((state) => ({ action: { kind: "new-workspace", projectId, nonce: (state.action?.nonce ?? 0) + 1 } })),
  activateWorkspace: (workspaceId) => set((state) => ({ action: { kind: "activate-workspace", workspaceId, nonce: (state.action?.nonce ?? 0) + 1 } })),
}));
