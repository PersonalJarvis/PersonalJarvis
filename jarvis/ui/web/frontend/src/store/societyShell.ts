import { create } from "zustand";

/** Section-local navigation starts hidden on every visit. */
export const useSocietyShell = create<{
  navigationOpen: boolean;
  toggleNavigation: () => void;
  reset: () => void;
}>((set) => ({
  navigationOpen: false,
  toggleNavigation: () => set((s) => ({ navigationOpen: !s.navigationOpen })),
  reset: () => set({ navigationOpen: false }),
}));
