import { create } from "zustand";

import { setReloadHold } from "@/lib/reloadHold";
import type { LineEnding, TextEncoding } from "@/components/agentic/editor/editorApi";

/**
 * The Agentic IDE's code editor: which files are open as tabs, and what is
 * known about each file on disk.
 *
 * The text itself lives in the editor engine's models (see
 * `components/agentic/editor/editorModels.ts`); this store holds only what the
 * React chrome draws — tabs, dirty dots, conflict bars, the status bar.
 *
 * Tabs follow the familiar desktop-editor rules: a single click opens a
 * *preview* tab (italic) that the next single click replaces; a double click,
 * an edit or a save keeps it. Tabs belong to one workspace, so switching
 * workspaces shows that workspace's own tabs.
 */
export type EditorMode = "edit" | "diff";

export interface EditorTab {
  key: string;
  fileKey: string;
  workspaceId: string;
  /** POSIX path relative to the workspace root. */
  path: string;
  mode: EditorMode;
  preview: boolean;
}

export type FileStatus = "loading" | "ready" | "binary" | "too_large" | "image" | "error";

export interface EditorFile {
  workspaceId: string;
  path: string;
  status: FileStatus;
  error: string;
  /** Version on disk the buffer is based on; null for a file not on disk. */
  version: string | null;
  encoding: TextEncoding;
  eol: LineEnding;
  dirty: boolean;
  /** Deleted on disk while open; the buffer is kept and a save recreates it. */
  deleted: boolean;
  /** The file changed on disk while the buffer had unsaved edits. */
  conflict: { diskVersion: string | null } | null;
  saving: boolean;
}

export interface CursorInfo {
  line: number;
  column: number;
  selected: number;
  language: string;
  insertSpaces: boolean;
  tabSize: number;
}

export interface RevealRequest {
  fileKey: string;
  line: number;
  column: number;
  nonce: number;
}

interface ClosedTab {
  workspaceId: string;
  path: string;
  mode: EditorMode;
}

export interface OpenOptions {
  mode?: EditorMode;
  /** Single-click opens replace each other; false keeps the tab. */
  preview?: boolean;
  line?: number;
  column?: number;
}

interface CodeEditorState {
  tabs: EditorTab[];
  /** The active tab per workspace. */
  active: Record<string, string | null>;
  files: Record<string, EditorFile>;
  /** Whether the editor covers the terminals (tabs stay open when hidden). */
  visible: boolean;
  quickOpen: boolean;
  cursor: CursorInfo | null;
  reveal: RevealRequest | null;
  closed: ClosedTab[];
  openFile: (workspaceId: string, path: string, options?: OpenOptions) => void;
  closeTab: (key: string) => void;
  closeTabs: (keys: string[]) => void;
  activate: (key: string) => void;
  pinTab: (key: string) => void;
  cycle: (workspaceId: string, step: 1 | -1) => void;
  reopenClosed: (workspaceId: string) => void;
  patchFile: (fileKey: string, patch: Partial<EditorFile>) => void;
  setVisible: (visible: boolean) => void;
  setQuickOpen: (open: boolean) => void;
  setCursor: (cursor: CursorInfo | null) => void;
  /** After a rename in the explorer: every open file under `from` moves to `to`. */
  renamePath: (workspaceId: string, from: string, to: string) => void;
  /** After a delete in the explorer: open files under `path` are marked deleted. */
  markDeleted: (workspaceId: string, path: string) => void;
}

export const fileKeyOf = (workspaceId: string, path: string) => `${workspaceId}\u0000${path}`;
export const tabKeyOf = (mode: EditorMode, fileKey: string) => `${mode}:${fileKey}`;

/** True when `path` is `root` itself or lies inside the folder `root`. */
export const isUnder = (path: string, root: string) => path === root || path.startsWith(`${root}/`);

const MAX_CLOSED = 20;

