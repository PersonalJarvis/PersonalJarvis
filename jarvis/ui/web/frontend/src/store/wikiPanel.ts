import { create } from "zustand";

/**
 * Whether the Wiki section's right-hand inspector is open.
 *
 * The toggle lives in the window caption (like the Agentic IDE's side panel)
 * and the section reads it, so the answer is one small store. It survives a
 * reload through localStorage, which may be unavailable (private window,
 * blocked storage) — then the panel simply starts open.
 */
const OPEN_KEY = "jarvis.wiki.inspector.open.v1";

function readOpen(): boolean {
  try {
    return localStorage.getItem(OPEN_KEY) !== "0";
  } catch {
    return true;
  }
}

interface WikiPanelState {
  open: boolean;
  setOpen: (open: boolean) => void;
}

export const useWikiPanelStore = create<WikiPanelState>((set) => ({
  open: readOpen(),
  setOpen: (open) => {
    try {
      localStorage.setItem(OPEN_KEY, open ? "1" : "0");
    } catch {
      // Storage unavailable: the choice holds for this session only.
    }
    set({ open });
  },
}));

/** DOM id of the inspector, for the caption toggle's aria-controls. */
export const WIKI_INSPECTOR_ID = "wiki-inspector";
