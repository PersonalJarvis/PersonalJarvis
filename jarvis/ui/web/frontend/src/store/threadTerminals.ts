import { create } from "zustand";

/**
 * The thread layout's terminal drawer: plain shells under the conversation,
 * in the open thread's folder.
 *
 * The caption's terminal toggle and the drawer itself both need the answer,
 * and the caption cannot reach the thread view by props, so it lives here.
 * Shells are kept per folder: threads that share a checkout share its shells,
 * and a dev server keeps running while the person moves between threads.
 *
 * Shells belong to this window and are never persisted — a reload must not
 * start replacements for commands that were running before it. Only the
 * drawer's height survives, through localStorage, which may be blocked.
 */

export interface ThreadShell {
  id: string;
  folder: string;
  /** Shown as "Terminal N"; counted per folder. */
  number: number;
}

const HEIGHT_KEY = "jarvis.ide.threadDrawerHeight.v1";
export const DEFAULT_DRAWER_HEIGHT = 280;
export const MIN_DRAWER_HEIGHT = 140;
/** Most shells one folder holds; a tab strip past this is unreadable. */
export const MAX_FOLDER_SHELLS = 8;

function storedHeight(): number {
  try {
    const value = Number(localStorage.getItem(HEIGHT_KEY));
    return Number.isFinite(value) && value >= MIN_DRAWER_HEIGHT ? value : DEFAULT_DRAWER_HEIGHT;
  } catch {
    return DEFAULT_DRAWER_HEIGHT;
  }
}

interface ThreadTerminalsState {
  /** The open thread's folder, published by the thread view; "" without one. */
  folder: string;
  /** The drawer is pulled up. It only shows when the folder has a shell. */
  open: boolean;
  shells: ThreadShell[];
  /** The shell in front, per folder. */
  active: Record<string, string>;
  height: number;
  setFolder: (folder: string) => void;
  /**
   * The caption toggle: hide a shown drawer, or show it — starting the
   * folder's first shell when it has none yet.
   */
  toggle: () => void;
  /** Open one more shell in the current folder and bring it forward. */
  addShell: () => void;
  /** End a shell; the drawer goes down with the folder's last one. */
  closeShell: (id: string) => void;
  select: (id: string) => void;
  setHeight: (height: number) => void;
}

/** The current folder's shells, in the order they were opened. */
export const shellsIn = (shells: readonly ThreadShell[], folder: string): ThreadShell[] =>
  folder ? shells.filter((shell) => shell.folder === folder) : [];

/** Whether the drawer is on screen: pulled up, and the folder has a shell to show. */
export const drawerShown = (state: Pick<ThreadTerminalsState, "open" | "shells" | "folder">): boolean =>
  state.open && shellsIn(state.shells, state.folder).length > 0;

export const useThreadTerminalsStore = create<ThreadTerminalsState>((set, get) => ({
  folder: "",
  open: false,
  shells: [],
  active: {},
  height: storedHeight(),
  setFolder: (folder) => {
    if (get().folder !== folder) set({ folder });
  },
  toggle: () => {
    const state = get();
    if (drawerShown(state)) {
      set({ open: false });
      return;
    }
    if (!state.folder) return;
    if (shellsIn(state.shells, state.folder).length === 0) get().addShell();
    else set({ open: true });
  },
  addShell: () => {
    const { folder, shells, active } = get();
    if (!folder) return;
    const here = shellsIn(shells, folder);
    if (here.length >= MAX_FOLDER_SHELLS) {
      set({ open: true });
      return;
    }
    const id = `thread-shell-${crypto.randomUUID()}`;
    const number = Math.max(0, ...here.map((shell) => shell.number)) + 1;
    set({ open: true, shells: [...shells, { id, folder, number }], active: { ...active, [folder]: id } });
  },
  closeShell: (id) => {
    const { shells, active, folder, open } = get();
    const closing = shells.find((shell) => shell.id === id);
    if (!closing) return;
    const siblings = shellsIn(shells, closing.folder);
    const rest = shells.filter((shell) => shell.id !== id);
    const left = siblings.filter((shell) => shell.id !== id);
    const nextActive = { ...active };
    if (left.length === 0) delete nextActive[closing.folder];
    else if (active[closing.folder] === id) {
      const index = siblings.findIndex((shell) => shell.id === id);
      nextActive[closing.folder] = left[Math.max(0, index - 1)].id;
    }
    set({
      shells: rest,
      active: nextActive,
      open: open && !(closing.folder === folder && left.length === 0),
    });
  },
  select: (id) => {
    const shell = get().shells.find((entry) => entry.id === id);
    if (shell) set({ active: { ...get().active, [shell.folder]: id } });
  },
  setHeight: (height) => {
    const next = Math.max(MIN_DRAWER_HEIGHT, Math.round(height));
    set({ height: next });
    try {
      localStorage.setItem(HEIGHT_KEY, String(next));
    } catch {
      /* a convenience only: the height just does not survive this session */
    }
  },
}));