const newFile = (workspaceId: string, path: string): EditorFile => ({
  workspaceId,
  path,
  status: "loading",
  error: "",
  version: null,
  encoding: "utf-8",
  eol: "\n",
  dirty: false,
  deleted: false,
  conflict: null,
  saving: false,
});

/** Drop file records no tab refers to any more. */
function prune(files: Record<string, EditorFile>, tabs: EditorTab[]): Record<string, EditorFile> {
  const used = new Set(tabs.map((tab) => tab.fileKey));
  const next: Record<string, EditorFile> = {};
  for (const [key, file] of Object.entries(files)) if (used.has(key)) next[key] = file;
  return next;
}

/** The tab to show once `key` closes: the nearest survivor to its right, else to its left. */
function successor(own: EditorTab[], key: string, closing: Set<string>): string | null {
  const index = own.findIndex((tab) => tab.key === key);
  const right = own.slice(index + 1).find((tab) => !closing.has(tab.key));
  const left = own
    .slice(0, Math.max(index, 0))
    .reverse()
    .find((tab) => !closing.has(tab.key));
  return (right ?? left)?.key ?? null;
}

let revealNonce = 0;

export const useCodeEditorStore = create<CodeEditorState>((set, get) => ({
  tabs: [],
  active: {},
  files: {},
  visible: false,
  quickOpen: false,
  cursor: null,
  reveal: null,
  closed: [],

  openFile: (workspaceId, path, options = {}) => {
    const mode = options.mode ?? "edit";
    const preview = options.preview ?? true;
    const fileKey = fileKeyOf(workspaceId, path);
    const key = tabKeyOf(mode, fileKey);
    set((state) => {
      let tabs = state.tabs;
      const existing = tabs.find((tab) => tab.key === key);
      if (existing) {
        if (!preview && existing.preview) tabs = tabs.map((tab) => (tab.key === key ? { ...tab, preview: false } : tab));
      } else {
        const tab: EditorTab = { key, fileKey, workspaceId, path, mode, preview };
        const replaced = tabs.find(
          (entry) => entry.workspaceId === workspaceId && entry.preview && !state.files[entry.fileKey]?.dirty,
        );
        if (replaced) {
          tabs = tabs.map((entry) => (entry.key === replaced.key ? tab : entry));
        } else {
          const activeKey = state.active[workspaceId];
          const at = activeKey ? tabs.findIndex((entry) => entry.key === activeKey) : -1;
          tabs = at < 0 ? [...tabs, tab] : [...tabs.slice(0, at + 1), tab, ...tabs.slice(at + 1)];
        }
      }
      const files = state.files[fileKey] ? state.files : { ...state.files, [fileKey]: newFile(workspaceId, path) };
      const reveal =
        options.line != null ? { fileKey, line: options.line, column: options.column ?? 1, nonce: ++revealNonce } : state.reveal;
      return {
        tabs,
        files: prune(files, tabs),
        active: { ...state.active, [workspaceId]: key },
        visible: true,
        reveal,
      };
    });
  },

  closeTab: (key) => get().closeTabs([key]),

  closeTabs: (keys) =>
    set((state) => {
      const closing = new Set(keys);
      const gone = state.tabs.filter((tab) => closing.has(tab.key));
      if (gone.length === 0) return {};
      const tabs = state.tabs.filter((tab) => !closing.has(tab.key));
      const active = { ...state.active };
      for (const workspaceId of new Set(gone.map((tab) => tab.workspaceId))) {
        const current = active[workspaceId];
        if (!current || !closing.has(current)) continue;
        active[workspaceId] = successor(state.tabs.filter((tab) => tab.workspaceId === workspaceId), current, closing);
      }
      const closed = [...state.closed, ...gone.map(({ workspaceId, path, mode }) => ({ workspaceId, path, mode }))];
      return { tabs, active, files: prune(state.files, tabs), closed: closed.slice(-MAX_CLOSED) };
    }),

  activate: (key) =>
    set((state) => {
      const tab = state.tabs.find((entry) => entry.key === key);
      return tab ? { active: { ...state.active, [tab.workspaceId]: key }, visible: true } : {};
    }),

  pinTab: (key) =>
    set((state) => ({ tabs: state.tabs.map((tab) => (tab.key === key && tab.preview ? { ...tab, preview: false } : tab)) })),

  cycle: (workspaceId, step) =>
    set((state) => {
      const own = state.tabs.filter((tab) => tab.workspaceId === workspaceId);
      if (own.length === 0) return {};
      const index = own.findIndex((tab) => tab.key === state.active[workspaceId]);
      const next = own[(index + step + own.length) % own.length];
      return { active: { ...state.active, [workspaceId]: next.key } };
    }),

  reopenClosed: (workspaceId) => {
    const closed = [...get().closed];
    for (let index = closed.length - 1; index >= 0; index -= 1) {
      const entry = closed[index];
      if (entry.workspaceId !== workspaceId) continue;
      closed.splice(index, 1);
      set({ closed });
      get().openFile(entry.workspaceId, entry.path, { mode: entry.mode, preview: false });
      return;
    }
  },

  patchFile: (fileKey, patch) =>
    set((state) => {
      const current = state.files[fileKey];
      if (!current) return {};
      const file = { ...current, ...patch };
      // An edit keeps a preview tab, the way typing in it would.
      const tabs =
        patch.dirty && !current.dirty
          ? state.tabs.map((tab) => (tab.fileKey === fileKey && tab.preview ? { ...tab, preview: false } : tab))
          : state.tabs;
      return { files: { ...state.files, [fileKey]: file }, tabs };
    }),

  setVisible: (visible) => set({ visible }),
  setQuickOpen: (quickOpen) => set({ quickOpen }),
  setCursor: (cursor) => set({ cursor }),

  renamePath: (workspaceId, from, to) =>
    set((state) => {
      const moved = (path: string) => (isUnder(path, from) ? to + path.slice(from.length) : path);
      const tabs = state.tabs.map((tab) => {
        if (tab.workspaceId !== workspaceId || !isUnder(tab.path, from)) return tab;
        const path = moved(tab.path);
        const fileKey = fileKeyOf(workspaceId, path);
        return { ...tab, path, fileKey, key: tabKeyOf(tab.mode, fileKey) };
      });
      const files: Record<string, EditorFile> = {};
      for (const [key, file] of Object.entries(state.files)) {
        if (file.workspaceId !== workspaceId || !isUnder(file.path, from)) files[key] = file;
        else {
          const path = moved(file.path);
          files[fileKeyOf(workspaceId, path)] = { ...file, path };
        }
      }
      const activeKey = state.active[workspaceId];
      const activeTab = activeKey ? state.tabs.findIndex((tab) => tab.key === activeKey) : -1;
      return {
        tabs,
        files,
        active: { ...state.active, [workspaceId]: activeTab >= 0 ? tabs[activeTab].key : (activeKey ?? null) },
      };
    }),

  markDeleted: (workspaceId, path) =>
    set((state) => {
      const files = { ...state.files };
      for (const [key, file] of Object.entries(files)) {
        if (file.workspaceId === workspaceId && isUnder(file.path, path)) files[key] = { ...file, deleted: true, version: null };
      }
      return { files };
    }),
}));

/** The tabs of one workspace, in order. */
export function tabsOf(state: Pick<CodeEditorState, "tabs">, workspaceId: string | null): EditorTab[] {
  return workspaceId ? state.tabs.filter((tab) => tab.workspaceId === workspaceId) : [];
}

// An unsaved buffer holds off the automatic reload a new frontend build
// triggers (lib/bundleWatch), so a rebuild never throws away typed code.
useCodeEditorStore.subscribe((state) => {
  setReloadHold(
    "code-editor",
    Object.values(state.files).some((file) => file.dirty),
  );
});
