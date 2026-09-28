import { useEffect } from "react";
import { create } from "zustand";

import { OPEN_PATH_EVENT } from "@/lib/terminalLinks";
import { useIdeChatStore } from "@/store/ideChat";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";

/**
 * The Changes and Folder tabs' state: which file each of them has open.
 *
 * Kept outside the component because a file can be opened from elsewhere: a
 * Ctrl+click on a path a coding agent printed in its terminal lands here (see
 * `OPEN_PATH_EVENT`), opens the side panel on the Folder tab, and shows that
 * file's diff. Each tab keeps its own open file, so opening one in Changes
 * does not cover the tree in Folder.
 */
export type ExplorerView = "files" | "changes";

export interface OpenedFile {
  workspaceId: string;
  /** Workspace-relative, or an absolute path inside the workspace as printed. */
  path: string;
}

interface IdeExplorerState {
  opened: Record<ExplorerView, OpenedFile | null>;
  open: (view: ExplorerView, file: OpenedFile) => void;
  close: (view: ExplorerView) => void;
}

export const useIdeExplorerStore = create<IdeExplorerState>((set) => ({
  opened: { files: null, changes: null },
  open: (view, file) => set((state) => ({ opened: { ...state.opened, [view]: file } })),
  close: (view) => set((state) => ({ opened: { ...state.opened, [view]: null } })),
}));

export interface OpenPathDetail {
  workspaceId: string;
  path: string;
}

/** Open a file in the side panel's Folder tab, bringing the panel up if needed. */
export function openInExplorer(file: OpenedFile): void {
  useIdeExplorerStore.getState().open("files", file);
  useIdeSidePanelStore.getState().openTab("files");
}

/**
 * Route terminal path clicks into the Folder tab while the Agentic IDE is mounted.
 *
 * Only for the workspace on screen: a path from another workspace's pane has
 * no tree here to show it in, so it keeps the old behaviour.
 */
export function useExplorerPathRouting(): void {
  useEffect(() => {
    const onOpen = (event: Event) => {
      const detail = (event as CustomEvent<OpenPathDetail>).detail;
      if (!detail?.workspaceId || !detail.path) return;
      if (useIdeChatStore.getState().workspace?.id !== detail.workspaceId) return;
      event.preventDefault();
      openInExplorer({ workspaceId: detail.workspaceId, path: detail.path });
    };
    window.addEventListener(OPEN_PATH_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_PATH_EVENT, onOpen);
  }, []);
}
