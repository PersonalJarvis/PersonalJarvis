import { create } from "zustand";
import type { RoutineChatTarget } from "./routineExecution";

/** Navigation only: opening the selected chat is the host's lazy responsibility. */
export const useRoutineNavigation = create<{
  target: RoutineChatTarget | null;
  open: (target: RoutineChatTarget) => void;
  close: () => void;
}>((set) => ({
  target: null,
  open: (target) => set({ target }),
  close: () => set({ target: null }),
}));
