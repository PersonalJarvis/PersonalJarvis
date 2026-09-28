import { useEffect } from "react";
import { create } from "zustand";

import { OPEN_PATH_EVENT } from "@/lib/terminalLinks";
import { useIdeChatStore } from "@/store/ideChat";
import { useIdeSidePanelStore } from "@/store/ideSidePanel";

/**
 * The Explorer tab's state: which list it shows and which file is open.
 *
 * Kept outside the component because a file can be opened from elsewhere: a
 * Ctrl+click on a path a coding agent printed in its terminal lands here (see
 * `OPEN_PATH_EVENT`), opens the side panel on the Explorer, and shows that
 * file's diff.
 */
export type ExplorerView = "files" | "changes";

export interface OpenedFile {
  workspaceId: string;
  /** Workspace-relative, or an absolute path inside the workspace as printed. */
  path: string;
}

interface IdeExplorerState {
  view: ExplorerView;
  opened: OpenedFile | null;
  setView: (view: ExplorerView) => void;
  open: (file: OpenedFile) => void;
  close: () => void;
}

export const useIdeExplorerStore = create<IdeExplorerState>((set) => ({
  view: "changes",
  opened: null,
  setView: (view) => set({ view }),
  open: (file) => set({ opened: file }),
  close: () => set({ opened: null }),
}));

export interface OpenPathDetail {
  workspaceId: string;
  path: string;
}

/** Open a file in the side panel's Explorer, bringing the panel up if needed. */
export function openInExplorer(file: OpenedFile): void {
  useIdeExplorerStore.getState().open(file);
  useIdeSidePanelStore.getState().openTab("files");
}

/**
 * Route terminal path clicks into the Explorer while the Agentic IDE is mounted.
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
