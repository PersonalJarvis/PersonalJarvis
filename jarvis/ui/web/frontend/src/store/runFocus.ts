import { create } from "zustand";

/**
 * A one-shot hand-off: "open the Run Inspector ON this run".
 *
 * The sidebar's recent-runs block lives outside the inspector, whose
 * selection is local state. Setting the focus here and switching sections
 * lets the inspector pick that run up on mount and clear the hand-off, so a
 * later visit starts on the newest run as before.
 */
interface RunFocusStore {
  sessionId: string | null;
  focus: (sessionId: string) => void;
  /** Take the pending hand-off (and clear it). Null when nothing is pending. */
  take: () => string | null;
}

export const useRunFocusStore = create<RunFocusStore>((set, get) => ({
  sessionId: null,
  focus: (sessionId) => set({ sessionId }),
  take: () => {
    const id = get().sessionId;
    if (id !== null) set({ sessionId: null });
    return id;
  },
}));
