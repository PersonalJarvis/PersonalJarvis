import { create } from "zustand";

/**
 * The Agentic IDE's right-hand side panel: open or shut, which tabs it holds,
 * and which one is in front.
 *
 * Two places need the answer and neither can reach the other by props: the
 * toggle lives in the window caption (`TopBar`), the panel itself inside the
 * IDE view. The state survives a reload through localStorage, which may be
 * blocked — every read and write degrades to the defaults instead of throwing.
 */

/** Every function the panel can show. A new one is a new id plus a registry entry. */
export type SidePanelTabId = "agents";

export const SIDE_PANEL_TAB_IDS: readonly SidePanelTabId[] = ["agents"];

const OPEN_KEY = "jarvis.agenticIde.sidePanelOpen";
const TABS_KEY = "jarvis.agenticIde.sidePanelTabs.v1";

const DEFAULT_TABS: SidePanelTabId[] = ["agents"];

const isTabId = (value: unknown): value is SidePanelTabId =>
  typeof value === "string" && (SIDE_PANEL_TAB_IDS as readonly string[]).includes(value);

function storedOpen(): boolean {
  try {
    return localStorage.getItem(OPEN_KEY) === "1";
  } catch {
    return false;
  }
}

function storedTabs(): { tabs: SidePanelTabId[]; active: SidePanelTabId } {
  try {
    const raw = JSON.parse(localStorage.getItem(TABS_KEY) ?? "null") as {
      tabs?: unknown;
      active?: unknown;
    } | null;
    const tabs = Array.isArray(raw?.tabs) ? [...new Set(raw.tabs.filter(isTabId))] : [];
    if (tabs.length === 0) return { tabs: DEFAULT_TABS, active: DEFAULT_TABS[0] };
    return { tabs, active: isTabId(raw?.active) && tabs.includes(raw.active) ? raw.active : tabs[0] };
  } catch {
    return { tabs: DEFAULT_TABS, active: DEFAULT_TABS[0] };
  }
}

function persist(open: boolean, tabs: SidePanelTabId[], active: SidePanelTabId): void {
  try {
    localStorage.setItem(OPEN_KEY, open ? "1" : "0");
    localStorage.setItem(TABS_KEY, JSON.stringify({ tabs, active }));
  } catch {
    /* a convenience only: the panel still works for this session */
  }
}

interface IdeSidePanelState {
  open: boolean;
  tabs: SidePanelTabId[];
  active: SidePanelTabId;
  setOpen: (open: boolean) => void;
  toggle: () => void;
  /** Open a tab (or bring it forward when it is already open). */
  openTab: (id: SidePanelTabId) => void;
  select: (id: SidePanelTabId) => void;
  /** Closing the last tab collapses the panel; reopening starts fresh. */
  closeTab: (id: SidePanelTabId) => void;
}

const initialTabs = storedTabs();

export const useIdeSidePanelStore = create<IdeSidePanelState>((set, get) => {
  const commit = (next: Partial<Pick<IdeSidePanelState, "open" | "tabs" | "active">>) => {
    const merged = { open: get().open, tabs: get().tabs, active: get().active, ...next };
    set(merged);
    persist(merged.open, merged.tabs, merged.active);
  };
  return {
    open: storedOpen(),
    tabs: initialTabs.tabs,
    active: initialTabs.active,
    setOpen: (open) => commit({ open }),
    toggle: () => get().setOpen(!get().open),
    openTab: (id) => {
      const { tabs } = get();
      commit({ open: true, tabs: tabs.includes(id) ? tabs : [...tabs, id], active: id });
    },
    select: (id) => {
      if (get().tabs.includes(id)) commit({ active: id });
    },
    closeTab: (id) => {
      const { tabs, active } = get();
      const index = tabs.indexOf(id);
      if (index < 0) return;
      const rest = tabs.filter((tab) => tab !== id);
      if (rest.length === 0) {
        commit({ open: false, tabs: DEFAULT_TABS, active: DEFAULT_TABS[0] });
        return;
      }
      commit({ tabs: rest, active: active === id ? rest[Math.max(0, index - 1)] : active });
    },
  };
});
