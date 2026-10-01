/**
 * Whether the quick switcher is open, and what it opens with.
 *
 * Two doors lead into it: the chord (App.tsx) and the search bar at the top of
 * the sidebar. The bar is typed into like a field — the first character opens
 * the switcher with that character already in it, so nothing typed is lost —
 * which is why the open state carries an initial query instead of being a bare
 * flag inside App.
 */
import { create } from "zustand";

interface QuickSwitcherState {
  open: boolean;
  /** Seeds the switcher's field when it opens; read once, on mount. */
  initialQuery: string;
  show: (initialQuery?: string) => void;
  hide: () => void;
  toggle: () => void;
}

export const useQuickSwitcher = create<QuickSwitcherState>((set, get) => ({
  open: false,
  initialQuery: "",
  show: (initialQuery = "") => set({ open: true, initialQuery }),
  hide: () => set({ open: false, initialQuery: "" }),
  toggle: () => (get().open ? get().hide() : get().show()),
}));
